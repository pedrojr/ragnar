"""Indexer: the only process that writes to the database, and only to the primary.

Reads files from the commit of the branch, never from the working tree: nothing
uncommitted enters the index. Runs the initial build or the increment since the last
indexed commit. Writes in a single transaction, so a search never sees a half-built index.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from .chunker import Chunk, Chunker, Symbol, normalize_source, split_identifiers
from .config import CONFIG_FILE, RagnarError, Settings, index_fingerprint, load_settings, with_repo_config
from .embedder import Embedder, FakeEmbedder, to_pgvector
from .llm import chat_from, contextualize

COLUMNS = (
    "repo", "path", "symbol", "language", "kind", "start_line", "end_line",
    "commit_sha", "content", "search_text", "embedding", "content_hash", "head_text",
)
SYMBOL_COLUMNS = ("repo", "path", "symbol", "name", "node_type", "kind", "start_line", "end_line", "signature", "commit_sha")
CACHE_DIR = Path.home() / ".cache" / "ragnar" / "repos"
REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]*$")
URL_CREDENTIALS = re.compile(r"(?<=://)[^/@\s]+@")


def scrub(text: str) -> str:
    """Removes credentials embedded in URLs from a message."""
    return URL_CREDENTIALS.sub("***@", text)


def git(repo_dir: Path, *args: str, input: bytes | None = None, env: dict[str, str] | None = None) -> bytes:
    r = subprocess.run(["git", "-C", str(repo_dir), *args], input=input, capture_output=True,
                       env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})})
    if r.returncode != 0:
        raise RagnarError(f"git {' '.join(args)}: {scrub(r.stderr.decode(errors='replace').strip())}")
    return r.stdout


def resolve_source(source: str, name: str, env: dict[str, str] | None = None) -> Path:
    """Local path of a git repository. A URL becomes a cached mirror, updated on every run."""
    local = Path(source)
    if local.exists():
        return local
    mirror = CACHE_DIR / f"{name}.git"
    if mirror.exists():
        git(mirror, "remote", "update", "--prune", env=env)
    else:
        mirror.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["git", "clone", "--mirror", source, str(mirror)], capture_output=True,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})})
        if r.returncode != 0:
            raise RagnarError(f"git clone {scrub(source)}: {scrub(r.stderr.decode(errors='replace').strip())}")
    return mirror


def list_files(repo_dir: Path, sha: str) -> list[str]:
    return [p for p in git(repo_dir, "ls-tree", "-r", "-z", "--name-only", sha).decode(errors="replace").split("\0") if p]


def changed_files(repo_dir: Path, old: str, new: str) -> list[str] | None:
    """Files changed between the commits; None if the old commit is gone (force push)."""
    try:
        git(repo_dir, "cat-file", "-e", f"{old}^{{commit}}")
    except RagnarError:
        return None
    out = git(repo_dir, "diff", "--name-only", "-z", "--no-renames", old, new).decode(errors="replace")
    return [p for p in out.split("\0") if p]


def read_blobs(repo_dir: Path, sha: str, paths: list[str]) -> dict[str, bytes]:
    """File contents at the commit, with a single `git cat-file --batch` call."""
    if not paths:
        return {}
    data = git(repo_dir, "cat-file", "--batch", input="".join(f"{sha}:{p}\n" for p in paths).encode())
    out, pos = {}, 0
    for path in paths:
        eol = data.index(b"\n", pos)
        header = data[pos:eol].split()
        pos = eol + 1
        if header[-1] == b"missing":
            continue
        size = int(header[2])
        if header[1] == b"blob":
            out[path] = data[pos : pos + size]
        pos += size + 1
    return out


def secret_scan(files: dict[str, bytes]) -> dict[str, list[str]]:
    """Runs gitleaks on the files about to be indexed. Returns the findings per file."""
    if not files:
        return {}
    with tempfile.TemporaryDirectory(prefix="ragnar-scan-") as tmp:
        root = Path(tmp) / "src"
        for path, content in files.items():
            f = root / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(content)
        report = Path(tmp) / "report.json"
        if shutil.which("gitleaks"):
            cmd = ["gitleaks", "dir", str(root), "--no-banner", "--redact",
                   "--report-format", "json", "--report-path", str(report)]
        elif shutil.which("docker"):
            cmd = ["docker", "run", "--rm", "-v", f"{tmp}:/scan", "ghcr.io/gitleaks/gitleaks:v8.30.1",
                   "dir", "/scan/src", "--no-banner", "--redact",
                   "--report-format", "json", "--report-path", "/scan/report.json"]
        else:
            raise RagnarError("gitleaks not found (and no docker to run its image). Install it to index.")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return {}
        findings = json.loads(report.read_text(encoding="utf-8") or "[]") if report.exists() else []
        if not findings:
            raise RagnarError(f"gitleaks failed (exit code {r.returncode}): {r.stderr.strip()[-500:]}")
        found: dict[str, list[str]] = {}
        for f in findings:
            found.setdefault(_relative(f["File"], root), []).append(f"{f['StartLine']} ({f['RuleID']})")
        return found


def apply_secret_policy(found: dict[str, list[str]], blobs: dict[str, bytes], action: str) -> int:
    """Stops the run, or removes the files with findings when the server allows it. Returns the files removed."""
    if not found:
        return 0
    where = sorted(f"{path}:{line}" for path, lines in found.items() for line in lines)
    if action == "fail":
        raise RagnarError("secret found; indexing cancelled. Rotate the credential and remove it from the repository:\n  "
                          + "\n  ".join(where))
    print("secret found; files left out of the index:\n  " + "\n  ".join(where), file=sys.stderr, flush=True)
    for path in found:
        blobs.pop(path, None)
    return len(found)


def _relative(file: str, root: Path) -> str:
    rel = file.replace("\\", "/")
    for prefix in (root.as_posix() + "/", "/scan/src/"):
        rel = rel.removeprefix(prefix)
    return rel


def embed_all(emb: Embedder | FakeEmbedder, texts: list[str], repo: str, batch: int, concurrency: int) -> list[list[float]]:
    """Parallel batches: with several embedder replicas behind a Service, all of them work.

    Texts go by length: a batch pads to its longest text, so similar lengths cost less on a CPU.
    """
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    groups = [order[i : i + batch] for i in range(0, len(order), batch)]
    vectors: list[list[float]] = [[] for _ in texts]
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(emb.embed_documents, [texts[i] for i in g]): g for g in groups}
        for f in as_completed(futures):
            for i, vector in zip(futures[f], f.result()):
                vectors[i] = vector
            done += len(futures[f])
            print(f"{repo}: embeddings {done}/{len(texts)}", file=sys.stderr, flush=True)
    return vectors


def content_hash(text: str, doc_prefix: str) -> str:
    return hashlib.sha256((doc_prefix + text).encode()).hexdigest()[:32]


def reusable_vectors(dsn: str, repo: str, hashes: list[str]) -> dict[str, str]:
    """Vectors already stored for this content, as pgvector text. The caller checks that the model is the same."""
    with psycopg.connect(dsn) as conn:
        rows = conn.execute("SELECT DISTINCT ON (content_hash) content_hash, embedding::text FROM code_chunk "
                            "WHERE repo = %s AND content_hash = ANY(%s)", (repo, hashes)).fetchall()
    return dict(rows)


@dataclass
class Result:
    repo: str
    mode: str  # full | incremental | unchanged
    commit_sha: str
    files: int
    chunks: int
    deleted_chunks: int
    seconds: float
    reused_vectors: int = 0
    skipped: dict[str, int] = field(default_factory=dict)  # reason -> files


def repo_settings(repo_dir: Path, head: str, settings: Settings) -> Settings:
    """Server settings plus the [index] section of the ragnar.toml committed in the repository."""
    blob = read_blobs(repo_dir, head, [CONFIG_FILE]).get(CONFIG_FILE)
    return with_repo_config(settings, blob.decode(errors="replace") if blob else None)


def index(source: str, repo: str, dsn: str, emb: Embedder | FakeEmbedder, settings: Settings | None = None,
          ref: str | None = None, full: bool = False, scan: bool = True,
          git_env: dict[str, str] | None = None) -> Result:
    t0 = time.monotonic()
    if not REPO_NAME.match(repo):
        raise RagnarError(f"invalid repository name {repo!r}: use letters, digits, '.', '_', '-' and '@'")
    settings = settings or load_settings()
    repo_dir = resolve_source(source, repo, git_env)
    head = git(repo_dir, "rev-parse", f"{ref or settings.project.branch}^{{commit}}").decode().strip()
    settings = repo_settings(repo_dir, head, settings)
    fingerprint = index_fingerprint(settings)
    chunker = Chunker.from_settings(settings)

    with psycopg.connect(dsn) as conn:
        state = conn.execute("SELECT commit_sha, embed_model, fingerprint FROM index_state WHERE repo = %s",
                             (repo,)).fetchone()

    if full or state is None or state[1] != emb.model or state[2] != fingerprint:
        mode, paths = "full", list_files(repo_dir, head)
    elif state[0] == head:
        return Result(repo, "unchanged", head, 0, 0, 0, time.monotonic() - t0)
    else:
        changed = changed_files(repo_dir, state[0], head)
        mode, paths = ("incremental", changed) if changed is not None else ("full", list_files(repo_dir, head))

    blobs = read_blobs(repo_dir, head, [p for p in paths if chunker.eligible(p)])
    skipped = Counter(reason for path, src in blobs.items() if (reason := chunker.skip_reason(path, src)))
    if scan and settings.index.secret_scan:
        # normalized text: gitleaks does not read UTF-16 and would miss secrets in it
        normalized = {p: normalize_source(b, settings.index.legacy_encoding) for p, b in blobs.items()}
        found = secret_scan({p: b for p, b in normalized.items() if b is not None})
        if removed := apply_secret_policy(found, blobs, settings.index.secret_action):
            skipped["secret"] = removed
    chunks: list[Chunk] = []
    symbols: list[Symbol] = []
    for path, src in blobs.items():
        chunks += chunker.chunk_file(path, src)
        symbols += chunker.extract_symbols(path, src)
    if settings.contextual.enabled and (chat := chat_from(settings)):
        chunks = contextualize(chat, settings.contextual, chunks, blobs)
    hashes = [content_hash(c.content, settings.embedding.doc_prefix) for c in chunks]
    known = reusable_vectors(dsn, repo, hashes) if state is not None and state[1] == emb.model else {}
    missing = [i for i, h in enumerate(hashes) if h not in known]
    fresh = embed_all(emb, [chunks[i].content for i in missing], repo, settings.embedding.batch,
                      settings.embedding.concurrency)
    vectors = {i: to_pgvector(v) for i, v in zip(missing, fresh)}
    for i, h in enumerate(hashes):
        vectors.setdefault(i, known.get(h, ""))

    # Short transaction at the end: lock the repo, confirm nobody indexed first, swap the chunks.
    with psycopg.connect(dsn) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (repo,))
        now = conn.execute("SELECT commit_sha FROM index_state WHERE repo = %s", (repo,)).fetchone()
        if (now[0] if now else None) != (state[0] if state else None):
            raise RagnarError(f"another indexer wrote {repo} during this run; run it again")
        scope, args = ("repo = %s", (repo,)) if mode == "full" else ("repo = %s AND path = ANY(%s)", (repo, paths))
        deleted = conn.execute(f"DELETE FROM code_chunk WHERE {scope}", args).rowcount
        conn.execute(f"DELETE FROM code_symbol WHERE {scope}", args)
        with conn.cursor().copy(f"COPY code_chunk ({', '.join(COLUMNS)}) FROM STDIN") as cp:
            for i, c in enumerate(chunks):
                cp.write_row((repo, c.path, c.symbol, c.language, c.kind, c.start_line, c.end_line, head, c.content,
                              c.search_text, vectors[i], hashes[i], split_identifiers(f"{c.path} {c.symbol}")))
        with conn.cursor().copy(f"COPY code_symbol ({', '.join(SYMBOL_COLUMNS)}) FROM STDIN") as cp:
            for s in symbols:
                cp.write_row((repo, s.path, s.symbol, s.name, s.node_type, s.kind,
                              s.start_line, s.end_line, s.signature, head))
        conn.execute(
            """INSERT INTO index_state (repo, commit_sha, embed_model, fingerprint, skipped) VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (repo) DO UPDATE SET commit_sha = EXCLUDED.commit_sha, embed_model = EXCLUDED.embed_model,
               fingerprint = EXCLUDED.fingerprint, skipped = EXCLUDED.skipped, indexed_at = now()""",
            (repo, head, emb.model, fingerprint, json.dumps(skipped)),
        )
    return Result(repo, mode, head, len(blobs), len(chunks), deleted, round(time.monotonic() - t0, 1),
                  len(chunks) - len(missing), dict(skipped))

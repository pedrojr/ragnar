"""Indexer: the only process that writes to the database, and only to the primary.

Reads files from the commit of the branch, never from the working tree: nothing
uncommitted enters the index. Runs the initial build or the increment since the last
indexed commit. Writes in a single transaction, so a search never sees a half-built index.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import psycopg

from .chunker import Chunk, Chunker, Symbol, normalize_source
from .config import CONFIG_FILE, RagnarError, Settings, index_fingerprint, load_settings, with_repo_config
from .embedder import Embedder, FakeEmbedder, to_pgvector
from .llm import chat_from, contextualize

COLUMNS = (
    "repo", "path", "symbol", "language", "kind", "start_line", "end_line",
    "commit_sha", "content", "search_text", "embedding",
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
    return [p for p in git(repo_dir, "ls-tree", "-r", "-z", "--name-only", sha).decode().split("\0") if p]


def changed_files(repo_dir: Path, old: str, new: str) -> list[str] | None:
    """Files changed between the commits; None if the old commit is gone (force push)."""
    try:
        git(repo_dir, "cat-file", "-e", f"{old}^{{commit}}")
    except RagnarError:
        return None
    out = git(repo_dir, "diff", "--name-only", "-z", "--no-renames", old, new).decode()
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


def secret_scan(files: dict[str, bytes]) -> None:
    """Runs gitleaks on the files about to be indexed. A finding stops the run."""
    if not files:
        return
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
            return
        findings = json.loads(report.read_text(encoding="utf-8") or "[]") if report.exists() else []
        if not findings:
            raise RagnarError(f"gitleaks failed (exit code {r.returncode}): {r.stderr.strip()[-500:]}")
        where = sorted({f"{_relative(f['File'], root)}:{f['StartLine']} ({f['RuleID']})" for f in findings})
        raise RagnarError("secret found; indexing cancelled. Rotate the credential and remove it from the repository:\n  "
                          + "\n  ".join(where))


def _relative(file: str, root: Path) -> str:
    rel = file.replace("\\", "/")
    for prefix in (root.as_posix() + "/", "/scan/src/"):
        rel = rel.removeprefix(prefix)
    return rel


def embed_all(emb: Embedder | FakeEmbedder, texts: list[str], repo: str, batch: int, concurrency: int) -> list[list[float]]:
    """Parallel batches: with several embedder replicas behind a Service, all of them work."""
    batches = [texts[i : i + batch] for i in range(0, len(texts), batch)]
    out: list[list[list[float]]] = [[] for _ in batches]
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(emb.embed_documents, b): i for i, b in enumerate(batches)}
        for f in as_completed(futures):
            out[futures[f]] = f.result()
            done += len(batches[futures[f]])
            print(f"{repo}: embeddings {done}/{len(texts)}", file=sys.stderr, flush=True)
    return [v for batch_vectors in out for v in batch_vectors]


@dataclass
class Result:
    repo: str
    mode: str  # full | incremental | unchanged
    commit_sha: str
    files: int
    chunks: int
    deleted_chunks: int
    seconds: float


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
    if scan and settings.index.secret_scan:
        # normalized text: gitleaks does not read UTF-16 and would miss secrets in it
        normalized = {p: normalize_source(b, settings.index.legacy_encoding) for p, b in blobs.items()}
        secret_scan({p: b for p, b in normalized.items() if b is not None})
    chunks: list[Chunk] = [c for path, src in blobs.items() for c in chunker.chunk_file(path, src)]
    symbols: list[Symbol] = [s for path, src in blobs.items() for s in chunker.extract_symbols(path, src)]
    if settings.contextual.enabled and (chat := chat_from(settings)):
        chunks = contextualize(chat, settings.contextual, chunks, blobs)
    vectors = embed_all(emb, [c.content for c in chunks], repo, settings.embedding.batch, settings.embedding.concurrency)

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
            for c, v in zip(chunks, vectors):
                cp.write_row((repo, c.path, c.symbol, c.language, c.kind, c.start_line, c.end_line,
                              head, c.content, c.search_text, to_pgvector(v)))
        with conn.cursor().copy(f"COPY code_symbol ({', '.join(SYMBOL_COLUMNS)}) FROM STDIN") as cp:
            for s in symbols:
                cp.write_row((repo, s.path, s.symbol, s.name, s.node_type, s.kind,
                              s.start_line, s.end_line, s.signature, head))
        conn.execute(
            """INSERT INTO index_state (repo, commit_sha, embed_model, fingerprint) VALUES (%s, %s, %s, %s)
               ON CONFLICT (repo) DO UPDATE SET commit_sha = EXCLUDED.commit_sha, embed_model = EXCLUDED.embed_model,
               fingerprint = EXCLUDED.fingerprint, indexed_at = now()""",
            (repo, head, emb.model, fingerprint),
        )
    return Result(repo, mode, head, len(blobs), len(chunks), deleted, round(time.monotonic() - t0, 1))

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

from . import delphi, graph
from .chunker import Chunk, Chunker, Symbol, normalize_source, split_identifiers
from .config import (
    CONFIG_FILE,
    RagnarError,
    Settings,
    index_fingerprint,
    load_settings,
    matches_path,
    summary_settings,
    with_repo_config,
)
from .embedder import Embedder, FakeEmbedder, to_pgvector
from .languages import FORM_EXTENSIONS, is_test, language_for
from .llm import chat_from, contextualize, summarize, summary_chat_from

COLUMNS = (
    "repo", "path", "symbol", "language", "kind", "start_line", "end_line",
    "commit_sha", "content", "search_text", "embedding", "content_hash", "head_text", "literal_text",
)
EDGE_COLUMNS = ("repo", "path", "line", "src_symbol", "dst_name", "kind", "text", "commit_sha")
SYMBOL_COLUMNS = ("repo", "path", "symbol", "name", "node_type", "kind", "start_line", "end_line", "signature", "commit_sha")
CACHE_DIR = Path.home() / ".cache" / "ragnar" / "repos"
REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]*$")
URL_CREDENTIALS = re.compile(r"(?<=://)[^/@\s]+@")


def scrub(text: str) -> str:
    """Removes credentials embedded in URLs from a message."""
    return URL_CREDENTIALS.sub("***@", text)


# A stalled connection must not freeze the worker for every repository: below 1 KB/s for 2 minutes, git gives
# up over HTTP; NETWORK_TIMEOUT bounds a fetch or clone over any transport.
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_HTTP_LOW_SPEED_LIMIT": "1000", "GIT_HTTP_LOW_SPEED_TIME": "120"}
NETWORK_TIMEOUT = 3600.0


def _run_git(cmd: list[str], what: str, input: bytes | None, env: dict[str, str] | None,
             timeout: float | None) -> bytes:
    try:
        r = subprocess.run(cmd, input=input, capture_output=True, timeout=timeout,
                           env={**os.environ, **GIT_ENV, **(env or {})})
    except subprocess.TimeoutExpired:
        raise RagnarError(f"{what}: no answer after {timeout:.0f}s") from None
    if r.returncode != 0:
        raise RagnarError(f"{what}: {scrub(r.stderr.decode(errors='replace').strip())}")
    return r.stdout


def git(repo_dir: Path, *args: str, input: bytes | None = None, env: dict[str, str] | None = None,
        timeout: float | None = None) -> bytes:
    return _run_git(["git", "-C", str(repo_dir), *args], f"git {' '.join(args)}", input, env, timeout)


def resolve_source(source: str, name: str, env: dict[str, str] | None = None) -> Path:
    """Local path of a git repository. A URL becomes a cached mirror, updated on every run."""
    local = Path(source)
    if local.exists():
        return local
    mirror = CACHE_DIR / f"{name}.git"
    if mirror.exists():
        git(mirror, "remote", "update", "--prune", env=env, timeout=NETWORK_TIMEOUT)
    else:
        mirror.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["git", "clone", "--mirror", source, str(mirror)], f"git clone {scrub(source)}", None, env,
                 NETWORK_TIMEOUT)
    return mirror


def safe_path(path: str) -> bool:
    """A tree path that stays inside a checkout. Git hosts refuse the others on push, but a mirror of a host
    that skips fsck, or a local repository, can still carry `..` or a drive letter, and the secret scan writes
    every path to disk."""
    if os.name == "nt" and ("\\" in path or ":" in path):  # separators and drive letters only on Windows
        return False
    if any(ord(ch) < 32 for ch in path):  # a newline would break the `git cat-file --batch` protocol
        return False
    return bool(path) and not path.startswith("/") and all(part not in ("", ".", "..") for part in path.split("/"))


NOT_UTF8, UNSAFE_PATH = "name not UTF-8", "unsafe path"


def _safe(paths: list[str], dropped: dict[str, str] | None = None, deleted: set[str] = frozenset()) -> list[str]:
    """The paths the index can use. The others go to `dropped` with their reason, unless the commit deleted them."""
    # a name committed in cp1252 by an old Git for Windows decodes with U+FFFD, and git no longer finds it
    reasons = {p: NOT_UTF8 if "�" in p else UNSAFE_PATH for p in paths if "�" in p or not safe_path(p)}
    broken = [p for p, r in reasons.items() if r == NOT_UTF8]
    if broken:
        print(f"left out {len(broken)} files whose names are not UTF-8, such as {broken[0]!r}: "
              "rename them in a commit (git mv) to index them", file=sys.stderr, flush=True)
    unsafe = [p for p, r in reasons.items() if r == UNSAFE_PATH]
    if unsafe:
        print(f"left out {len(unsafe)} paths that would leave the checkout, such as {unsafe[0]!r}",
              file=sys.stderr, flush=True)
    if dropped is not None:
        dropped.update({p: r for p, r in reasons.items() if p not in deleted})
    return [p for p in paths if p not in reasons]


def list_files(repo_dir: Path, sha: str, dropped: dict[str, str] | None = None) -> list[str]:
    out = git(repo_dir, "ls-tree", "-r", "-z", "--name-only", sha).decode(errors="replace")
    return _safe([p for p in out.split("\0") if p], dropped)


def changed_files(repo_dir: Path, old: str, new: str, dropped: dict[str, str] | None = None,
                  gone: set[str] | None = None) -> list[str] | None:
    """Files changed between the commits; None if the old commit is gone (force push).

    `gone` collects every changed path, usable or not, so a caller can forget what it knew about them.
    """
    try:
        git(repo_dir, "cat-file", "-e", f"{old}^{{commit}}")
    except RagnarError:
        return None
    out = git(repo_dir, "diff", "--name-status", "-z", "--no-renames", old, new).decode(errors="replace").split("\0")
    status = dict(zip(out[1::2], out[0::2]))  # -z --name-status: status, path, status, path...
    if gone is not None:
        gone.update(p for p in status if p)
    deleted = {p for p, s in status.items() if s.startswith("D")}
    return _safe([p for p in status if p], dropped, deleted)


FORM_SLACK = 16  # a form may be this many times max_file_kb on disk: embedded images make most of it


def blob_sizes(repo_dir: Path, sha: str, paths: list[str]) -> dict[str, int]:
    """Size of each file at the commit, without reading it."""
    if not paths:
        return {}
    out = git(repo_dir, "cat-file", "--batch-check=%(objecttype) %(objectsize)",
              input="".join(f"{sha}:{p}\n" for p in paths).encode()).decode(errors="replace").split("\n")
    # split on \n only: a "missing" line echoes the path, and splitlines would also cut at U+2028
    sizes = {}
    for path, line in zip(paths, out):
        kind, _, size = line.partition(" ")
        if kind == "blob" and size.isdigit():
            sizes[path] = int(size)
    return sizes


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
        # One folder per file, named by its position, holding the file under a name every OS accepts. A tree
        # path as it is could collide on a case-insensitive disk (Cfg.py and cfg.py: one overwrites the other,
        # and its secret goes unscanned) or be invalid on Windows (what?.txt).
        back: dict[str, str] = {}
        for i, (path, content) in enumerate(files.items()):
            if not safe_path(path):
                raise RagnarError(f"refusing to scan a path outside the checkout: {path!r}")
            # the whole path, each part made writable: gitleaks allowlists folders such as env/lib/
            written = f"{i}/" + "/".join(_disk_name(part) for part in path.split("/"))
            back[written] = path
            f = root / written
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
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=NETWORK_TIMEOUT)  # docker may pull
        except subprocess.TimeoutExpired:
            raise RagnarError(f"gitleaks: no answer after {NETWORK_TIMEOUT:.0f}s") from None
        if r.returncode == 0:
            return {}
        findings = json.loads(report.read_text(encoding="utf-8") or "[]") if report.exists() else []
        if not findings:
            raise RagnarError(f"gitleaks failed (exit code {r.returncode}): {r.stderr.strip()[-500:]}")
        found: dict[str, list[str]] = {}
        for f in findings:
            rel = _relative(f["File"], root)
            if rel not in back:  # fail closed: with secret_action = skip, an unmapped file would be indexed
                raise RagnarError(f"gitleaks reported a file the scan did not write: {rel!r}")
            found.setdefault(back[rel], []).append(f"{f['StartLine']} ({f['RuleID']})")
        return found


WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(\.|$)", re.I)


def _disk_name(name: str) -> str:
    """A file name any OS writes as it is: no <>:"/\\|?* or control characters, no trailing dot or space,
    and no reserved device name. The extension stays, so gitleaks rules that look at it still apply."""
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(". ") or "_"
    return "_" + clean if WINDOWS_RESERVED.match(clean) else clean


def apply_secret_policy(found: dict[str, list[str]], blobs: dict[str, bytes], action: str) -> int:
    """Stops the run, or removes the files with findings when the server allows it. Returns the files removed."""
    if not found:
        return 0
    where = sorted(f"{path}:{line}" for path, lines in found.items() for line in lines)
    if action == "fail":
        raise RagnarError("secret found; indexing cancelled. Rotate the credential and remove it from the repository:\n  "
                          + "\n  ".join(where)
                          + "\nIf these are false positives, such as sample keys in tests, the server can set "
                          'index.secret_action = "skip" to leave these files out of the index.')
    print("secret found; files left out of the index:\n  " + "\n  ".join(where), file=sys.stderr, flush=True)
    for path in found:
        blobs.pop(path, None)
    return len(found)


def _relative(file: str, root: Path) -> str:
    rel = file.replace("\\", "/")
    for prefix in (root.as_posix() + "/", "/scan/src/"):
        rel = rel.removeprefix(prefix)
    return rel


def embed_all(emb: Embedder | FakeEmbedder, texts: list[str], repo: str, batch: int, concurrency: int,
              done_batch=None) -> list[list[float]]:
    """Parallel batches: with several embedder replicas behind a Service, all of them work.

    Texts go by length: a batch pads to its longest text, so similar lengths cost less on a CPU.
    done_batch(indexes, vectors) runs as each batch arrives.
    """
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    groups = [order[i : i + batch] for i in range(0, len(order), batch)]
    vectors: list[list[float]] = [[] for _ in texts]
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(emb.embed_documents, [texts[i] for i in g]): g for g in groups}
        for f in as_completed(futures):
            result = f.result()
            for i, vector in zip(futures[f], result):
                vectors[i] = vector
            if done_batch:
                done_batch(futures[f], result)
            done += len(futures[f])
            print(f"{repo}: embeddings {done}/{len(texts)}", file=sys.stderr, flush=True)
    return vectors


MAX_HANDLER_CHARS = 300  # words of form controls added to one chunk


def with_form_pairs(paths: list[str]) -> list[str]:
    """Changed paths plus their form or unit pair: a handler's words come from the form beside it."""
    out = dict.fromkeys(paths)
    for path in paths:
        stem, dot, suffix = path.rpartition(".")
        if not dot:
            continue
        if f".{suffix.lower()}" in FORM_EXTENSIONS:
            out.setdefault(f"{stem}.pas")
        elif suffix.lower() == "pas":
            for ext in FORM_EXTENSIONS:
                out.setdefault(f"{stem}{ext}")
    return list(out)


def handler_words(chunks: list[Chunk], symbols: list[Symbol], blobs: dict[str, bytes], chunker: Chunker) -> dict[int, str]:
    """Chunk index -> words of the form components whose events call a handler in the chunk.

    The question names the screen ("double click on the orders grid"); the code names the handler
    (GridPedidosDblClick). These words join the chunk's path and symbol in the lexical index.
    """
    by_unit: dict[str, dict[str, str]] = {}
    lower = {p.lower(): p for p in blobs}
    for path, src in blobs.items():
        stem, _, suffix = path.rpartition(".")
        unit = lower.get(f"{stem}.pas".lower())
        if f".{suffix.lower()}" not in FORM_EXTENSIONS or unit is None:
            continue
        text = normalize_source(chunker.indexed_source(path, src), chunker.index.legacy_encoding)
        if text is not None and not delphi.is_binary_form(text):
            by_unit.setdefault(unit, {}).update(delphi.event_bindings(delphi.parse_form(text.decode(errors="replace"))))
    if not by_unit:
        return {}
    # The body of a handler comes after its declaration in the class: the last definition is the one to tag.
    # Tagging the declaration would pile the words of every control of a large form onto one chunk.
    bodies: dict[tuple[str, str], tuple[int, str]] = {}
    for s in symbols:
        key = (s.path, s.name.lower())
        if (words := by_unit.get(s.path, {}).get(key[1])) and s.start_line >= bodies.get(key, (0,))[0]:
            bodies[key] = (s.start_line, words)
    handlers: dict[str, list[tuple[int, str]]] = {}
    for (path, _), (line, words) in bodies.items():
        handlers.setdefault(path, []).append((line, words))
    out: dict[int, str] = {}
    for i, c in enumerate(chunks):
        found = [w for line, w in handlers.get(c.path, ()) if c.start_line <= line <= c.end_line]
        if found:
            out[i] = " ".join(dict.fromkeys(found))[:MAX_HANDLER_CHARS]
    return out


def definition_counts(dsn: str, repo: str, symbols: list[Symbol], replaced: list[str] | None) -> dict[str, int]:
    """Declarations per lowercase name: this run's symbols, plus, in an incremental run, the stored
    symbols of the files it does not replace."""
    counts: dict[str, int] = {}
    if replaced is not None:
        with psycopg.connect(dsn) as conn:
            counts = dict(conn.execute("SELECT lower(name), count(*) FROM code_symbol WHERE repo = %s "
                                       "AND NOT (path = ANY(%s)) GROUP BY 1", (repo, replaced)).fetchall())
    for s in symbols:
        counts[s.name.lower()] = counts.get(s.name.lower(), 0) + 1
    return counts


def code_edges(chunker: Chunker, blobs: dict[str, bytes], chunks: list[Chunk], symbols: list[Symbol],
               definitions: dict[str, int]) -> list[graph.Edge]:
    """References, form events and SQL tables of the files of this run."""
    names = graph.useful_names(definitions)
    by_path: dict[str, list[Symbol]] = {}
    for s in symbols:
        by_path.setdefault(s.path, []).append(s)
    edges: list[graph.Edge] = []
    for path, src in blobs.items():
        if (kind := chunker.source_kind(path)) == "doc":
            continue
        text = chunker.readable_text(path, src)
        if text is None:
            continue
        if kind == "form":
            edges += graph.form_edges(path, delphi.parse_form(text))
        else:
            edges += graph.reference_edges(path, text, by_path.get(path, []), names)
    scopes = {path: graph.Scopes(found) for path, found in by_path.items()}
    for chunk in chunks:
        edges += graph.sql_edges(chunk, scopes.get(chunk.path) or graph.Scopes([]))
    return edges


SUMMARY_SYMBOL = "(summary)"


def summary_chunks(chat, settings: Settings, dsn: str, chunker: Chunker, blobs: dict[str, bytes],
                   symbols: list[Symbol], edges: list[graph.Edge]) -> list[Chunk]:
    """One model-written summary per code file of this run, as a chunk of kind summary.

    Summaries are cached by the hash of the file text and the model, so only new or changed files cost
    a model call, even in a full reindex.
    """
    cfg = settings.summaries
    # the cache key: a summary in another language is another summary
    model = summary_settings(settings).model + (f" ({cfg.language})" if cfg.language else "")
    excluded = cfg.exclude + settings.index.summary_exclude
    declared: dict[str, list[str]] = {}
    for s in symbols:
        if s.symbol.count(" > ") <= 2:
            declared.setdefault(s.path, []).append(s.name)
    refers: dict[str, list[str]] = {}
    for e in edges:
        refers.setdefault(e.path, []).append(e.dst_name)
    files = []
    for path, src in blobs.items():
        if chunker.source_kind(path) != "code" or is_test(language_for(path), path) or matches_path(excluded, path):
            continue
        text = chunker.readable_text(path, src)
        if text is None or text.count("\n") + 1 < cfg.min_lines:
            continue
        files.append({"path": path, "text": text, "hash": hashlib.sha256(text.encode()).hexdigest()[:32],
                      "declarations": ", ".join(dict.fromkeys(declared.get(path, [])))[:1500],
                      "refers": ", ".join(dict.fromkeys(refers.get(path, [])))[:1500]})
    if not files:
        return []
    with psycopg.connect(dsn) as conn:
        cached = dict(conn.execute("SELECT source_hash, summary FROM summary_cache WHERE model = %s AND source_hash = ANY(%s)",
                                   (model, [f["hash"] for f in files])).fetchall())
    missing = [f for f in files if f["hash"] not in cached]
    if missing:
        print(f"summaries: {len(missing)} files to the model, {len(files) - len(missing)} cached", file=sys.stderr, flush=True)
        # Each summary reaches the cache as it arrives, outside the run's transaction: a run that stops halfway
        # keeps what the model already wrote. A file whose call fails gets no summary in this run.
        done = [0]
        with psycopg.connect(dsn, autocommit=True) as conn:
            def keep(f: dict, text: str) -> None:
                conn.execute("INSERT INTO summary_cache (model, source_hash, summary) VALUES (%s, %s, %s) "
                             "ON CONFLICT DO NOTHING", (model, f["hash"], text))
                cached[f["hash"]] = text
                done[0] += 1
                if done[0] % 10 == 0 or done[0] == len(missing):
                    print(f"summaries: {done[0]}/{len(missing)}", file=sys.stderr, flush=True)

            summarize(chat, cfg, missing, on_done=keep, skip_errors=True)
        if failed := len(missing) - done[0]:
            print(f"summaries: {failed} files left without a summary; they get one when they change "
                  "or on the next full run (ragnar index --full)", file=sys.stderr, flush=True)
    out = []
    for f in files:
        if f["hash"] not in cached:
            continue
        lang = language_for(f["path"])
        comment, language = (lang.comment, lang.name) if lang else ("#", "text")
        content = f"{comment} file: {f['path']}\n{comment} symbol: {SUMMARY_SYMBOL}\n{cached[f['hash']]}"
        out.append(Chunk(f["path"], SUMMARY_SYMBOL, language, "summary", 1, f["text"].count("\n") + 1, content,
                         content + "\n" + split_identifiers(content)))
    return out


def content_hash(text: str, doc_prefix: str) -> str:
    return hashlib.sha256((doc_prefix + text).encode()).hexdigest()[:32]


def reusable_vectors(dsn: str, repo: str, hashes: list[str]) -> dict[str, str]:
    """Vectors already stored for this content, as pgvector text. The caller checks that the model is the same."""
    with psycopg.connect(dsn) as conn:
        rows = conn.execute("SELECT DISTINCT ON (content_hash) content_hash, embedding::text FROM code_chunk "
                            "WHERE repo = %s AND content_hash = ANY(%s)", (repo, hashes)).fetchall()
    return dict(rows)


def checkpointed_vectors(dsn: str, model: str, hashes: list[str]) -> dict[str, str]:
    """Vectors embedded by a run that stopped before it committed."""
    with psycopg.connect(dsn) as conn:
        rows = conn.execute("SELECT content_hash, embedding FROM embedding_checkpoint "
                            "WHERE model = %s AND content_hash = ANY(%s)", (model, hashes)).fetchall()
    return dict(rows)


class Checkpoint:
    """Saves each batch of vectors as it arrives, outside the run's transaction."""

    def __init__(self, dsn: str, model: str, hashes: list[str]):
        self.conn = psycopg.connect(dsn, autocommit=True)
        self.model, self.hashes = model, hashes

    def __call__(self, indexes: list[int], vectors: list[list[float]]) -> None:
        with self.conn.cursor() as cur:
            cur.executemany("INSERT INTO embedding_checkpoint (model, content_hash, embedding) VALUES (%s, %s, %s) "
                            "ON CONFLICT DO NOTHING",
                            [(self.model, self.hashes[i], to_pgvector(v)) for i, v in zip(indexes, vectors)])

    def close(self) -> None:
        self.conn.close()


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


def previous_skips(stored: object) -> dict[str, str]:
    """The path -> reason map of the last run. Older versions stored reason -> count, which names no file."""
    if not isinstance(stored, dict):
        return {}
    return {p: r for p, r in stored.items() if isinstance(r, str)}


def repo_settings(repo_dir: Path, head: str, settings: Settings) -> Settings:
    """Server settings plus the [index] section of the .ragnar/config.toml committed in the repository."""
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
        state = conn.execute("SELECT commit_sha, embed_model, fingerprint, skipped FROM index_state WHERE repo = %s",
                             (repo,)).fetchone()

    # path -> why it is not in the index. An incremental run starts from the last map and forgets the paths
    # it sees again, so files skipped long ago stay counted and files fixed or deleted leave the map.
    left_out: dict[str, str] = {}
    gone: set[str] = set()
    if full or state is None or state[1] != emb.model or state[2] != fingerprint:
        mode, paths = "full", list_files(repo_dir, head, left_out)
    elif state[0] == head:
        return Result(repo, "unchanged", head, 0, 0, 0, round(time.monotonic() - t0, 1))
    else:
        changed = changed_files(repo_dir, state[0], head, left_out, gone)
        if changed is None:
            mode, paths = "full", list_files(repo_dir, head, left_out)
        else:
            mode, paths = "incremental", with_form_pairs(changed)
    before = previous_skips(state[3] if state is not None else None) if mode == "incremental" else {}
    run_skips: dict[str, str] = dict(left_out)  # this run's reasons, for the report

    wanted = [p for p in paths if chunker.eligible(p)]
    sizes = blob_sizes(repo_dir, head, wanted)
    # a 200 MB dump must not be read into memory only to be skipped: forms get room for the image data
    # form_source drops, everything else must fit max_file_kb as stored
    limit = settings.index.max_file_kb * 1024
    huge = {p for p in wanted if sizes.get(p, 0) > limit * (FORM_SLACK if chunker.source_kind(p) == "form" else 1)}
    run_skips.update(dict.fromkeys(huge, "too large"))
    blobs = read_blobs(repo_dir, head, [p for p in wanted if p not in huge])
    parsed: dict[str, tuple[list[Chunk], list[Symbol]]] = {}
    for path, src in list(blobs.items()):
        try:
            reason = chunker.skip_reason(path, src)
            parsed[path] = (chunker.chunk_file(path, src), chunker.extract_symbols(path, src))
        except RagnarError:
            raise
        except Exception as exc:  # one malformed file (a broken form, bad bytes) must not stop the repository
            print(f"{repo}: left out {path}: cannot read it ({type(exc).__name__}: {str(exc)[:120]})",
                  file=sys.stderr, flush=True)
            del blobs[path]
            run_skips[path] = "unreadable"
            continue
        if reason:  # out of the index: out of the secret scan too, so a sample key there cannot stop the run
            run_skips[path] = reason
            del blobs[path]
    if scan and settings.index.secret_scan:
        # what gets indexed, as UTF-8: gitleaks does not read UTF-16, and form images look like tokens
        normalized = {p: normalize_source(chunker.indexed_source(p, b), settings.index.legacy_encoding)
                      for p, b in blobs.items()}
        found = secret_scan({p: b for p, b in normalized.items() if b is not None})
        if apply_secret_policy(found, blobs, settings.index.secret_action):
            run_skips.update(dict.fromkeys(found, "secret"))
    seen = set(paths) | gone
    skipped_map = {p: r for p, r in before.items() if p not in seen} | run_skips
    skipped = Counter(run_skips.values())
    chunks: list[Chunk] = []
    symbols: list[Symbol] = []
    for path in blobs:
        chunks += parsed[path][0]
        symbols += parsed[path][1]
    extra_head = handler_words(chunks, symbols, blobs, chunker)
    edges = code_edges(chunker, blobs, chunks, symbols, definition_counts(dsn, repo, symbols, None if mode == "full" else paths))
    if settings.contextual.enabled and (chat := chat_from(settings)):
        chunks = contextualize(chat, settings.contextual, chunks, blobs)
    if settings.summaries.enabled and (chat := summary_chat_from(settings)):
        chunks += summary_chunks(chat, settings, dsn, chunker, blobs, symbols, edges)
    hashes = [content_hash(c.content, settings.embedding.doc_prefix) for c in chunks]
    known = reusable_vectors(dsn, repo, hashes) if state is not None and state[1] == emb.model else {}
    known |= checkpointed_vectors(dsn, emb.model, [h for h in hashes if h not in known])
    missing = [i for i, h in enumerate(hashes) if h not in known]
    checkpoint = Checkpoint(dsn, emb.model, hashes) if missing else None
    try:
        fresh = embed_all(emb, [chunks[i].content for i in missing], repo, settings.embedding.batch,
                          settings.embedding.concurrency,
                          checkpoint and (lambda batch, vecs: checkpoint([missing[j] for j in batch], vecs)))
    finally:
        if checkpoint:
            checkpoint.close()
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
        conn.execute(f"DELETE FROM code_edge WHERE {scope}", args)
        with conn.cursor().copy(f"COPY code_chunk ({', '.join(COLUMNS)}) FROM STDIN") as cp:
            for i, c in enumerate(chunks):
                cp.write_row((repo, c.path, c.symbol, c.language, c.kind, c.start_line, c.end_line, head, c.content,
                              c.search_text, vectors[i], hashes[i],
                              split_identifiers(f"{c.path} {c.symbol} {extra_head.get(i, '')}"),
                              split_identifiers(c.literals)))
        with conn.cursor().copy(f"COPY code_symbol ({', '.join(SYMBOL_COLUMNS)}) FROM STDIN") as cp:
            for s in symbols:
                cp.write_row((repo, s.path, s.symbol, s.name, s.node_type, s.kind,
                              s.start_line, s.end_line, s.signature, head))
        with conn.cursor().copy(f"COPY code_edge ({', '.join(EDGE_COLUMNS)}) FROM STDIN") as cp:
            for e in edges:
                cp.write_row((repo, e.path, e.line, e.src_symbol, e.dst_name, e.kind, e.text, head))
        conn.execute(
            """INSERT INTO index_state (repo, commit_sha, embed_model, fingerprint, skipped) VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (repo) DO UPDATE SET commit_sha = EXCLUDED.commit_sha, embed_model = EXCLUDED.embed_model,
               fingerprint = EXCLUDED.fingerprint, skipped = EXCLUDED.skipped, indexed_at = now()""",
            (repo, head, emb.model, fingerprint, json.dumps(skipped_map, ensure_ascii=False)),
        )
        conn.execute("DELETE FROM embedding_checkpoint WHERE model = %s AND content_hash = ANY(%s)", (emb.model, hashes))
    return Result(repo, mode, head, len(blobs), len(chunks), deleted, round(time.monotonic() - t0, 1),
                  len(chunks) - len(missing), dict(skipped))

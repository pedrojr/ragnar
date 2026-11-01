"""Indexador: o único processo que grava no banco, e só no primário.

Lê os arquivos do commit da branch (não do diretório de trabalho): nada que não
foi commitado entra no índice. Faz a base inicial ou o incremento desde o último
commit indexado. Grava numa transação só, então a busca nunca vê índice pela metade.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import psycopg

from .chunker import Chunk, Symbol, chunk_file, eligible, extract_symbols
from .config import RagError
from .embedder import Embedder, FakeEmbedder, to_pgvector

COLUMNS = (
    "repo", "path", "symbol", "language", "kind", "start_line", "end_line",
    "commit_sha", "content", "search_text", "embedding",
)
SYMBOL_COLUMNS = ("repo", "path", "symbol", "name", "node_type", "kind", "start_line", "end_line", "signature", "commit_sha")
CACHE_DIR = Path.home() / ".cache" / "raglib" / "repos"


def git(repo_dir: Path, *args: str, input: bytes | None = None) -> bytes:
    r = subprocess.run(["git", "-C", str(repo_dir), *args], input=input, capture_output=True)
    if r.returncode != 0:
        raise RagError(f"git {' '.join(args)}: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def resolve_source(source: str, name: str) -> Path:
    """Caminho local de um repositório git. URL vira clone espelho em cache, atualizado a cada execução."""
    local = Path(source)
    if local.exists():
        return local
    mirror = CACHE_DIR / f"{name}.git"
    if mirror.exists():
        git(mirror, "remote", "update", "--prune")
    else:
        mirror.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["git", "clone", "--mirror", source, str(mirror)], capture_output=True)
        if r.returncode != 0:
            raise RagError(f"git clone {source}: {r.stderr.decode(errors='replace').strip()}")
    return mirror


def list_files(repo_dir: Path, sha: str) -> list[str]:
    return [p for p in git(repo_dir, "ls-tree", "-r", "-z", "--name-only", sha).decode().split("\0") if p]


def changed_files(repo_dir: Path, old: str, new: str) -> list[str] | None:
    """Arquivos alterados entre os commits; None se o commit antigo sumiu (force push)."""
    try:
        git(repo_dir, "cat-file", "-e", f"{old}^{{commit}}")
    except RagError:
        return None
    out = git(repo_dir, "diff", "--name-only", "-z", "--no-renames", old, new).decode()
    return [p for p in out.split("\0") if p]


def read_blobs(repo_dir: Path, sha: str, paths: list[str]) -> dict[str, bytes]:
    """Conteúdo dos arquivos no commit, com uma chamada só a `git cat-file --batch`."""
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
    """Roda gitleaks nos arquivos que serão indexados. Achou segredo: falha (regra 3 da seção 9 do guia)."""
    if not files:
        return
    with tempfile.TemporaryDirectory(prefix="rag-scan-") as tmp:
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
            raise RagError("gitleaks não encontrado (nem docker para rodar a imagem). Instale ou use --no-secret-scan.")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return
        findings = json.loads(report.read_text(encoding="utf-8") or "[]") if report.exists() else []
        if not findings:
            raise RagError(f"gitleaks falhou (código {r.returncode}): {r.stderr.strip()[-500:]}")
        where = sorted({f"{_relative(f['File'], root)}:{f['StartLine']} ({f['RuleID']})" for f in findings})
        raise RagError("segredo encontrado; indexação cancelada. Rotacione a credencial e ajuste a denylist:\n  "
                       + "\n  ".join(where))


def _relative(file: str, root: Path) -> str:
    rel = file.replace("\\", "/")
    for prefix in (root.as_posix() + "/", "/scan/src/"):
        rel = rel.removeprefix(prefix)
    return rel


def embed_all(emb: Embedder | FakeEmbedder, texts: list[str], repo: str) -> list[list[float]]:
    """Lotes em paralelo (EMBED_CONCURRENCY): com várias réplicas do embedder atrás de um Service, todas trabalham."""
    batches = [texts[i : i + 16] for i in range(0, len(texts), 16)]
    workers = max(1, int(os.environ.get("EMBED_CONCURRENCY", "2")))
    out: list[list[list[float]]] = [[] for _ in batches]
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(emb.embed_documents, b): i for i, b in enumerate(batches)}
        for f in as_completed(futures):
            out[futures[f]] = f.result()
            done += len(batches[futures[f]])
            print(f"{repo}: embeddings {done}/{len(texts)}", file=sys.stderr, flush=True)
    return [v for batch in out for v in batch]


@dataclass
class Result:
    repo: str
    mode: str  # full | incremental | unchanged
    commit_sha: str
    files: int
    chunks: int
    deleted_chunks: int
    seconds: float


def index(source: str, repo: str, dsn: str, emb: Embedder | FakeEmbedder, ref: str = "main",
          full: bool = False, scan: bool = True) -> Result:
    t0 = time.monotonic()
    repo_dir = resolve_source(source, repo)
    head = git(repo_dir, "rev-parse", f"{ref}^{{commit}}").decode().strip()

    with psycopg.connect(dsn) as conn:
        state = conn.execute("SELECT commit_sha, embed_model FROM index_state WHERE repo = %s", (repo,)).fetchone()

    if full or state is None or state[1] != emb.model:
        mode, paths = "full", list_files(repo_dir, head)
    elif state[0] == head:
        return Result(repo, "unchanged", head, 0, 0, 0, time.monotonic() - t0)
    else:
        changed = changed_files(repo_dir, state[0], head)
        mode, paths = ("incremental", changed) if changed is not None else ("full", list_files(repo_dir, head))

    blobs = read_blobs(repo_dir, head, [p for p in paths if eligible(p)])
    if scan:
        secret_scan(blobs)
    chunks: list[Chunk] = [c for path, src in blobs.items() for c in chunk_file(path, src)]
    symbols: list[Symbol] = [s for path, src in blobs.items() for s in extract_symbols(path, src)]
    vectors = embed_all(emb, [c.content for c in chunks], repo)

    # Transação curta no fim: trava o repo, confere que ninguém indexou antes, troca os chunks.
    with psycopg.connect(dsn) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (repo,))
        now = conn.execute("SELECT commit_sha FROM index_state WHERE repo = %s", (repo,)).fetchone()
        if (now[0] if now else None) != (state[0] if state else None):
            raise RagError(f"outro indexador gravou {repo} durante esta execução; rode de novo")
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
            """INSERT INTO index_state (repo, commit_sha, embed_model) VALUES (%s, %s, %s)
               ON CONFLICT (repo) DO UPDATE SET commit_sha = EXCLUDED.commit_sha,
               embed_model = EXCLUDED.embed_model, indexed_at = now()""",
            (repo, head, emb.model),
        )
    return Result(repo, mode, head, len(blobs), len(chunks), deleted, round(time.monotonic() - t0, 1))

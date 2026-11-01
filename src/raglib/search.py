"""Busca híbrida (vetorial + lexical, fusão RRF) e consultas auxiliares. Só leitura."""

from __future__ import annotations

import re
import time

import psycopg

from .chunker import split_identifiers
from .embedder import Embedder, FakeEmbedder, to_pgvector

HYBRID_SQL = """
WITH sem AS (
  SELECT id, RANK() OVER (ORDER BY embedding <=> %(emb)s::vector) AS r
  FROM code_chunk
  WHERE repo = ANY(%(repos)s) AND path LIKE %(prefix)s ESCAPE '\\'
    AND (%(kind)s::text IS NULL OR kind = %(kind)s)
  ORDER BY embedding <=> %(emb)s::vector
  LIMIT 50
), lex AS (
  SELECT id, RANK() OVER (ORDER BY ts_rank_cd(tsv, q) DESC) AS r
  FROM code_chunk, to_tsquery('simple', %(tsq)s) q
  WHERE repo = ANY(%(repos)s) AND path LIKE %(prefix)s ESCAPE '\\'
    AND (%(kind)s::text IS NULL OR kind = %(kind)s) AND tsv @@ q
  ORDER BY ts_rank_cd(tsv, q) DESC
  LIMIT 50
)
SELECT c.id, c.repo, c.path, c.symbol, c.kind, c.start_line, c.end_line, c.content,
       COALESCE(1.0 / (60 + sem.r), 0) + COALESCE(1.0 / (60 + lex.r), 0) AS score
FROM sem FULL OUTER JOIN lex ON sem.id = lex.id
JOIN code_chunk c ON c.id = COALESCE(sem.id, lex.id)
ORDER BY score DESC
LIMIT %(k)s
"""
HIT_COLUMNS = ("id", "repo", "path", "symbol", "kind", "start_line", "end_line", "content", "score")


def like_prefix(prefix: str) -> str:
    return re.sub(r"([\\%_])", r"\\\1", prefix) + "%"


def tsquery(query: str) -> str:
    # termos sanitizados e unidos com OR: código raramente contém todos os termos da pergunta
    terms = dict.fromkeys(re.findall(r"[a-z0-9]{2,}", split_identifiers(query).lower()))
    return " | ".join(terms) or "zzzznomatch"


def search(conn: psycopg.Connection, emb: Embedder | FakeEmbedder, repos: list[str], query: str,
           k: int = 10, path_prefix: str = "", kind: str | None = None) -> tuple[list[dict], dict[str, float]]:
    """Devolve (trechos, tempos em segundos por fase: embed e sql)."""
    t0 = time.perf_counter()
    vector = to_pgvector(list(emb.embed_query(query)))
    t1 = time.perf_counter()
    # SET LOCAL vale só nesta transação: seguro com pool de conexões
    conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")  # pgvector >= 0.8: filtros não reduzem resultados
    rows = conn.execute(HYBRID_SQL, {
        "emb": vector, "repos": repos, "prefix": like_prefix(path_prefix), "kind": kind,
        "tsq": tsquery(query), "k": k,
    }).fetchall()
    t2 = time.perf_counter()
    return [dict(zip(HIT_COLUMNS, r)) for r in rows], {"embed": t1 - t0, "sql": t2 - t1}


def find_symbol(conn: psycopg.Connection, repos: list[str], name: str, k: int = 20) -> list[dict]:
    """Definições cujo nome começa com `name` (sem diferenciar maiúsculas); nomes exatos primeiro."""
    rows = conn.execute(
        """SELECT id, repo, path, symbol, kind, start_line, end_line, signature
           FROM code_symbol
           WHERE repo = ANY(%(repos)s) AND lower(name) LIKE %(pattern)s
           ORDER BY lower(name) = lower(%(name)s) DESC, kind = 'test', length(symbol), path, start_line
           LIMIT %(k)s""",
        {"repos": repos, "name": name, "pattern": like_prefix(name.lower()), "k": k},
    ).fetchall()
    cols = ("id", "repo", "path", "symbol", "kind", "start_line", "end_line", "signature")
    return [dict(zip(cols, r)) for r in rows]


def list_repos(conn: psycopg.Connection, repos: list[str] | None) -> list[dict]:
    """Repositórios indexados (todos, se `repos` for None)."""
    rows = conn.execute(
        """SELECT s.repo, s.commit_sha, s.embed_model, s.indexed_at,
                  (SELECT count(*) FROM code_chunk c WHERE c.repo = s.repo) AS chunks
           FROM index_state s
           WHERE %(all)s OR s.repo = ANY(%(repos)s)
           ORDER BY s.repo""",
        {"all": repos is None, "repos": repos or []},
    ).fetchall()
    return [dict(zip(("repo", "commit_sha", "embed_model", "indexed_at", "chunks"), r)) for r in rows]

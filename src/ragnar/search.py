"""Retrieval pipeline. Read only.

query -> optional expansion -> ranked lists (vector, lexical, exact symbol) -> RRF fusion
      -> optional rerank -> per-file cap and diversity -> neighbor chunks -> token budget
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import psycopg

from .chunker import split_identifiers
from .config import Retrieval, Settings
from .embedder import Embedder, FakeEmbedder, to_pgvector
from .llm import Chat, Reranker, expand_query

FILTERS = """repo = ANY(%(repos)s) AND path LIKE %(prefix)s ESCAPE '\\'
    AND (%(kind)s::text IS NULL OR kind = %(kind)s)"""
VECTOR_SQL = f"""SELECT id FROM code_chunk WHERE {FILTERS}
ORDER BY embedding <=> %(emb)s::vector LIMIT %(limit)s"""
LEXICAL_SQL = f"""SELECT id FROM code_chunk, to_tsquery('simple', %(tsq)s) q WHERE {FILTERS} AND tsv @@ q
ORDER BY ts_rank_cd(tsv, q) DESC LIMIT %(limit)s"""
SYMBOL_SQL = """SELECT DISTINCT c.id FROM code_symbol s
JOIN code_chunk c ON c.repo = s.repo AND c.path = s.path AND c.start_line <= s.start_line AND c.end_line >= s.start_line
WHERE s.repo = ANY(%(repos)s) AND lower(s.name) = ANY(%(names)s) AND c.path LIKE %(prefix)s ESCAPE '\\'
  AND (%(kind)s::text IS NULL OR c.kind = %(kind)s)
LIMIT %(limit)s"""
HIT_COLUMNS = ("id", "repo", "path", "symbol", "kind", "start_line", "end_line", "content")
HITS_SQL = f"SELECT {', '.join(HIT_COLUMNS)} FROM code_chunk WHERE id = ANY(%s)"
NEIGHBORS_SQL = f"""SELECT {', '.join(HIT_COLUMNS)} FROM code_chunk
WHERE repo = %(repo)s AND path = %(path)s AND id <> %(id)s
  AND (end_line < %(start)s OR start_line > %(end)s)
ORDER BY LEAST(abs(%(start)s - end_line), abs(start_line - %(end)s)) LIMIT %(limit)s"""
CHARS_PER_TOKEN = 4  # rough on purpose: the budget only needs the order of magnitude


def like_prefix(prefix: str) -> str:
    return re.sub(r"([\\%_])", r"\\\1", prefix) + "%"


def tsquery(query: str) -> str:
    # terms joined with OR: code rarely contains every word of a question
    terms = dict.fromkeys(re.findall(r"[^\W_]{2,}", split_identifiers(query).lower()))
    return " | ".join(terms) or "zzzznomatch"


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN + 1


def identifier_names(query: str) -> list[str]:
    """Words of the query that may be symbol names, lowercased: symbol lookups ignore case."""
    return list(dict.fromkeys(w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query)))


def fuse(ranked: list[tuple[float, list[int]]], rrf_k: int) -> list[int]:
    """Weighted Reciprocal Rank Fusion: ids by the sum of weight / (rrf_k + rank)."""
    scores: dict[int, float] = {}
    for weight, ids in ranked:
        for rank, chunk_id in enumerate(ids, 1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (rrf_k + rank)
    return sorted(scores, key=lambda i: (-scores[i], i))


def _terms(text: str) -> set[str]:
    return set(re.findall(r"\w{3,}", text.lower()))


def select_diverse(hits: list[dict], k: int, cfg: Retrieval) -> list[dict]:
    """Cuts to k with a per-file cap and, when diversity > 0, MMR over shared terms."""
    per_file: dict[str, int] = {}
    chosen: list[dict] = []
    pool = list(hits)
    term_sets = {h["id"]: _terms(h["content"]) for h in pool} if cfg.diversity else {}

    def similarity(hit: dict) -> float:
        terms = term_sets[hit["id"]]
        return max((len(terms & term_sets[c["id"]]) / (len(terms | term_sets[c["id"]]) or 1) for c in chosen),
                   default=0.0)

    while pool and len(chosen) < k:
        if cfg.diversity and chosen:
            total = len(pool)
            best = max(range(total), key=lambda i: (1 - cfg.diversity) * (1 - i / total) - cfg.diversity * similarity(pool[i]))
        else:
            best = 0
        hit = pool.pop(best)
        key = f'{hit["repo"]}:{hit["path"]}'
        if cfg.max_chunks_per_file and per_file.get(key, 0) >= cfg.max_chunks_per_file:
            continue
        per_file[key] = per_file.get(key, 0) + 1
        chosen.append(hit)
    return chosen


def fit_budget(hits: list[dict], max_tokens: int) -> list[dict]:
    """Keeps the best hits that fit. The first hit always stays: an answer beats an empty one."""
    if not max_tokens:
        return hits
    out, used = [], 0
    for hit in hits:
        cost = estimate_tokens(hit["content"])
        if out and used + cost > max_tokens:
            break
        out.append(hit)
        used += cost
    return out


@dataclass
class Pipeline:
    """One retrieval configuration with its clients."""

    settings: Settings
    emb: Embedder | FakeEmbedder
    chat: Chat | None = None
    reranker: Reranker | None = None
    timing: dict[str, float] = field(default_factory=dict)

    def _lists(self, conn: psycopg.Connection, text: str, base: dict) -> list[tuple[float, list[int]]]:
        cfg = self.settings.retrieval
        out: list[tuple[float, list[int]]] = []
        if cfg.mode in ("hybrid", "vector"):
            t0 = time.perf_counter()
            vector = to_pgvector(list(self.emb.embed_query(text)))
            self.timing["embed"] = self.timing.get("embed", 0.0) + time.perf_counter() - t0
            rows = conn.execute(VECTOR_SQL, {**base, "emb": vector}).fetchall()
            out.append((cfg.vector_weight, [r[0] for r in rows]))
        if cfg.mode in ("hybrid", "lexical"):
            rows = conn.execute(LEXICAL_SQL, {**base, "tsq": tsquery(text)}).fetchall()
            out.append((cfg.lexical_weight, [r[0] for r in rows]))
        return out

    def search(self, conn: psycopg.Connection, repos: list[str], query: str, k: int | None = None,
               path_prefix: str = "", kind: str | None = None) -> tuple[list[dict], dict[str, float]]:
        """Returns (hits, seconds per phase)."""
        s, cfg = self.settings, self.settings.retrieval
        self.timing = {}
        k = k or cfg.default_k
        t0 = time.perf_counter()
        extras = expand_query(self.chat, s.query.strategy, s.query.variants, query) \
            if self.chat and s.query.strategy != "none" else []
        t1 = time.perf_counter()
        # SET LOCAL lasts for this transaction: safe with a connection pool
        conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")  # pgvector >= 0.8: filters keep the result count
        conn.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(cfg.ef_search),))
        base = {"repos": repos, "prefix": like_prefix(path_prefix), "kind": kind, "limit": cfg.candidates}
        ranked: list[tuple[float, list[int]]] = []
        for text in (query, *extras):
            ranked += self._lists(conn, text, base)
        if cfg.symbol_weight and (names := identifier_names(query)):
            rows = conn.execute(SYMBOL_SQL, {**base, "names": names}).fetchall()
            ranked.append((cfg.symbol_weight, [r[0] for r in rows]))
        ids = fuse(ranked, cfg.rrf_k)[: max(cfg.candidates, k)]
        rows = conn.execute(HITS_SQL, (ids,)).fetchall()
        by_id = {r[0]: dict(zip(HIT_COLUMNS, r)) for r in rows}
        hits = [by_id[i] for i in ids if i in by_id]
        t2 = time.perf_counter()
        if self.reranker and hits:
            top = hits[: s.rerank.candidates]
            hits = [top[i] for i in self.reranker.rerank(query, [h["content"] for h in top]) if i < len(top)]
        t3 = time.perf_counter()
        hits = select_diverse(hits, k, cfg)
        if cfg.neighbor_chunks:
            hits = self._with_neighbors(conn, hits)
        hits = fit_budget(hits, cfg.max_context_tokens)
        self.timing.update({"expand": t1 - t0, "sql": t2 - t1 - self.timing.get("embed", 0.0), "rerank": t3 - t2})
        return hits, self.timing

    def _with_neighbors(self, conn: psycopg.Connection, hits: list[dict]) -> list[dict]:
        """Small-to-big: adds the chunks around each hit, right after it."""
        seen, out = {h["id"] for h in hits}, []
        for hit in hits:
            out.append(hit)
            rows = conn.execute(NEIGHBORS_SQL, {
                "repo": hit["repo"], "path": hit["path"], "id": hit["id"], "start": hit["start_line"],
                "end": hit["end_line"], "limit": self.settings.retrieval.neighbor_chunks,
            }).fetchall()
            for row in rows:
                if row[0] not in seen:
                    seen.add(row[0])
                    out.append(dict(zip(HIT_COLUMNS, row)))
        return out


def find_symbol(conn: psycopg.Connection, repos: list[str], name: str, k: int = 20) -> list[dict]:
    """Definitions whose name starts with `name`, ignoring case; exact names first."""
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
    """Indexed repositories (all of them when `repos` is None)."""
    rows = conn.execute(
        """SELECT s.repo, s.commit_sha, s.embed_model, s.indexed_at,
                  (SELECT count(*) FROM code_chunk c WHERE c.repo = s.repo) AS chunks
           FROM index_state s
           WHERE %(all)s OR s.repo = ANY(%(repos)s)
           ORDER BY s.repo""",
        {"all": repos is None, "repos": repos or []},
    ).fetchall()
    return [dict(zip(("repo", "commit_sha", "embed_model", "indexed_at", "chunks"), r)) for r in rows]

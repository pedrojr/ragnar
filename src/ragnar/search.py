"""Retrieval pipeline. Read only.

query -> optional expansion -> ranked lists (vector, lexical, exact symbol) -> RRF fusion
      -> test demotion -> optional rerank -> per-file cap and diversity -> neighbor chunks
      -> token budget -> references to the hits that did not fit

Lexical ranking is BM25 in spirit, on plain Postgres: each query term weighs by its rarity,
and ts_rank supplies a saturating term frequency with a length penalty. Ordinary ts_rank has no
notion of rarity, so "how", "the" and "is" would weigh as much as "circuit".
"""

from __future__ import annotations

import math
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
TOTAL_SQL = f"SELECT count(*) FROM code_chunk WHERE {FILTERS}"
TERM_SQL = f"SELECT count(*) FROM code_chunk WHERE {FILTERS} AND tsv @@ to_tsquery('simple', %(term)s)"
SYMBOL_SQL = """SELECT c.id FROM code_symbol s
JOIN code_chunk c ON c.repo = s.repo AND c.path = s.path AND c.start_line <= s.start_line AND c.end_line >= s.start_line
WHERE s.repo = ANY(%(repos)s) AND lower(s.name) = ANY(%(names)s) AND c.path LIKE %(prefix)s ESCAPE '\\'
  AND (%(kind)s::text IS NULL OR c.kind = %(kind)s)
GROUP BY c.id
ORDER BY min(array_position(%(names)s::text[], lower(s.name))), bool_and(c.kind = 'test'), c.id
LIMIT %(limit)s"""
HIT_COLUMNS = ("id", "repo", "path", "symbol", "kind", "start_line", "end_line", "content")
HITS_SQL = f"SELECT {', '.join(HIT_COLUMNS)} FROM code_chunk WHERE id = ANY(%s)"
NEIGHBORS_SQL = f"""SELECT {', '.join(HIT_COLUMNS)} FROM code_chunk
WHERE repo = %(repo)s AND path = %(path)s AND id <> %(id)s
  AND (end_line < %(start)s OR start_line > %(end)s)
ORDER BY LEAST(abs(%(start)s - end_line), abs(start_line - %(end)s)) LIMIT %(limit)s"""
CHARS_PER_TOKEN = 4  # rough on purpose: the budget only needs the order of magnitude
RANK_WEIGHTS = [0.1, 0.2, 0.4, 1.0]  # D (body) to A (path and symbol)
RANK_NORMALIZATION = 1  # divides by 1 + log(length): a long chunk does not win by repetition
MAX_TERMS = 12
COMMON_SHARE = 0.25  # a term in more chunks than this share ranks results, but does not select them
STOPWORDS = frozenset("""
a about after all also an and any are as at be been but by can could did do does each either every find for from get got had has
have how i if in into is it its just like may me might more most much must my need no not of on one only or other our out over
please shall should show so some such than that the their them then there these they this those to under up us use used uses
using was way we were what when where which while who whom whose why will with within without would you your
a ao aos as até com como da das de dela dele deles depois do dos e ela ele eles em entre era essa esse esta este eu foi for
foram há isso isto já lhe mais mas me mesmo meu minha muito na nas nem no nos nossa nosso num numa não o onde os ou para
pela pelas pelo pelos por qual quais qualquer quando que quem se sem ser seu seus sobre sua suas são só também tem têm um
uma umas uns vai você
""".split())
TEST_WORDS = frozenset({"test", "tests", "testing", "spec", "specs", "teste", "testes", "unittest", "mock", "mocks"})
OCCURRENCE_SQL = """SELECT repo, path, symbol, kind, language, start_line, content FROM code_chunk
WHERE repo = ANY(%(repos)s) AND path LIKE %(prefix)s ESCAPE '\\' AND tsv @@ phraseto_tsquery('simple', %(words)s)
ORDER BY kind = 'test', repo, path, start_line LIMIT %(limit)s"""


def like_prefix(prefix: str) -> str:
    return re.sub(r"([\\%_])", r"\\\1", prefix) + "%"


def query_terms(query: str) -> list[str]:
    """Lexical terms of a query. Stopwords go, unless the query is nothing else."""
    terms = list(dict.fromkeys(re.findall(r"[^\W_]{2,}", split_identifiers(query).lower())))
    return ([t for t in terms if t not in STOPWORDS] or terms)[:MAX_TERMS]


def term_weights(counts: dict[str, int], total: int) -> dict[str, float]:
    """BM25 inverse document frequency. Terms that match nothing drop out."""
    return {t: math.log(1 + (total - n + 0.5) / (n + 0.5)) for t, n in counts.items() if n}


def selective_terms(counts: dict[str, int], total: int) -> list[str]:
    """Terms that pick candidates. Without this, one common word pulls in the whole repository."""
    present = sorted((n, t) for t, n in counts.items() if n)
    return [t for n, t in present if n <= total * COMMON_SHARE] or [t for _, t in present[:2]]


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN + 1


def identifier_names(query: str) -> list[str]:
    """Words of the query that look like symbol names, lowercased, longest first: lookups ignore case.

    In a sentence, only what reads like code counts: "order" is a word, "TOrder" and "get_order" are names.
    A short query such as "find retry" has no sentence around it, so every word may be a name.
    """
    words = [m for m in re.finditer(r"[A-Za-z_][A-Za-z0-9_]{2,}", query) if m.group().lower() not in STOPWORDS]
    short = len(words) <= 2 and len(query.split()) <= 4

    def named(m: re.Match) -> bool:
        w = m.group()
        code = "_" in w or any(c.isdigit() for c in w) or any(c.isupper() for c in w[1:])
        return code or short or (w[0].isupper() and m.start() > 0)

    names = dict.fromkeys(m.group().lower() for m in words if named(m))
    return sorted(names, key=lambda n: -len(n))


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


def demote_tests(hits: list[dict], penalty: float) -> list[dict]:
    """A test at position p competes as if at p * penalty: production code answers most questions."""
    if penalty <= 1:
        return hits
    positions = [((i + 1) * penalty if h["kind"] == "test" else i + 1, i) for i, h in enumerate(hits)]
    return [hits[i] for _, i in sorted(positions)]


def references(hits: list[dict], kept: list[dict], limit: int) -> list[dict]:
    """The kept hits, then what did not fit as references: a path costs a fraction of its chunk."""
    ids = {h["id"] for h in kept}
    rest = [{**h, "compact": True} for h in hits if h["id"] not in ids]
    return kept + rest[: max(0, limit - len(kept))]


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
            out.append((cfg.lexical_weight, self._lexical(conn, text, base)))
        return out

    def _lexical(self, conn: psycopg.Connection, text: str, base: dict) -> list[int]:
        terms = query_terms(text)
        total = conn.execute(TOTAL_SQL, base).fetchone()[0]
        counts = {t: conn.execute(TERM_SQL, {**base, "term": t}).fetchone()[0] for t in terms}
        weights = term_weights(counts, total)
        if not weights:
            return []
        scored = list(weights.items())
        params = {f"t{i}": term for i, (term, _) in enumerate(scored)}
        params |= {f"w{i}": weight for i, (_, weight) in enumerate(scored)}
        score = " + ".join(f"%(w{i})s::float8 * ts_rank(%(ranks)s::float4[], tsv, to_tsquery('simple', %(t{i})s), "
                           f"{RANK_NORMALIZATION})" for i in range(len(scored)))
        sql = (f"SELECT id FROM code_chunk WHERE {FILTERS} AND tsv @@ to_tsquery('simple', %(selective)s) "
               f"ORDER BY {score} DESC LIMIT %(limit)s")
        rows = conn.execute(sql, {**base, **params, "ranks": RANK_WEIGHTS,
                                  "selective": " | ".join(selective_terms(counts, total))}).fetchall()
        return [r[0] for r in rows]

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
        if kind is None and not TEST_WORDS & set(re.findall(r"\w+", query.lower())):
            hits = demote_tests(hits, cfg.test_penalty)
        t2 = time.perf_counter()
        if self.reranker and hits:
            top = hits[: s.rerank.candidates]
            hits = [top[i] for i in self.reranker.rerank(query, [h["content"] for h in top]) if i < len(top)]
        t3 = time.perf_counter()
        hits = select_diverse(hits, k + cfg.reference_hits, cfg)
        ranked_hits = hits
        hits = hits[:k]
        if cfg.neighbor_chunks:
            hits = self._with_neighbors(conn, hits)
        kept = fit_budget(hits, cfg.max_context_tokens)
        seen = {h["id"] for h in kept}
        hits = references([*hits, *(h for h in ranked_hits if h["id"] not in seen)], kept, len(kept) + cfg.reference_hits)
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


SYMBOL_COLUMNS = ("id", "repo", "path", "symbol", "kind", "start_line", "end_line", "signature")


def split_qualified(name: str) -> tuple[str, str]:
    """'TOrder.Cancel', 'a::b', 'pkg/Func()' -> (owner, name), so a pasted name finds its definition."""
    parts = [p for p in re.split(r"::|->|[.#/\\]", re.sub(r"[(<].*", "", name.strip())) if p]
    return (parts[-2] if len(parts) > 1 else "", parts[-1] if parts else name.strip())


def find_symbol(conn: psycopg.Connection, repos: list[str], name: str, k: int = 20) -> list[dict]:
    """Definitions whose name starts with `name`, or contains it when nothing starts with it.

    Ignores case. Exact names first, and within them the owner of a qualified name.
    """
    owner, short = split_qualified(name)
    args = {"repos": repos, "name": short.lower(), "owner": f"%{like_prefix(owner.lower())}", "k": k}
    order = """ORDER BY lower(name) = %(name)s DESC, lower(symbol) LIKE %(owner)s ESCAPE '\\' DESC, kind = 'test',
               length(symbol), path, start_line LIMIT %(k)s"""
    for pattern in (like_prefix(short.lower()), f"%{like_prefix(short.lower())}"):
        rows = conn.execute(f"""SELECT {", ".join(SYMBOL_COLUMNS)} FROM code_symbol
                                WHERE repo = ANY(%(repos)s) AND lower(name) LIKE %(pattern)s ESCAPE '\\' {order}""",
                            {**args, "pattern": pattern}).fetchall()
        if rows:
            break
    return [dict(zip(SYMBOL_COLUMNS, r)) for r in rows]


def _body(content: str) -> list[str]:
    """Source lines of a chunk: everything after its two header lines (and a contextual note, if any)."""
    lines = content.split("\n")
    for i, line in enumerate(lines[:4]):
        if " symbol: " in line:
            return lines[i + 1 :]
    return lines


def find_references(conn: psycopg.Connection, repos: list[str], name: str, k: int = 30, path_prefix: str = "") -> list[dict]:
    """Lines that use an identifier, definitions left out, production code first."""
    _, short = split_qualified(name)
    words = " ".join(re.findall(r"[^\W_]+", short))
    rows = conn.execute(OCCURRENCE_SQL, {"repos": repos, "prefix": like_prefix(path_prefix), "words": words,
                                         "limit": max(k * 5, 200)}).fetchall()
    defined = {(r[0], r[1], r[2]) for r in conn.execute(
        "SELECT repo, path, start_line FROM code_symbol WHERE repo = ANY(%s) AND lower(name) = %s", (repos, short.lower()))}
    out: list[dict] = []
    seen: set[tuple[str, str, int]] = set()
    for repo, path, symbol, kind, language, start, content in rows:
        pattern = re.compile(rf"(?<![\w]){re.escape(short)}(?![\w])", re.I if language == "delphi" else 0)
        for offset, line in enumerate(_body(content)):
            key = (repo, path, start + offset)
            if pattern.search(line) and key not in defined and key not in seen:
                seen.add(key)
                out.append({"repo": repo, "path": path, "line": start + offset, "symbol": symbol, "kind": kind,
                            "text": line.strip()[:160]})
    return out[:k]


def file_outline(conn: psycopg.Connection, repos: list[str], path: str, limit: int = 400) -> list[dict]:
    """Declarations of a file in order, matched by full path or by its end."""
    rows = conn.execute(
        f"""SELECT {", ".join(SYMBOL_COLUMNS)} FROM code_symbol
            WHERE repo = ANY(%(repos)s) AND (path = %(path)s OR path LIKE %(suffix)s ESCAPE '\\')
            ORDER BY repo, path, start_line LIMIT %(limit)s""",
        {"repos": repos, "path": path, "suffix": f"%/{like_prefix(path)[:-1]}", "limit": limit},
    ).fetchall()
    return [dict(zip(SYMBOL_COLUMNS, r)) for r in rows]


def read_code(conn: psycopg.Connection, repos: list[str], path: str, start: int, end: int) -> dict | None:
    """Lines of an indexed file, rebuilt from its chunks, for code that is not on this disk."""
    row = conn.execute(
        """SELECT repo, path FROM code_chunk
           WHERE repo = ANY(%(repos)s) AND (path = %(path)s OR path LIKE %(suffix)s ESCAPE '\\') ORDER BY repo, path LIMIT 1""",
        {"repos": repos, "path": path, "suffix": f"%/{like_prefix(path)[:-1]}"},
    ).fetchone()
    if row is None:
        return None
    repo, full_path = row
    rows = conn.execute(
        """SELECT start_line, content FROM code_chunk WHERE repo = %s AND path = %s AND start_line <= %s AND end_line >= %s
           ORDER BY start_line""",
        (repo, full_path, end, start),
    ).fetchall()
    lines: dict[int, str] = {}
    for first, content in rows:
        for offset, line in enumerate(_body(content)):
            if start <= first + offset <= end:
                lines.setdefault(first + offset, line)
    return {"repo": repo, "path": full_path, "lines": sorted(lines.items())}


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

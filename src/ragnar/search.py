"""Retrieval pipeline. Read only.

query -> optional expansion -> ranked lists (vector, lexical, whole file, literals, exact symbol)
      -> RRF fusion -> optional graph list around the best hits, fused again -> documentation weight
      -> optional rerank -> per-file cap and diversity -> neighbor chunks -> excerpts
      -> token budget -> references to the hits that did not fit

Lexical ranking is BM25 in spirit, on plain Postgres: each query term weighs by its rarity,
and ts_rank supplies a saturating term frequency with a length penalty. Ordinary ts_rank has no
notion of rarity, so "how", "the" and "is" would weigh as much as "circuit".
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
from dataclasses import dataclass, field

import psycopg

from .chunker import split_identifiers, unaccent
from .config import RagnarError, Retrieval, Settings
from .embedder import Embedder, FakeEmbedder, to_pgvector
from .languages import FAMILY_BY_EXTENSION
from .llm import Chat, Reranker, expand_query


def same_family(a: str, b: str) -> str:
    """SQL: the two paths are in one language family, as ragnar.xref computes it. An xref edge resolves only
    to declarations of its own family. A query that uses it takes the families parameter (FAMILIES_PARAM)."""
    ext = r"substring(lower({}) from '(\.[^./]+)$')"
    return f"(%(families)s::jsonb ->> {ext.format(a)}) = (%(families)s::jsonb ->> {ext.format(b)})"


FAMILIES_PARAM = json.dumps(FAMILY_BY_EXTENSION)


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
WHERE repo = %(repo)s AND path = %(path)s AND id <> %(id)s AND kind <> 'summary'
  AND (end_line < %(start)s OR start_line > %(end)s)
ORDER BY LEAST(abs(%(start)s - end_line), abs(start_line - %(end)s)) LIMIT %(limit)s"""
# Graph neighbors of the best hits: what they call, and who calls them. Each seed brings a few of each,
# in seed order, so a utility called from everywhere does not flood the list.
GRAPH_SQL = f"""
WITH seed AS (
  SELECT c.id, c.repo, c.path, c.start_line, c.end_line, s.ord
  FROM unnest(%(ids)s::bigint[]) WITH ORDINALITY AS s(id, ord) JOIN code_chunk c ON c.id = s.id
), callee AS (
  SELECT seed.ord, d.repo, d.path, d.start_line AS line FROM seed
  CROSS JOIN LATERAL (
    SELECT DISTINCT d.repo, d.path, d.start_line FROM code_edge e
    JOIN code_symbol d ON lower(d.name) = e.dst_name
                      AND (d.repo = e.repo OR (e.kind = 'xref' AND d.repo = ANY(%(repos)s)
                                               AND {same_family("d.path", "e.path")}))
    WHERE e.repo = seed.repo AND e.path = seed.path AND e.line BETWEEN seed.start_line AND seed.end_line
      AND e.kind IN ('ref', 'event', 'xref')
      AND NOT (d.repo = seed.repo AND d.path = seed.path AND d.start_line BETWEEN seed.start_line AND seed.end_line)
    LIMIT 8) d
), caller AS (
  SELECT seed.ord, e.repo, e.path, e.line FROM seed
  CROSS JOIN LATERAL (
    SELECT DISTINCT e.repo, e.path, e.line FROM code_symbol s
    JOIN code_edge e ON e.dst_name = lower(s.name)
                    AND ((e.repo = s.repo AND e.kind IN ('ref', 'event')) OR (e.kind = 'xref' AND e.repo <> s.repo
                                                                                  AND {same_family("s.path", "e.path")}))
    WHERE s.repo = seed.repo AND s.path = seed.path AND s.start_line BETWEEN seed.start_line AND seed.end_line
      AND e.path <> seed.path
    LIMIT 8) e
), near AS (SELECT * FROM callee UNION ALL SELECT * FROM caller)
SELECT c.id FROM near
JOIN code_chunk c ON c.repo = near.repo AND c.path = near.path AND c.start_line <= near.line AND c.end_line >= near.line
WHERE c.id <> ALL(%(ids)s) AND c.repo = ANY(%(repos)s) AND c.path LIKE %(prefix)s ESCAPE '\\'
  AND (%(kind)s::text IS NULL OR c.kind = %(kind)s)
GROUP BY c.id ORDER BY min(near.ord), count(*) DESC, c.id LIMIT %(limit)s"""
CHARS_PER_TOKEN = 4  # rough on purpose: the budget only needs the order of magnitude
RANK_WEIGHTS = [0.1, 0.2, 0.2, 0.5]  # D (body), C (unused), B (string literals), A (path and symbol)
LITERAL_RANK_WEIGHTS = [0.0, 0.0, 1.0, 0.0]  # the literal list ranks by string literals alone
# "text", “text” and 'text', but not the apostrophe of "don't"
QUOTED = re.compile(r""""([^"]{3,}?)"|“([^”]{3,}?)”|(?<!\w)'([^']{3,}?)'(?!\w)""")
MAX_TERMS = 12
MIN_STEM = 4
FILE_CANDIDATES = 40  # files considered by the whole-file list
FILE_K1 = 1.2  # saturation of the chunks of a file that hold a term: 0 counts presence only
FILE_B = 0.4  # length penalty of the whole-file list: a giant unit holds every word
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
STOPWORDS |= frozenset(unaccent(w) for w in STOPWORDS)  # questions are often typed without accents
OCCURRENCE_SQL = """SELECT repo, path, symbol, kind, language, start_line, content FROM code_chunk
WHERE repo = ANY(%(repos)s) AND path LIKE %(prefix)s ESCAPE '\\' AND kind <> 'summary'
  AND tsv @@ phraseto_tsquery('simple', %(words)s)
ORDER BY kind = 'test', repo, path, start_line LIMIT %(limit)s"""


def like_prefix(prefix: str) -> str:
    return re.sub(r"([\\%_])", r"\\\1", prefix) + "%"


# English and Portuguese endings, longest first: code mixes both, and names use the verb where
# questions use the noun ("cancelamento" asks for Cancelar, "validacao" for ValidaCadastro)
SUFFIXES = ("amentos", "imentos", "amento", "imento", "mentos", "mento", "acoes", "icoes", "acao", "icao", "coes",
            "cao", "ados", "adas", "idos", "idas", "ando", "endo", "indo", "ado", "ada", "ido", "ida", "ing", "ar",
            "er", "ir", "ed", "es", "s")


def stem(term: str) -> str:
    """Light stemming as a tsquery prefix: 'escaped' also matches escape, escapes and escapeString."""
    for suffix in SUFFIXES:
        if term.endswith(suffix) and len(term) - len(suffix) >= MIN_STEM:
            return f"{term.removesuffix(suffix)}:*"
    return f"{term}:*" if len(term) >= MIN_STEM + 1 else term


# Words people use for screen parts, and the names VCL, WinForms and their peers give them in code.
# Narrow on purpose: a general Portuguese to English glossary did not pay for itself.
WIDGET_TERMS: dict[str, tuple[str, ...]] = {
    "botao": ("button", "btn"), "botoes": ("button", "btn"), "button": ("btn",), "buttons": ("button", "btn"),
    "grade": ("grid",), "grid": ("grd",),
    "clique": ("click",), "clicar": ("click",), "duplo": ("dbl",), "double": ("dbl",),
    "tela": ("form", "frm"), "telas": ("form", "frm"), "formulario": ("form", "frm"), "janela": ("form", "frm"),
    "screen": ("form", "frm"), "window": ("form", "frm"),
    "campo": ("edit", "field"), "rotulo": ("label", "lbl"), "aba": ("tab", "page"), "menu": ("mnu",),
    "lista": ("list",), "combo": ("cbx", "cmb"), "atalho": ("shortcut",),
}


def query_terms(query: str) -> list[str]:
    """Lexical terms of a query, as tsquery operands. Stopwords go, unless the query is nothing else.

    A word for a screen part brings the names code gives it: "botão" also asks for button and btn.
    """
    terms = list(dict.fromkeys(unaccent(w) for w in re.findall(r"[^\W_]{2,}", split_identifiers(query).lower())))
    words = [t for t in terms if t not in STOPWORDS] or terms
    # the question's own words first: an expansion must never push one of them past MAX_TERMS
    expanded = dict.fromkeys([*words[:MAX_TERMS], *(e for w in words for e in WIDGET_TERMS.get(w, ()))])
    return [stem(t) for t in list(expanded)[:MAX_TERMS]]


def quoted_terms(query: str) -> list[str]:
    """Lexical terms of the quoted parts of a query: the text of a message the person saw."""
    parts = [next(g for g in m.groups() if g) for m in QUOTED.finditer(query)]
    return list(dict.fromkeys(t for part in parts for t in query_terms(part)))


def in_field(term: str, label: str) -> str:
    """A tsquery operand restricted to one weight label: 'cancel:*' -> 'cancel:*B', 'erro' -> 'erro:B'."""
    return f"{term}{label}" if term.endswith(":*") else f"{term}:{label}"


TOTAL_TTL = 300.0  # seconds a chunk total is reused; the index state also invalidates it
_totals: dict[tuple, tuple[float, int]] = {}


def chunk_total(conn: psycopg.Connection, base: dict) -> int:
    """Chunks under the search filters, for term rarity. It changes only when an indexer writes, so it is cached."""
    stamp = conn.execute("SELECT max(indexed_at) FROM index_state WHERE repo = ANY(%s)", (base["repos"],)).fetchone()[0]
    key = (getattr(getattr(conn, "info", None), "dsn", ""), tuple(sorted(base["repos"])), base["prefix"], base["kind"], stamp)
    now = time.monotonic()
    if (cached := _totals.get(key)) and now - cached[0] < TOTAL_TTL:
        return cached[1]
    total = conn.execute(TOTAL_SQL, base).fetchone()[0]
    if len(_totals) > 1000:
        _totals.clear()
    _totals[key] = (now, total)
    return total


def term_counts(conn: psycopg.Connection, base: dict, terms: list[str]) -> dict[str, int]:
    """Chunks that hold each term. One GIN lookup per term is faster than one pass with FILTER,
    which reads the vector of every chunk that holds any term."""
    return {t: conn.execute(TERM_SQL, {**base, "term": t}).fetchone()[0] for t in terms}


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


def file_scores(hits: dict[str, dict[tuple[str, str], int]], sizes: dict[tuple[str, str], int],
                weights: dict[str, float]) -> dict[tuple[str, str], float]:
    """BM25 over files, with a chunk as the unit of text: hits[term][file] is how many chunks of the file hold the term.

    A 20 000 line unit holds every word of a question, and that alone proves little. With
    FILE_K1 = 0 only presence counts, and size does not matter.
    """
    mean_size = sum(sizes.values()) / len(sizes) if sizes else 1.0
    held: dict[tuple[str, str], float] = {}
    for term, found in hits.items():
        for key, n in found.items():
            norm = 1 - FILE_B + FILE_B * sizes.get(key, mean_size) / mean_size
            held[key] = held.get(key, 0.0) + weights[term] * n * (FILE_K1 + 1) / (n + FILE_K1 * norm)
    return held


def fused_scores(ranked: list[tuple[float, list[int]]], rrf_k: int) -> dict[int, float]:
    """Weighted Reciprocal Rank Fusion: the sum of weight / (rrf_k + rank) per id."""
    scores: dict[int, float] = {}
    for weight, ids in ranked:
        for rank, chunk_id in enumerate(ids, 1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (rrf_k + rank)
    return scores


def fuse(ranked: list[tuple[float, list[int]]], rrf_k: int) -> list[int]:
    """Ids by their fused score, best first."""
    scores = fused_scores(ranked, rrf_k)
    return sorted(scores, key=lambda i: (-scores[i], i))


AGREEMENT_DEPTH = 10  # a list agrees with the top hit when the hit is within its first entries


def confidence(ranked: list[tuple[float, list[int]]], top: int) -> str:
    """high when at least two retrieval lists put the top hit near their start, low otherwise.

    A single list that likes a chunk is how a near miss looks: the closest text, not an answer. The
    agent gets the hint and decides; nothing is hidden, because thresholds that hide results are
    poorly calibrated on code (Agent Retrieval Bench, 2026).
    """
    agreeing = sum(1 for weight, ids in ranked if weight and top in ids[:AGREEMENT_DEPTH])
    return "high" if agreeing >= 2 else "low"


def weigh_kinds(hits: list[dict], scores: dict[int, float], doc_weight: float) -> list[dict]:
    """Reorders hits with documentation scaled by doc_weight. Overview documents mention every module,
    so they match nearly any question; below 1, they stop taking the first place from code."""
    if doc_weight == 1:
        return hits
    factor = {"doc": doc_weight}
    return sorted(hits, key=lambda h: (-scores.get(h["id"], 0.0) * factor.get(h["kind"], 1.0), h["id"]))


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


def references(hits: list[dict], kept: list[dict], limit: int) -> list[dict]:
    """The kept hits, then what did not fit as references: a path costs a fraction of its chunk."""
    seen = {h["id"] for h in kept}
    rest = []
    for h in hits:  # once each: a hit can arrive both as a dropped hit and as a ranked one
        if h["id"] not in seen:
            seen.add(h["id"])
            rest.append({**h, "compact": True})
    return kept + rest[: max(0, limit - len(kept))]


FOCUS_CONTEXT = 2  # lines kept around each line that holds a query term
FOCUS_MIN_LINES = 12  # shorter chunks stay whole
FOCUS_MIN_SAVING = 0.3  # an excerpt must drop at least this share of the lines, or the chunk stays whole


def focus(hit: dict, terms: list[str], context: int = FOCUS_CONTEXT) -> dict:
    """The chunk cut to its first line and the lines that hold query terms, with their numbers.

    A whole chunk spends the token budget on lines that do not answer. Like `grep -C2`, the excerpt keeps
    what matched and says which lines it left out, so read_code can fetch them.
    """
    lines = hit["content"].split("\n")
    head = next((i + 1 for i, line in enumerate(lines[:4]) if " symbol: " in line), 0)
    body = lines[head:]
    if len(body) < FOCUS_MIN_LINES or not terms:
        return hit
    exact = {t for t in terms if not t.endswith(":*")}
    prefixes = tuple(t[:-2] for t in terms if t.endswith(":*"))

    def matches(line: str) -> bool:
        words = re.findall(r"[^\W_]+", unaccent(split_identifiers(line)).lower())
        return any(w in exact or w.startswith(prefixes) for w in words) if prefixes else any(w in exact for w in words)

    keep = {0}  # the first line usually holds the signature
    for i, line in enumerate(body):
        if matches(line):
            keep.update(range(max(0, i - context), min(len(body), i + context + 1)))
    if len(keep) > len(body) * (1 - FOCUS_MIN_SAVING):
        return hit
    first, out, last = hit["start_line"], lines[:head], -1
    for i in sorted(keep):
        if i > last + 1:
            out.append(f"... lines {first + last + 1}-{first + i - 1}")
        out.append(f"{first + i}: {body[i]}")
        last = i
    if last < len(body) - 1:
        out.append(f"... lines {first + last + 1}-{first + len(body) - 1}")
    return {**hit, "content": "\n".join(out), "excerpt": True}


def fit_budget(hits: list[dict], max_tokens: int) -> list[dict]:
    """Keeps the best hits that fit, in order. The first hit always stays: an answer beats an empty one.

    A hit that does not fit is skipped, not a stop: one large chunk in second place must not push
    smaller hits after it out of the budget.
    """
    if not max_tokens:
        return hits
    out, used = [], 0
    for hit in hits:
        cost = estimate_tokens(hit["content"])
        if out and used + cost > max_tokens:
            continue
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
    file_ids: list[int] = field(default_factory=list)
    literal_ids: list[int] = field(default_factory=list)
    graph_ids: list[int] = field(default_factory=list)
    confidence: str = ""  # high | low | none, for the last search

    def _lists(self, conn: psycopg.Connection, text: str, base: dict) -> list[tuple[float, list[int]]]:
        cfg = self.settings.retrieval
        out: list[tuple[float, list[int]]] = []
        if cfg.mode in ("hybrid", "vector"):
            rows = conn.execute(VECTOR_SQL, {**base, "emb": self._vectors[text]}).fetchall()
            out.append((cfg.vector_weight, [r[0] for r in rows]))
        if cfg.mode in ("hybrid", "lexical"):
            out.append((cfg.lexical_weight, self._lexical(conn, text, base)))
        return out

    def _lexical(self, conn: psycopg.Connection, text: str, base: dict) -> list[int]:
        terms = query_terms(text)
        norm = int(self.settings.retrieval.rank_normalization)
        total = chunk_total(conn, base)
        counts = term_counts(conn, base, terms)
        weights = term_weights(counts, total)
        if not weights:
            return []
        scored = list(weights.items())
        params = {f"t{i}": term for i, (term, _) in enumerate(scored)}
        params |= {f"w{i}": weight for i, (_, weight) in enumerate(scored)}
        score = " + ".join(f"%(w{i})s::float8 * ts_rank(%(ranks)s::float4[], tsv, to_tsquery('simple', %(t{i})s), "
                           f"{norm})" for i in range(len(scored)))
        sql = (f"SELECT id FROM code_chunk WHERE {FILTERS} AND tsv @@ to_tsquery('simple', %(selective)s) "
               f"ORDER BY {score} DESC LIMIT %(limit)s")
        selective = selective_terms(counts, total)
        rows = conn.execute(sql, {**base, **params, "ranks": RANK_WEIGHTS, "selective": " | ".join(selective)}).fetchall()
        files = self._file_list(conn, base, selective, weights, score, params) if self.settings.retrieval.file_weight else []
        self.file_ids += files
        if self.settings.retrieval.literal_weight:
            self.literal_ids += self._literal_list(conn, base, text, weights, score, params)
        return [r[0] for r in rows]

    def _literal_list(self, conn: psycopg.Connection, base: dict, text: str, weights: dict[str, float], score: str,
                      params: dict) -> list[int]:
        """Chunks whose string literals hold every quoted word of the query, ranked by those literals.

        Only for quoted text: "erro 'limite de crédito excedido'" asks for that message. On every query,
        this list cost 0.05 to 0.09 of MRR on the benchmark, though it doubled the MRR of symptom questions.
        """
        quoted = [t for t in quoted_terms(text) if t in weights]
        if not quoted:
            return []
        wanted = " & ".join(in_field(t, "B") for t in quoted)
        sql = (f"SELECT id FROM code_chunk WHERE {FILTERS} AND tsv @@ to_tsquery('simple', %(wanted)s) "
               f"ORDER BY {score} DESC LIMIT %(limit)s")
        rows = conn.execute(sql, {**base, **params, "ranks": LITERAL_RANK_WEIGHTS, "wanted": wanted}).fetchall()
        return [r[0] for r in rows]

    def _file_list(self, conn: psycopg.Connection, base: dict, selective: list[str], weights: dict[str, float],
                   score: str, params: dict) -> list[int]:
        """Best chunk of the files that hold the most query weight, spread over any of their chunks.

        A chunk rarely holds every word of a question, and a 20 000 line unit is exactly where the
        words sit far apart. This is what makes grep strong on large files.
        """
        hits: dict[str, dict[tuple[str, str], int]] = {}
        for term in selective:  # one GIN lookup per term: counting with FILTER would read every vector
            hits[term] = {(repo, path): n for repo, path, n in conn.execute(
                f"SELECT repo, path, count(*) FROM code_chunk WHERE {FILTERS} "
                "AND tsv @@ to_tsquery('simple', %(term)s) GROUP BY repo, path", {**base, "term": term})}
        sizes = self._file_sizes(conn, base, {key for found in hits.values() for key in found}) if FILE_B and FILE_K1 else {}
        held = file_scores(hits, sizes, weights)
        top = sorted(held, key=lambda key: (-held[key], key))[:FILE_CANDIDATES]
        if not top:
            return []
        rows = conn.execute(
            f"""SELECT DISTINCT ON (repo, path) repo, path, id FROM code_chunk
                WHERE {FILTERS} AND path = ANY(%(paths)s) AND tsv @@ to_tsquery('simple', %(any)s)
                ORDER BY repo, path, {score} DESC""",
            {**base, **params, "ranks": RANK_WEIGHTS, "paths": [p for _, p in top], "any": " | ".join(selective)},
        ).fetchall()
        best = {(repo, path): chunk for repo, path, chunk in rows}
        return [best[key] for key in top if key in best]

    @staticmethod
    def _file_sizes(conn: psycopg.Connection, base: dict, keys: set[tuple[str, str]]) -> dict[tuple[str, str], int]:
        """Chunks per file, for the files that hold some query term."""
        if not keys:
            return {}
        rows = conn.execute("SELECT repo, path, count(*) FROM code_chunk WHERE repo = ANY(%s) AND path = ANY(%s) "
                            "GROUP BY repo, path", (base["repos"], sorted({p for _, p in keys}))).fetchall()
        return {(repo, path): n for repo, path, n in rows if (repo, path) in keys}

    def search(self, conn: psycopg.Connection, repos: list[str], query: str, k: int | None = None,
               path_prefix: str = "", kind: str | None = None) -> tuple[list[dict], dict[str, float]]:
        """Returns (hits, seconds per phase). A server that pools connections calls the four steps itself,
        so the model calls in prepare and rerank never hold a database connection."""
        self.prepare(query)
        hits = self.retrieve(conn, repos, query, k, path_prefix, kind)
        hits = self.rerank(query, hits)
        return self.finish(conn, query, hits)

    def prepare(self, query: str) -> None:
        """Model calls before any SQL: query expansion, and the query vectors (the embedder caches them)."""
        s = self.settings
        self.timing = {}
        t0 = time.perf_counter()
        try:
            self._extras = expand_query(self.chat, s.query.strategy, s.query.variants, query) \
                if self.chat and s.query.strategy != "none" else []
        except RagnarError as exc:  # expansion is optional: the plain query still answers
            print(f"query expansion skipped: {exc}", file=sys.stderr, flush=True)
            self._extras = []
        t1 = time.perf_counter()
        self._vectors = {text: to_pgvector(list(self.emb.embed_query(text))) for text in (query, *self._extras)} \
            if s.retrieval.mode in ("hybrid", "vector") else {}
        self.timing.update({"expand": t1 - t0, "embed": time.perf_counter() - t1})
        self._prepared = query

    def retrieve(self, conn: psycopg.Connection, repos: list[str], query: str, k: int | None = None,
                 path_prefix: str = "", kind: str | None = None) -> list[dict]:
        """The SQL lists, fused. Call prepare(query) first."""
        if getattr(self, "_prepared", None) != query:
            self.prepare(query)
        cfg = self.settings.retrieval
        self.file_ids, self.literal_ids, self.graph_ids = [], [], []
        self._k = k or cfg.default_k
        k = self._k
        extras = self._extras
        t1 = time.perf_counter()
        # SET LOCAL lasts for this transaction: safe with a connection pool
        conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")  # pgvector >= 0.8: filters keep the result count
        conn.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(cfg.ef_search),))
        base = {"repos": repos, "prefix": like_prefix(path_prefix), "kind": kind, "limit": cfg.candidates}
        ranked: list[tuple[float, list[int]]] = []
        for text in (query, *extras):
            ranked += self._lists(conn, text, base)
        if self.file_ids:
            ranked.append((cfg.file_weight, self.file_ids))
        if self.literal_ids:
            ranked.append((cfg.literal_weight, self.literal_ids))
        if cfg.symbol_weight and (names := identifier_names(query)):
            rows = conn.execute(SYMBOL_SQL, {**base, "names": names}).fetchall()
            ranked.append((cfg.symbol_weight, [r[0] for r in rows]))
        scores = fused_scores(ranked, cfg.rrf_k)
        if cfg.graph_weight:
            seeds = sorted(scores, key=lambda i: (-scores[i], i))[: cfg.graph_seeds]
            args = {**base, "ids": seeds, "families": FAMILIES_PARAM}
            self.graph_ids = [r[0] for r in conn.execute(GRAPH_SQL, args).fetchall()]
            if self.graph_ids:
                ranked.append((cfg.graph_weight, self.graph_ids))
                scores = fused_scores(ranked, cfg.rrf_k)
        ids = sorted(scores, key=lambda i: (-scores[i], i))[: max(cfg.candidates, k)]
        rows = conn.execute(HITS_SQL, (ids,)).fetchall()
        by_id = {r[0]: dict(zip(HIT_COLUMNS, r)) for r in rows}
        hits = weigh_kinds([by_id[i] for i in ids if i in by_id], scores, cfg.doc_weight)
        self._ranked = ranked
        self.timing["sql"] = time.perf_counter() - t1
        return hits

    def rerank(self, query: str, hits: list[dict]) -> list[dict]:
        """The reranker call, if any: no SQL."""
        t2 = time.perf_counter()
        if self.reranker and hits:
            top = hits[: self.settings.rerank.candidates]
            # only valid, first-seen indexes: a reranker that repeats or invents one must not duplicate a hit
            try:
                given = self.reranker.rerank(query, [h["content"] for h in top])
            except RagnarError as exc:  # an optional model must not fail a search the hybrid ranking can answer
                print(f"rerank skipped: {exc}", file=sys.stderr, flush=True)
                given = list(range(len(top)))
            order = dict.fromkeys(i for i in given if isinstance(i, int) and 0 <= i < len(top))
            # hits the reranker left out (or never saw) keep their place after the reranked ones
            hits = [top[i] for i in order] + [h for j, h in enumerate(top) if j not in order] + hits[len(top):]
        self.timing["rerank"] = time.perf_counter() - t2
        return hits

    def finish(self, conn: psycopg.Connection, query: str, hits: list[dict]) -> tuple[list[dict], dict[str, float]]:
        """Diversity, neighbors, excerpts and the token budget. Needs the connection only for neighbor chunks."""
        cfg, k, ranked = self.settings.retrieval, self._k, self._ranked
        hits = select_diverse(hits, k + cfg.reference_hits, cfg)
        ranked_hits = hits
        hits = hits[:k]
        if cfg.neighbor_chunks:
            hits = self._with_neighbors(conn, hits)
        if cfg.excerpts:  # the best hit stays whole: it is the one most likely to be read in full
            terms = query_terms(query)
            hits = [hit if i == 0 else focus(hit, terms) for i, hit in enumerate(hits)]
        self.confidence = confidence(ranked, hits[0]["id"]) if hits else "none"
        kept = fit_budget(hits, cfg.max_context_tokens)
        hits = references([*hits, *ranked_hits], kept, len(kept) + cfg.reference_hits)
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
            ORDER BY path = %(path)s DESC, repo, path, start_line LIMIT %(limit)s""",
        {"repos": repos, "path": path, "suffix": f"%/{like_prefix(path)[:-1]}", "limit": limit},
    ).fetchall()
    out = [dict(zip(SYMBOL_COLUMNS, r)) for r in rows]
    # one file only: two util.py in different folders must not interleave their declarations
    return [s for s in out if (s["repo"], s["path"]) == (out[0]["repo"], out[0]["path"])] if out else out


def read_code(conn: psycopg.Connection, repos: list[str], path: str, start: int, end: int) -> dict | None:
    """Lines of an indexed file, rebuilt from its chunks, for code that is not on this disk."""
    row = conn.execute(
        """SELECT repo, path FROM code_chunk
           WHERE repo = ANY(%(repos)s) AND (path = %(path)s OR path LIKE %(suffix)s ESCAPE '\\')
           ORDER BY path = %(path)s DESC, repo, path LIMIT 1""",
        {"repos": repos, "path": path, "suffix": f"%/{like_prefix(path)[:-1]}"},
    ).fetchone()
    if row is None:
        return None
    repo, full_path = row
    rows = conn.execute(
        """SELECT start_line, content FROM code_chunk WHERE repo = %s AND path = %s AND start_line <= %s AND end_line >= %s
           AND kind <> 'summary'
           ORDER BY start_line""",
        (repo, full_path, end, start),
    ).fetchall()
    lines: dict[int, str] = {}
    for first, content in rows:
        for offset, line in enumerate(_body(content)):
            if start <= first + offset <= end:
                lines.setdefault(first + offset, line)
    return {"repo": repo, "path": full_path, "lines": sorted(lines.items())}


def repo_names(conn: psycopg.Connection) -> list[str]:
    """Every indexed repository, without counting chunks: a wildcard token asks for this on each call."""
    return [r[0] for r in conn.execute("SELECT repo FROM index_state ORDER BY repo").fetchall()]


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


EDGE_COLUMNS = ("repo", "path", "line", "src_symbol", "dst_name", "kind", "text")
CALL_KINDS = ["ref", "event", "xref"]
# An xref edge counts only when its declaration is in a repository the caller may read: otherwise the edge
# would tell a token that some repository it cannot see declares the name.
XREF_READABLE = f"""(e.kind <> 'xref' OR EXISTS (SELECT 1 FROM code_symbol x WHERE x.repo = ANY(%(repos)s)
                    AND x.repo <> e.repo AND lower(x.name) = e.dst_name AND {same_family("x.path", "e.path")}))"""


def edge_references(conn: psycopg.Connection, repos: list[str], name: str, k: int = 30,
                    path_prefix: str = "") -> list[dict]:
    """Routines and form events that name a declaration, from the code graph, in this repository and in the
    others (xref). Empty when the graph has no edge for the name: it only holds names declared in few places."""
    _, short = split_qualified(name)
    rows = conn.execute(
        """SELECT e.repo, e.path, e.line, e.src_symbol, e.kind, e.text, c.kind FROM code_edge e
            LEFT JOIN LATERAL (SELECT kind FROM code_chunk c WHERE c.repo = e.repo AND c.path = e.path
                               AND c.start_line <= e.line AND c.end_line >= e.line LIMIT 1) c ON true
            WHERE e.repo = ANY(%(repos)s) AND e.dst_name = %(name)s AND e.kind = ANY(%(kinds)s)
              AND e.path LIKE %(prefix)s ESCAPE '\\' AND """ + XREF_READABLE + """
            ORDER BY c.kind = 'test', e.repo, e.path, e.line LIMIT %(k)s""",
        {"repos": repos, "name": short.lower(), "kinds": CALL_KINDS, "prefix": like_prefix(path_prefix), "k": k,
         "families": FAMILIES_PARAM},
    ).fetchall()
    return [{"repo": r[0], "path": r[1], "line": r[2], "symbol": r[3], "edge": r[4], "text": r[5], "kind": r[6] or "code"}
            for r in rows]


def callees(conn: psycopg.Connection, repos: list[str], name: str, k: int = 30) -> list[dict]:
    """Declarations named inside the routines called `name`, with where each is declared. An xref edge
    resolves in the other repositories the caller may read."""
    owner, short = split_qualified(name)
    rows = conn.execute(
        f"""SELECT DISTINCT ON (e.dst_name, d.repo, d.path, d.start_line) e.repo, e.dst_name, e.kind, e.line, e.text,
                  d.path, d.start_line, d.symbol, d.repo
           FROM code_symbol s
           JOIN code_edge e ON e.repo = s.repo AND e.path = s.path AND e.line BETWEEN s.start_line AND s.end_line
                            AND e.kind = ANY(%(kinds)s)
           LEFT JOIN code_symbol d ON lower(d.name) = e.dst_name
                                  AND (d.repo = e.repo OR (e.kind = 'xref' AND d.repo = ANY(%(repos)s)
                                                           AND {same_family("d.path", "e.path")}))
           WHERE s.repo = ANY(%(repos)s) AND lower(s.name) = %(name)s AND lower(s.symbol) LIKE %(owner)s ESCAPE '\\'
             AND (e.kind <> 'xref' OR d.repo IS NOT NULL)
           ORDER BY e.dst_name, d.repo, d.path, d.start_line, e.line LIMIT %(k)s""",
        {"repos": repos, "name": short.lower(), "owner": f"%{like_prefix(owner.lower())}", "kinds": CALL_KINDS, "k": k,
         "families": FAMILIES_PARAM},
    ).fetchall()
    return [{"repo": r[0], "name": r[1], "edge": r[2], "line": r[3], "text": r[4], "path": r[5], "start_line": r[6],
             "symbol": r[7], "decl_repo": r[8]} for r in rows]


def table_usage(conn: psycopg.Connection, repos: list[str], table: str, op: str | None = None, k: int = 50) -> list[dict]:
    """Routines whose SQL reads or writes a table, writes first. op: read or write."""
    kinds = {"read": ["sql_read"], "write": ["sql_write"]}.get(op or "", ["sql_write", "sql_read"])
    name = table.strip().lower()
    rows = conn.execute(
        """SELECT repo, path, line, src_symbol, kind, text, dst_name FROM code_edge
           WHERE repo = ANY(%(repos)s) AND kind = ANY(%(kinds)s)
             AND (dst_name = %(name)s OR dst_name LIKE %(qualified)s ESCAPE '\\')
           ORDER BY kind = 'sql_read', repo, path, line LIMIT %(k)s""",
        {"repos": repos, "kinds": kinds, "name": name, "qualified": f"%.{like_prefix(name)[:-1]}", "k": k},
    ).fetchall()
    return [{"repo": r[0], "path": r[1], "line": r[2], "symbol": r[3], "edge": r[4], "text": r[5], "table": r[6]}
            for r in rows]


def trace_flow(conn: psycopg.Connection, repos: list[str], source: str, target: str, max_depth: int = 4,
               fan_out: int = 200) -> list[dict] | None:
    """Shortest chain of references from the routine `source` to the declaration or table `target`.

    Breadth first over the graph, one query per level. Each step lists where the reference is made.
    None when no chain is found within max_depth.
    """
    _, start = split_qualified(source)
    _, goal = split_qualified(target)
    start, goal = start.lower(), goal.lower()
    frontier, parent = {start}, {start: None}
    for _ in range(max_depth):
        rows = conn.execute(
            """SELECT lower(s.name), e.dst_name, e.repo, e.path, e.line, e.kind, e.text FROM code_symbol s
               JOIN code_edge e ON e.repo = s.repo AND e.path = s.path AND e.line BETWEEN s.start_line AND s.end_line
               WHERE s.repo = ANY(%(repos)s) AND lower(s.name) = ANY(%(frontier)s) AND """ + XREF_READABLE + """
               ORDER BY e.kind LIKE 'sql%%' DESC, e.path, e.line LIMIT %(cap)s""",
            {"repos": repos, "frontier": list(frontier), "cap": fan_out * len(frontier), "families": FAMILIES_PARAM}).fetchall()
        nxt: set[str] = set()
        for src, dst, repo, path, line, kind, text in rows:
            if dst in parent:
                continue
            parent[dst] = (src, {"repo": repo, "path": path, "line": line, "edge": kind, "to": dst, "text": text})
            if dst == goal or dst.endswith(f".{goal}"):
                steps, node = [], dst
                while parent[node] is not None:
                    node, step = parent[node]
                    steps.append(step)
                return list(reversed(steps))
            nxt.add(dst)
        if not nxt:
            return None
        frontier = nxt
    return None


REPO_MAP_SQL = f"""
WITH defs AS (
  SELECT DISTINCT repo, path, lower(name) AS name FROM code_symbol
  WHERE repo = ANY(%(repos)s) AND path LIKE %(prefix)s ESCAPE '\\' AND kind <> 'test'
), degree AS (
  SELECT d.repo, d.path, count(*) AS refs FROM defs d
  JOIN code_edge e ON e.dst_name = d.name
                  AND ((e.repo = d.repo AND e.path <> d.path AND e.kind IN ('ref', 'event'))
                       OR (e.kind = 'xref' AND e.repo <> d.repo AND e.repo = ANY(%(repos)s)
                           AND {same_family("d.path", "e.path")}))
  GROUP BY d.repo, d.path
)
SELECT g.repo, g.path, g.refs,
       (SELECT string_agg(s.name, ', ' ORDER BY s.start_line) FROM (
          SELECT name, start_line FROM code_symbol s WHERE s.repo = g.repo AND s.path = g.path
          ORDER BY (length(s.symbol) - length(replace(s.symbol, '>', ''))), s.start_line LIMIT %(per_file)s) s) AS names
FROM degree g ORDER BY g.refs DESC, g.path LIMIT %(k)s"""


def repo_map(conn: psycopg.Connection, repos: list[str], path_prefix: str = "", k: int = 40,
             per_file: int = 6) -> list[dict]:
    """The files under a folder that the rest of the code refers to most, with their top declarations.

    A map ranked by the code graph: where to start reading in an unfamiliar area.
    """
    rows = conn.execute(REPO_MAP_SQL, {"repos": repos, "prefix": like_prefix(path_prefix), "k": k,
                                       "per_file": per_file, "families": FAMILIES_PARAM}).fetchall()
    return [{"repo": r[0], "path": r[1], "refs": r[2], "names": r[3] or ""} for r in rows]

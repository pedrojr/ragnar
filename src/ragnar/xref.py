"""Cross-repository edges: a routine of one repository that names a declaration of another.

The same rule as the edges inside a repository applies (graph.useful_names): a name counts when it is
long enough and declared in few places, here across the other indexed repositories, and when the
repository itself does not declare it (its own declaration wins). A file links only to names declared
in its own language family: Python code that says `from` must not point at a Delphi routine called From.
These edges have kind xref.

The edges are matched from the indexed chunks, not from git, so any repository can be linked again
without its source. Each repository keeps the set of names it was matched against (code_xref_name).
When another repository declares a new name, only the files whose text holds it are scanned again, and
the files that linked to a name nobody declares any more are matched again without it.
"""

from __future__ import annotations

import re
import sys

import psycopg

from . import graph
from .chunker import Symbol
from .languages import FORM_EXTENSIONS, language_for
from .search import _body

BATCH = 300  # names per full-text query
SYMBOL_FIELDS = ("path", "symbol", "name", "node_type", "kind", "start_line", "end_line", "signature")
# languages that call each other's declarations by name
FAMILIES = {"typescript": "javascript", "cpp": "c", "kotlin": "java", "scala": "java"}


def family(path: str) -> str | None:
    lang = language_for(path)
    return FAMILIES.get(lang.name, lang.name) if lang else None


def repo_families(conn: psycopg.Connection, repo: str) -> set[str]:
    exts = conn.execute(r"""SELECT DISTINCT substring(lower(path) from '(\.[^./]+)$') FROM code_symbol
                            WHERE repo = %s""", (repo,)).fetchall()
    return {f for (ext,) in exts if ext and (f := family("x" + ext))}


def external_names(conn: psycopg.Connection, repo: str) -> dict[str, set[str]]:
    """Names other repositories declare that this repository may link to, with the language families
    that declare them. Only the families this repository has count."""
    mine = repo_families(conn, repo)
    rows = conn.execute(
        r"""SELECT lower(s.name), array_agg(DISTINCT substring(lower(s.path) from '(\.[^./]+)$')) FROM code_symbol s
            WHERE s.repo <> %(repo)s AND length(s.name) >= %(least)s
              AND NOT EXISTS (SELECT 1 FROM code_symbol m WHERE m.repo = %(repo)s AND lower(m.name) = lower(s.name))
            GROUP BY 1 HAVING count(*) <= %(most)s""",
        {"repo": repo, "most": graph.MAX_DEFINITIONS, "least": graph.MIN_NAME}).fetchall()
    out: dict[str, set[str]] = {}
    for name, exts in rows:
        if not graph.IDENTIFIER.fullmatch(name):
            continue
        families = {f for ext in exts if ext and (f := family("x" + ext))} & mine
        if families:
            out[name] = families
    return out


def tsquery(names: list[str]) -> str:
    """Matches a chunk that holds any of the names. The simple parser splits my_name into my and name."""
    terms = []
    for name in names:
        parts = [p for p in re.split(r"_+", name) if p]
        if parts:
            terms.append("(" + " <-> ".join(parts) + ")")
    return " | ".join(terms)


def candidate_paths(conn: psycopg.Connection, repo: str, names: list[str]) -> set[str]:
    paths: set[str] = set()
    for i in range(0, len(names), BATCH):
        query = tsquery(names[i : i + BATCH])
        if query:
            paths |= {r[0] for r in conn.execute(
                "SELECT DISTINCT path FROM code_chunk WHERE repo = %s AND kind IN ('code', 'test') "
                "AND tsv @@ to_tsquery('simple', %s)", (repo, query))}
    return paths


FILES_PER_READ = 200  # files whose chunks and symbols one query reads


def file_texts(conn: psycopg.Connection, repo: str, paths: list[str]) -> dict[str, str]:
    """Files rebuilt from their chunks. Lines no chunk covers stay empty, so line numbers hold."""
    lines: dict[str, dict[int, str]] = {}
    for start, content, path in conn.execute(
            "SELECT start_line, content, path FROM code_chunk WHERE repo = %s AND path = ANY(%s) "
            "AND kind IN ('code', 'test') ORDER BY path, start_line", (repo, paths)):
        mine = lines.setdefault(path, {})
        for offset, line in enumerate(_body(content)):
            mine.setdefault(start + offset, line)
    return {path: "\n".join(found.get(n, "") for n in range(1, max(found, default=0) + 1))
            for path, found in lines.items()}


def file_symbols(conn: psycopg.Connection, repo: str, paths: list[str]) -> dict[str, list[Symbol]]:
    out: dict[str, list[Symbol]] = {}
    for row in conn.execute(f"SELECT {', '.join(SYMBOL_FIELDS)} FROM code_symbol WHERE repo = %s AND path = ANY(%s)",
                            (repo, paths)):
        out.setdefault(row[0], []).append(Symbol(*row))
    return out


def link(conn: psycopg.Connection, repo: str, refresh: list[str] | None = None) -> int:
    """Brings the xref edges of one repository up to date. refresh: paths to scan again whatever changed
    (the files an indexing run just replaced). Returns the number of edges written.

    code_xref_name holds (name, family) pairs, so a name that moves to another language family is a change
    too: the files that linked to it are matched again, and keep no edge to the other family's declaration.
    """
    state = conn.execute("SELECT commit_sha FROM index_state WHERE repo = %s", (repo,)).fetchone()
    if state is None:
        return 0
    names = external_names(conn, repo)
    current = {(name, f) for name, families in names.items() for f in families}
    stored = {tuple(r) for r in conn.execute("SELECT name, family FROM code_xref_name WHERE repo = %s", (repo,))}
    added, removed = sorted(current - stored), sorted(stored - current)
    paths = set(refresh or [])
    if removed:
        # files whose edges point at a name that left (or left a family): they are matched again below
        paths |= {r[0] for r in conn.execute(
            "SELECT DISTINCT path FROM code_edge WHERE repo = %s AND kind = 'xref' AND dst_name = ANY(%s)",
            (repo, sorted({n for n, _ in removed})))}
    paths |= candidate_paths(conn, repo, sorted({n for n, _ in added}))
    paths_list = sorted(p for p in paths if not p.lower().endswith(FORM_EXTENSIONS))
    by_family: dict[str, set[str]] = {}
    for name, f in current:
        by_family.setdefault(f, set()).add(name)
    # every read before the COPY: a connection runs nothing else while a COPY is open
    edges: list[graph.Edge] = []
    for i in range(0, len(paths_list), FILES_PER_READ):
        group = [p for p in paths_list[i : i + FILES_PER_READ] if family(p) in by_family]
        if not group:
            continue
        texts, symbols = file_texts(conn, repo, group), file_symbols(conn, repo, group)
        for path in group:
            if path in texts:
                edges += graph.reference_edges(path, texts[path], symbols.get(path, []), by_family[family(path)],
                                               kind="xref")
    if paths_list:
        conn.execute("DELETE FROM code_edge WHERE repo = %s AND kind = 'xref' AND path = ANY(%s)", (repo, paths_list))
    if edges:
        with conn.cursor().copy("COPY code_edge (repo, path, line, src_symbol, dst_name, kind, text, commit_sha) "
                                "FROM STDIN") as cp:
            for e in edges:
                cp.write_row((repo, e.path, e.line, e.src_symbol, e.dst_name, e.kind, e.text, state[0]))
    if removed:
        conn.execute("DELETE FROM code_xref_name WHERE repo = %s AND (name, family) IN "
                     "(SELECT * FROM unnest(%s::text[], %s::text[]))",
                     (repo, [n for n, _ in removed], [f for _, f in removed]))
    if added:
        with conn.cursor().copy("COPY code_xref_name (repo, name, family) FROM STDIN") as cp:
            for name, f in added:
                cp.write_row((repo, name, f))
    return len(edges)


def link_repo(dsn: str, repo: str, refresh: list[str] | None = None) -> int | None:
    """link() in its own transaction, under the repository's indexing lock. None when it failed: the
    failure is reported, and the repository's name set is cleared so the next link matches it all again."""
    try:
        with psycopg.connect(dsn) as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (repo,))
            return link(conn, repo, refresh)
    except Exception as exc:  # noqa: BLE001 - the index itself is already committed; links can wait
        print(f"{repo}: cross-repository links not updated: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        try:
            with psycopg.connect(dsn) as conn:
                conn.execute("DELETE FROM code_xref_name WHERE repo = %s", (repo,))
        except Exception:  # noqa: BLE001 - the database is gone; the next run starts over anyway
            pass
        return None


def link_all(dsn: str, skip: str | None = None, only: str | None = None) -> dict[str, int]:
    """Links every indexed repository (or one), each in its own transaction under its indexing lock.
    A failure is reported and stops neither the others nor the caller: the indexes are already committed."""
    try:
        with psycopg.connect(dsn) as conn:
            repos = [r[0] for r in conn.execute("SELECT repo FROM index_state ORDER BY repo")]
    except psycopg.Error as exc:
        print(f"cross-repository links not updated: {exc}", file=sys.stderr, flush=True)
        return {}
    out: dict[str, int] = {}
    for repo in repos:
        if repo == skip or (only and repo != only):
            continue
        if (edges := link_repo(dsn, repo)) is not None:
            out[repo] = edges
    return out

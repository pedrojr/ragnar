"""Cross-repository edges: a routine of one repository that names a declaration of another.

The same rule as the edges inside a repository applies (graph.useful_names): a name counts when it is
long enough and declared in few places, here across the other indexed repositories, and when the
repository itself does not declare it (its own declaration wins). A file links only to names declared
in its own language family: Python code that says `from` must not point at a Delphi routine called From.
These edges have kind xref.

The edges are matched from the indexed chunks, not from git, so any repository can be linked again
without its source. Each repository keeps the set of names it was matched against (code_xref_name).
When another repository declares a new name, only the files whose text holds it are scanned again, and
a name nobody declares any more takes its edges with it.
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


def file_text(conn: psycopg.Connection, repo: str, path: str) -> str:
    """The file, rebuilt from its chunks. Lines no chunk covers stay empty, so line numbers hold."""
    lines: dict[int, str] = {}
    for start, content in conn.execute("SELECT start_line, content FROM code_chunk WHERE repo = %s AND path = %s "
                                       "AND kind IN ('code', 'test') ORDER BY start_line", (repo, path)):
        for offset, line in enumerate(_body(content)):
            lines.setdefault(start + offset, line)
    return "\n".join(lines.get(n, "") for n in range(1, max(lines, default=0) + 1))


def file_symbols(conn: psycopg.Connection, repo: str, path: str) -> list[Symbol]:
    rows = conn.execute(f"SELECT {', '.join(SYMBOL_FIELDS)} FROM code_symbol WHERE repo = %s AND path = %s",
                        (repo, path)).fetchall()
    return [Symbol(*r) for r in rows]


def link(conn: psycopg.Connection, repo: str, refresh: list[str] | None = None) -> int:
    """Brings the xref edges of one repository up to date. refresh: paths to scan again whatever changed
    (the files an indexing run just replaced). Returns the number of edges written."""
    state = conn.execute("SELECT commit_sha FROM index_state WHERE repo = %s", (repo,)).fetchone()
    if state is None:
        return 0
    names = external_names(conn, repo)
    stored = {r[0] for r in conn.execute("SELECT name FROM code_xref_name WHERE repo = %s", (repo,))}
    added, removed = sorted(names.keys() - stored), sorted(stored - names.keys())
    if removed:
        conn.execute("DELETE FROM code_edge WHERE repo = %s AND kind = 'xref' AND dst_name = ANY(%s)", (repo, removed))
        conn.execute("DELETE FROM code_xref_name WHERE repo = %s AND name = ANY(%s)", (repo, removed))
    paths = set(refresh or []) | candidate_paths(conn, repo, added)
    paths = {p for p in paths if not p.lower().endswith(FORM_EXTENSIONS)}
    # every read before the COPY: a connection runs nothing else while a COPY is open
    by_family: dict[str, set[str]] = {}
    for name, families in names.items():
        for f in families:
            by_family.setdefault(f, set()).add(name)
    edges = [e for path in sorted(paths) if (f := family(path)) in by_family for e in graph.reference_edges(
        path, file_text(conn, repo, path), file_symbols(conn, repo, path), by_family[f], kind="xref")]
    if paths:
        conn.execute("DELETE FROM code_edge WHERE repo = %s AND kind = 'xref' AND path = ANY(%s)", (repo, sorted(paths)))
    if edges:
        with conn.cursor().copy("COPY code_edge (repo, path, line, src_symbol, dst_name, kind, text, commit_sha) "
                                "FROM STDIN") as cp:
            for e in edges:
                cp.write_row((repo, e.path, e.line, e.src_symbol, e.dst_name, e.kind, e.text, state[0]))
    if added:
        with conn.cursor().copy("COPY code_xref_name (repo, name) FROM STDIN") as cp:
            for name in added:
                cp.write_row((repo, name))
    return len(edges)


def link_all(dsn: str, skip: str | None = None, only: str | None = None) -> dict[str, int]:
    """Links every indexed repository (or one), each in its own transaction under its indexing lock.
    A failure on one repository is reported and does not stop the others."""
    with psycopg.connect(dsn) as conn:
        repos = [r[0] for r in conn.execute("SELECT repo FROM index_state ORDER BY repo")]
    out: dict[str, int] = {}
    for repo in repos:
        if repo == skip or (only and repo != only):
            continue
        try:
            with psycopg.connect(dsn) as conn:
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (repo,))
                out[repo] = link(conn, repo)
        except psycopg.Error as exc:
            print(f"{repo}: cross-repository links not updated: {exc}", file=sys.stderr, flush=True)
    return out

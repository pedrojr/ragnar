"""Code graph: which routine refers to which name, which form event calls which handler, and which
routine reads or writes which table.

The edges are found by name, not by a compiler: a word in a routine that names a declaration of the
repository counts as a reference. That works on every language, including Delphi units the grammar
cannot parse, at the price of some noise. Names declared in many places (Create, Execute) or too
short to mean anything are left out, so the edges that remain point somewhere specific.

kinds: ref (a routine names a declaration), event (a form control calls a handler),
       sql_read and sql_write (a routine runs SQL on a table).
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass

from .chunker import Chunk, Symbol
from .delphi import Component

MIN_NAME = 4  # shorter names are mostly loop variables and abbreviations
MAX_DEFINITIONS = 4  # a name declared in more places than this does not point anywhere specific
MAX_LINE_CHARS = 160
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SQL_HINT = re.compile(r"\b(select|insert|update|delete|merge|exec(?:ute)?)\b", re.I)
TABLE = r"([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)"
SQL_WRITES = re.compile(rf"\b(?:insert\s+into|update\s+or\s+insert\s+into|merge\s+into|delete\s+from)\s+{TABLE}"
                        rf"|\bupdate\s+{TABLE}\s+set\b", re.I)
SQL_READS = re.compile(rf"\b(?:from|join)\s+{TABLE}", re.I)
NOT_TABLES = frozenset({"select", "where", "set", "values", "as", "on", "the", "a", "an", "to", "of", "and", "or"})


@dataclass(frozen=True)
class Edge:
    path: str
    line: int
    src_symbol: str  # qualified symbol that holds the reference; empty at file level
    dst_name: str  # lowercase name, or table name for SQL edges
    kind: str
    text: str  # the source line, trimmed


class Scopes:
    """Innermost declaration around a line of one file."""

    def __init__(self, symbols: list[Symbol]):
        self.spans = sorted(((s.start_line, s.end_line, s.symbol) for s in symbols), key=lambda x: (x[0], -x[1]))
        self.starts = [s[0] for s in self.spans]

    def at(self, line: int) -> str:
        best = ""
        for _, end, symbol in reversed(self.spans[: bisect_right(self.starts, line)]):
            if end >= line:
                best = symbol  # the latest start that still covers the line is the innermost
                break
        return best


def useful_names(definitions: dict[str, int]) -> set[str]:
    """Names worth an edge: long enough, and declared in few enough places to mean one thing."""
    return {n for n, count in definitions.items() if len(n) >= MIN_NAME and count <= MAX_DEFINITIONS}


def reference_edges(path: str, text: str, symbols: list[Symbol], names: set[str]) -> list[Edge]:
    """One edge per routine and referenced name, at the first line that names it."""
    scopes = Scopes(symbols)
    declared = {(s.start_line, s.name.lower()) for s in symbols}
    seen: set[tuple[str, str]] = set()
    out: list[Edge] = []
    for number, line in enumerate(text.split("\n"), 1):  # not splitlines: a form feed would shift every line after it
        found = {w.lower() for w in IDENTIFIER.findall(line)} & names
        if not found:
            continue
        scope = scopes.at(number)
        for name in sorted(found):
            if (number, name) in declared or (scope, name) in seen:
                continue
            seen.add((scope, name))
            out.append(Edge(path, number, scope, name, "ref", line.strip()[:MAX_LINE_CHARS]))
    return out


def form_edges(path: str, components: list[Component]) -> list[Edge]:
    """A control's event names the handler it calls: btnSave OnClick -> btnsaveclick."""
    form = components[0].name if components else ""
    out = []
    for c in components:
        for key, value in c.properties:
            if key.startswith("On") and IDENTIFIER.fullmatch(value):
                owner = form if c is components[0] else f"{form} > {c.name}"
                out.append(Edge(path, c.start_line, owner, value.lower(), "event", f"{c.name}.{key} = {value}"))
    return out


def sql_tables(sql: str) -> list[tuple[str, str]]:
    """(kind, table) pairs of a piece of SQL, writes first. Empty when the text does not look like SQL."""
    if not SQL_HINT.search(sql):
        return []
    found: dict[tuple[str, str], None] = {}
    for m in SQL_WRITES.finditer(sql):
        found.setdefault(("sql_write", (m.group(1) or m.group(2)).lower()), None)
    for m in SQL_READS.finditer(sql):
        table = m.group(1).lower()
        if table not in NOT_TABLES and ("sql_write", table) not in found:
            found.setdefault(("sql_read", table), None)
    return [k for k in found if k[1] not in NOT_TABLES]


def sql_edges(chunk: Chunk, scopes: Scopes) -> list[Edge]:
    """Tables a chunk's SQL reads or writes. SQL is often built line by line (SQL.Add), so the
    chunk's literals are read as one text, and each table is placed on the first line that names it."""
    if not chunk.literals:
        return []
    lines = chunk.content.split("\n")
    body = lines[2:] if len(lines) > 2 else lines  # after the file and symbol header
    out = []
    for kind, table in sql_tables(" ".join(chunk.literals.split("\n"))):
        short = table.rsplit(".", 1)[-1]
        pattern = re.compile(rf"(?<![\w$]){re.escape(short)}(?![\w$])", re.I)
        offset = next((i for i, line in enumerate(body) if pattern.search(line)), 0)
        line = chunk.start_line + offset
        out.append(Edge(chunk.path, line, scopes.at(line), table, kind, body[offset].strip()[:MAX_LINE_CHARS] if body else ""))
    return out

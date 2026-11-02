"""Delphi and Lazarus forms (.dfm, .fmx, .lfm).

In legacy applications, forms hold business logic the source files do not show: SQL
statements and the event handlers bound to each component. A form is mostly geometry,
so this parser keeps what explains behavior and drops the rest, which also keeps
embedding costs down. Binary forms (TPF0) cannot be read here: convert them to text in
the Delphi IDE or with convert.exe.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field

OBJECT = re.compile(r"^\s*(object|inherited|inline)\s+(?:(\w+)\s*:\s*)?([\w.]+)", re.I)
PROPERTY = re.compile(r"^\s*([\w.]+)\s*=\s*(.*)$")
END = re.compile(r"^\s*end\s*$", re.I)
KEEP = re.compile(
    r"^(On[A-Z]\w*|Caption|Text|Hint|Title|Action|DataField|DataSource|DataSet|TableName|ProcedureName"
    r"|StoredProcName|CommandText|FieldName|DisplayLabel|MasterSource|MasterFields|IndexFieldNames"
    r"|SQL(\.Strings)?|Items(\.Strings)?|Lines(\.Strings)?)$"
)
CHAR_CODE = re.compile(r"#(\d+)")
BINARY_MAGIC = b"TPF0"
ROUTINE = re.compile(r"^[ \t]*(?:class[ \t]+)?(procedure|function|constructor|destructor|operator)[ \t]+"
                     r"([A-Za-z_][\w.]*(?:<[^>\n]*>)?(?:\.\w+)*)", re.I | re.M)
TYPE = re.compile(r"^[ \t]*(?:type[ \t]+)?([A-Za-z_]\w*)(?:<[^>\n]*>)?[ \t]*=[ \t]*(?:packed[ \t]+)?"
                  r"(class|record|interface|dispinterface|object)\b(?![ \t]*(?:of\b|;))", re.I | re.M)


@dataclass
class Component:
    name: str
    class_name: str
    depth: int
    start_line: int
    end_line: int = 0
    properties: list[tuple[str, str]] = field(default_factory=list)

    def describe(self) -> str:
        props = " ".join(f"{k}={v}" for k, v in self.properties)
        return f"{'  ' * self.depth}{self.name}: {self.class_name}" + (f" {props}" if props else "")


@dataclass(frozen=True)
class Declaration:
    """A declaration found by scanning text, for files the grammar cannot parse."""

    line: int
    owners: tuple[str, ...]
    name: str
    node_type: str
    text: str


def scan_declarations(text: str) -> list[Declaration]:
    """Routines and types in file order. Conditional defines break the grammar on real units, not this."""
    starts = [0, *(m.end() for m in re.finditer("\n", text))]
    found: list[tuple[int, Declaration]] = []
    for m in ROUTINE.finditer(text):
        parts = re.sub(r"<[^>]*>", "", m.group(2)).split(".")
        line = bisect_right(starts, m.start())
        found.append((m.start(), Declaration(line, tuple(parts[:-1]), parts[-1], "defProc", m.group().strip())))
    for m in TYPE.finditer(text):
        found.append((m.start(), Declaration(bisect_right(starts, m.start()), (), m.group(1), "declType", m.group().strip())))
    return [d for _, d in sorted(found, key=lambda item: item[0])]


def is_binary_form(src: bytes) -> bool:
    return src.startswith(BINARY_MAGIC)


def _value(raw: str) -> str:
    """'a'#39'b' + 'c' -> 'a'b'c'. Keeps the text readable for the embedding and the lexical index."""
    out = []
    for part in re.findall(r"'((?:[^']|'')*)'|(#\d+)", raw):
        if part[1]:
            out.append(chr(int(CHAR_CODE.match(part[1]).group(1))))
        else:
            out.append(part[0].replace("''", "'"))
    return "".join(out) if out else raw.strip()


def parse_form(text: str) -> list[Component]:
    """Components in file order. Multi-line values of kept properties are joined."""
    lines = text.splitlines()
    components: list[Component] = []
    stack: list[Component] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        match = OBJECT.match(line)
        if match:
            name = match.group(2) or match.group(3)
            current = Component(name, match.group(3), len(stack), i)
            components.append(current)
            stack.append(current)
            continue
        if END.match(line):
            if stack:
                stack.pop().end_line = i
            continue
        prop = PROPERTY.match(line)
        if prop is None or not stack:
            continue
        key, raw = prop.group(1), prop.group(2).strip()
        block, joiner = [], " "
        if raw[:1] in ("(", "<", "{"):  # lists, collections and binary data
            closing = {"(": ")", "<": ">", "{": "}"}[raw[0]]
            block.append(raw[1:])
            while not block[-1].rstrip().endswith(closing) and i < len(lines):
                block.append(lines[i].strip())
                i += 1
            block[-1] = block[-1].rstrip().removesuffix(closing)
        else:
            block.append(raw)
            joiner = ""  # 'long ' + 'text' is one string split across lines
            while block[-1].rstrip().endswith("+") and i < len(lines):
                block[-1] = block[-1].rstrip().removesuffix("+")
                block.append(lines[i].strip())
                i += 1
        if not KEEP.match(key) or raw[:1] in ("<", "{"):
            continue
        value = joiner.join(v for v in (_value(b) for b in block) if v)
        if value:
            stack[-1].properties.append((key.removesuffix(".Strings"), value))
    for component in stack:
        component.end_line = len(lines)
    return components

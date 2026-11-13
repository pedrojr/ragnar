"""Delphi and Lazarus forms (.dfm, .fmx, .lfm).

In legacy applications, forms hold business logic the source files do not show: SQL
statements and the event handlers bound to each component. A form is mostly geometry,
so this parser keeps what explains behavior and drops the rest, which also keeps
embedding costs down. Binary forms (TPF0, bare or inside a resource header) are turned
into the text format first, the way convert.exe does.
"""

from __future__ import annotations

import re
import struct
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


RESOURCE_MAGIC = b"\xff\x0a\x00"  # a form saved as a Windows RCDATA resource
DATA_BLOCK = re.compile(rb"=[ \t]*\{[0-9A-Fa-f\s]*\}")


def is_binary_form(src: bytes) -> bool:
    return src.startswith(BINARY_MAGIC) or (src.startswith(RESOURCE_MAGIC) and BINARY_MAGIC in src[:512])


def form_source(src: bytes, legacy_encoding: str = "cp1252") -> bytes:
    """The text form of a .dfm, without the hex blocks of images and binary properties.

    Embedded pictures make forms of several megabytes that hold a few kilobytes of SQL and
    event bindings. A binary form that cannot be read stays as it is.
    """
    if is_binary_form(src):
        try:
            return binary_form_to_text(src, legacy_encoding).encode()
        except (struct.error, IndexError, ValueError, RecursionError):  # RecursionError: nesting too deep
            return src
    if b"{" not in src:
        return src
    return DATA_BLOCK.sub(lambda m: b"= {" + b"\n" * m.group().count(b"\n") + b"}", src)  # lines keep their numbers


class _Reader:
    def __init__(self, data: bytes, encoding: str):
        self.data, self.pos, self.encoding = data, 0, encoding

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):  # a negative length would move back and loop forever
            raise ValueError("truncated form")
        out = self.data[self.pos : self.pos + n]
        self.pos += n
        return out

    def byte(self) -> int:
        return self.take(1)[0]

    def unpack(self, fmt: str):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def short_string(self) -> str:
        return self.take(self.byte()).decode(self.encoding, errors="replace")


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _read_value(r: _Reader, kind: int, indent: str) -> str:
    """One property value in the text syntax. Binary data becomes {}, which the parser skips."""
    if kind == 0:
        return ""
    if kind == 1:  # list
        items = []
        while (k := r.byte()) != 0:
            items.append(f"{indent}  {_read_value(r, k, indent + '  ')}")
        return "(\n" + "\n".join(items) + ")" if items else "()"
    if kind in (2, 3, 4, 19):
        return str(r.unpack({2: "<b", 3: "<h", 4: "<i", 19: "<q"}[kind]))
    if kind == 5:
        r.take(10)  # 80-bit extended: the value does not explain behavior
        return "0"
    if kind in (6, 7):  # string, identifier
        text = r.short_string()
        return _quote(text) if kind == 6 else text
    if kind in (8, 9, 13):
        return {8: "False", 9: "True", 13: "nil"}[kind]
    if kind == 10:  # binary
        r.take(r.unpack("<i"))
        return "{}"
    if kind == 11:  # set
        names = []
        while name := r.short_string():
            names.append(name)
        return "[" + ", ".join(names) + "]"
    if kind == 12:  # long string
        return _quote(r.take(r.unpack("<i")).decode(r.encoding, errors="replace"))
    if kind == 14:  # collection
        items = []
        while (k := r.byte()) != 0:
            order = ""
            if k in (2, 3, 4):
                order = f" [{_read_value(r, k, indent)}]"
                k = r.byte()
            if k != 1:
                raise ValueError("collection item without a property list")
            props = _read_properties(r, indent + "    ")
            items.append(f"{indent}  item{order}\n" + "".join(props) + f"{indent}  end")
        return "<\n" + "\n".join(items) + ">" if items else "<>"
    if kind in (15, 16, 17, 21):
        r.take({15: 4, 16: 8, 17: 8, 21: 8}[kind])
        return "0"
    if kind == 18:  # wide string
        return _quote(r.take(2 * r.unpack("<i")).decode("utf-16-le", errors="replace"))
    if kind == 20:  # UTF-8 string
        return _quote(r.take(r.unpack("<i")).decode("utf-8", errors="replace"))
    raise ValueError(f"unknown value type {kind}")


def _read_properties(r: _Reader, indent: str) -> list[str]:
    out = []
    while name := r.short_string():
        out.append(f"{indent}{name} = {_read_value(r, r.byte(), indent)}\n")
    return out


def _read_object(r: _Reader, indent: str) -> str:
    keyword, first = "object", r.data[r.pos]
    if first & 0xF0 == 0xF0:
        flags = r.byte() & 0x0F
        if flags & 2:  # child position
            _read_value(r, r.byte(), indent)
        keyword = "inline" if flags & 4 else "inherited" if flags & 1 else "object"
    class_name, name = r.short_string(), r.short_string()
    out = [f"{indent}{keyword} {name}: {class_name}\n" if name else f"{indent}{keyword} {class_name}\n"]
    out += _read_properties(r, indent + "  ")
    while r.data[r.pos] != 0:
        out.append(_read_object(r, indent + "  "))
    r.byte()
    out.append(f"{indent}end\n")
    return "".join(out)


def binary_form_to_text(src: bytes, legacy_encoding: str = "cp1252") -> str:
    """A binary form (TPF0 stream) in the text syntax that parse_form reads."""
    r = _Reader(src, legacy_encoding)
    r.pos = src.index(BINARY_MAGIC) + len(BINARY_MAGIC)
    return _read_object(r, "")


def _value(raw: str) -> str:
    """'a'#39'b' + 'c' -> 'a'b'c'. Keeps the text readable for the embedding and the lexical index."""
    out = []
    for part in re.findall(r"'((?:[^']|'')*)'|(#\d+)", raw):
        if part[1]:
            code = int(CHAR_CODE.match(part[1]).group(1))
            if code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF:  # no code point, or a lone surrogate: drop it
                out.append(chr(code))
        else:
            out.append(part[0].replace("''", "'"))
    return "".join(out) if out else raw.strip()


SHOWN = ("Caption", "Hint", "Text", "Title", "DisplayLabel")


def event_bindings(components: list[Component]) -> dict[str, str]:
    """Handler name (lowercase) -> words of the components that fire it.

    "btnGravarClick" learns that a TButton captioned "Gravar pedido" calls it. The handler is in the .pas
    and the words a person uses are in the .dfm; this joins them.
    """
    out: dict[str, list[str]] = {}
    for c in components[1:]:  # the form's own events (FormCreate) would carry the words of the whole screen
        shown = " ".join(v for k, v in c.properties if k in SHOWN)
        for key, handler in c.properties:
            if key.startswith("On") and re.fullmatch(r"\w+", handler):
                out.setdefault(handler.lower(), []).append(f"{c.name} {c.class_name} {key} {shown}".strip())
    return {h: " ".join(dict.fromkeys(parts)) for h, parts in out.items()}


def parse_form(text: str) -> list[Component]:
    """Components in file order. Multi-line values of kept properties are joined."""
    lines = [line.rstrip("\r") for line in text.split("\n")]  # line numbers as editors count them
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

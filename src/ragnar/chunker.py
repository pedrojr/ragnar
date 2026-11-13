"""Chunking: AST (cAST) for languages with a grammar, sections for Markdown, components for
Delphi forms, and line windows for everything else.

cAST: a node that fits the budget becomes a chunk; otherwise it splits into its children and
neighbors merge while they fit. Size is measured in non-whitespace characters.
"""

from __future__ import annotations

import codecs
import re
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass, field
from itertools import accumulate
from pathlib import PurePosixPath

from tree_sitter import Node, Parser, Tree

from . import delphi
from .config import Index, Settings, matches_path
from .delphi import Declaration
from .languages import (
    BY_NAME,
    DOC_EXTENSIONS,
    FORM_EXTENSIONS,
    Language,
    grammar_for,
    is_agent_note,
    is_denied,
    is_generated,
    is_test,
    language_for,
)


@dataclass(frozen=True)
class Chunk:
    path: str
    symbol: str
    language: str
    kind: str  # code | test | doc | summary
    start_line: int
    end_line: int
    content: str  # header + snippet
    search_text: str  # content + split identifiers (lexical search)
    literals: str = ""  # string literals of the snippet: messages, captions and SQL, weighed above the body


@dataclass(frozen=True)
class Symbol:
    path: str
    symbol: str  # qualified name: module > type > method
    name: str
    node_type: str
    kind: str
    start_line: int
    end_line: int
    signature: str  # first line of the declaration


def nws(b: bytes) -> int:
    return len(re.sub(rb"\s", b"", b))


def split_identifiers(text: str) -> str:
    """getUserById -> 'getUserById get User By Id' for the lexical search."""
    out = []
    for w in re.findall(r"[^\W\d_][^\W_]*", text):
        out.append(w)
        if w.isascii():  # the case split below would cut accented words apart
            out += re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", w)
        elif (plain := unaccent(w)) != w:  # "configuração" in a comment, "Configuracao" in a name
            out.append(plain)
    return " ".join(out)


def unaccent(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize_source(src: bytes, legacy_encoding: str = "cp1252") -> bytes | None:
    """UTF-8 bytes of a text file, or None if it is binary.

    Legacy code is often UTF-16 or a single-byte encoding. Without this step, UTF-16 looks
    binary and accented text turns into replacement characters.
    """
    if src.startswith(codecs.BOM_UTF8):
        src = src[len(codecs.BOM_UTF8):]
    for bom, encoding in ((codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")):
        if src.startswith(bom):
            return src[len(bom):].decode(encoding, errors="replace").encode()
    if bytes(1) in src[:8000]:
        return None
    try:
        src.decode()
        return src
    except UnicodeDecodeError:
        return src.decode(legacy_encoding, errors="replace").encode()


WHITESPACE = frozenset(b" \t\r\n\f\v")
OVERSIZE = 1.5  # a span above budget * OVERSIZE is split: one data table must not eat a whole token budget


def lines_of(text: str) -> list[str]:
    """Lines with their ends, split on \\n only: str.splitlines also splits on form feeds and other
    separators, so its line numbers drift from the ones tree-sitter and editors report."""
    parts = text.split("\n")
    return [p + "\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def size_index(src: bytes) -> list[int]:
    """Non-whitespace bytes before each offset, so sizing a span costs one subtraction."""
    return [0, *accumulate(b not in WHITESPACE for b in src)]


def cast_chunks(node: Node, src: bytes, budget: int, sizes: list[int] | None = None) -> list[tuple[int, int]]:
    sizes = size_index(src) if sizes is None else sizes
    if sizes[node.end_byte] - sizes[node.start_byte] <= budget or not node.children:
        return [(node.start_byte, node.end_byte)]
    spans: list[tuple[int, int]] = []
    cur: tuple[int, int] | None = None
    comment_start: int | None = None  # leading comments travel with the declaration after them
    for child in node.children:
        for s, e in cast_chunks(child, src, budget, sizes):
            if cur and sizes[e] - sizes[cur[0]] <= budget:
                cur = (cur[0], e)
            else:
                if cur and comment_start is not None and comment_start > cur[0]:
                    spans.append((cur[0], comment_start))
                    cur = (comment_start, e)
                    if sizes[cur[1]] - sizes[cur[0]] > budget:
                        spans.append((cur[0], s))
                        cur = (s, e)
                else:
                    if cur:
                        spans.append(cur)
                    cur = (s, e)
        if "comment" in child.type:
            comment_start = comment_start if comment_start is not None else child.start_byte
        else:
            comment_start = None
    if cur:
        spans.append(cur)
    return spans


def bounded(spans: list[tuple[int, int]], src: bytes, budget: int, sizes: list[int]) -> list[tuple[int, int]]:
    """Cuts oversized spans at line ends. A leaf node, such as a long literal, reaches here whole."""
    out: list[tuple[int, int]] = []
    for s, e in spans:
        while sizes[e] - sizes[s] > budget * OVERSIZE:
            limit = bisect_right(sizes, sizes[s] + budget, s, e + 1) - 1
            cut = src.rfind(b"\n", s + 1, limit) + 1 or limit
            while cut < e and src[cut] & 0xC0 == 0x80:  # never inside a UTF-8 sequence
                cut += 1
            out.append((s, cut))
            s = cut
        if s < e:
            out.append((s, e))
    return out


# Single quotes delimit strings in these languages; elsewhere they are characters, or apostrophes in comments
SINGLE_QUOTED = frozenset({"delphi", "sql", "python", "javascript", "typescript", "php", "ruby", "bash", "scala"})
DOUBLE_QUOTED_STRING = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
SINGLE_QUOTED_STRING = re.compile(r"'((?:[^'\n]|'')*)'")
MAX_LITERAL_CHARS = 4000


def extract_literals(text: str, language: str) -> str:
    """String literals worth searching, one per line, in order and without repeats.

    The message a user reads ("Cliente sem limite de crédito") lives in a string. Ranked as part of
    the body, it weighs as little as any other word; in its own field, a symptom finds its code.
    """
    if language in ("markdown", "text"):
        return ""
    found: dict[str, None] = {}
    if language not in ("delphi", "sql"):
        for m in DOUBLE_QUOTED_STRING.finditer(text):
            found.setdefault(m.group(1).strip(), None)
    if language in SINGLE_QUOTED:
        for m in SINGLE_QUOTED_STRING.finditer(text):
            found.setdefault(m.group(1).replace("''", "'").strip(), None)
    kept = [v for v in found if len(v) >= 3 and sum(c.isalpha() for c in v) >= 3]
    return "\n".join(kept)[:MAX_LITERAL_CHARS]


def _make(lang_comment: str, path: str, symbol: str, language: str, kind: str, start: int, end: int, text: str,
          literals: str | None = None) -> Chunk:
    content = f"{lang_comment} file: {path}\n{lang_comment} symbol: {symbol}\n{text}"
    literals = extract_literals(text, language) if literals is None else literals
    return Chunk(path, symbol, language, kind, start, end, content, content + "\n" + split_identifiers(content),
                 literals)


def _qualified(lang: Language, node: Node) -> list[str]:
    owners, name = lang.name_of(node)
    return [p for p in [*owners, name] if p]


def enclosing_symbol(lang: Language, root: Node, s: int, e: int) -> str:
    parts: list[list[str]] = []
    n = root.named_descendant_for_byte_range(s, max(s, e - 1))
    while n is not None:
        if n.type in lang.scopes or (n.type in lang.declarations and lang.accept(n)):
            if names := _qualified(lang, n):
                parts.append(names)
        n = n.parent
    return " > ".join(p for names in reversed(parts) for p in names)


def declared_in(lang: Language, root: Node, s: int, e: int, limit: int = 3,
                cache: dict[int, tuple[list[Node], list[int]]] | None = None) -> str:
    """Top declarations inside a span. A chunk that holds a whole file has no enclosing symbol.

    cache keeps each node's children with their end offsets across the chunks of one file: a data literal
    with 100k elements is cut into hundreds of chunks, and listing every child for each of them took minutes.
    """
    cache = {} if cache is None else cache

    def inside(node: Node) -> list[Node]:
        if node.id not in cache:
            kids = node.children
            cache[node.id] = (kids, [k.end_byte for k in kids])
        kids, ends = cache[node.id]
        out = []
        for child in kids[bisect_right(ends, s):]:  # the first child that ends after s
            if child.start_byte >= e:
                break
            out.append(child)
        return out

    names: list[str] = []
    stack = list(reversed(inside(root)))
    while stack and len(names) < limit:
        node = stack.pop()
        if node.end_byte <= s or node.start_byte >= e:
            continue
        declared = (node.type in lang.scopes or node.type in lang.declarations) and lang.accept(node)
        if declared and (found := _qualified(lang, node)):
            names.append(".".join(found))
        else:
            stack.extend(reversed(inside(node)))
    return ", ".join(dict.fromkeys(names))


def form_literals(components: list[delphi.Component]) -> str:
    """Text a person sees or a query runs on a form: captions, hints, list items and SQL, not event bindings."""
    values = dict.fromkeys(v.strip() for c in components for k, v in c.properties if not k.startswith("On") and v.strip())
    return "\n".join(values)[:MAX_LITERAL_CHARS]


HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass
class Chunker:
    """Applies one set of index settings to the files of one repository."""

    index: Index = field(default_factory=Index)
    languages: tuple[str, ...] = ()
    _parsed: tuple[bytes, Tree] | None = field(default=None, repr=False)  # chunks and symbols share one parse

    @classmethod
    def from_settings(cls, settings: Settings) -> Chunker:
        return cls(settings.index, settings.project.languages)

    def eligible(self, path: str) -> bool:
        if matches_path(self.index.exclude, path):
            return False
        if matches_path(self.index.include, path):
            return True
        suffix = PurePosixPath(path).suffix.lower()
        if suffix in DOC_EXTENSIONS:
            return self.index.index_docs and not is_denied(None, path)
        lang = BY_NAME["delphi"] if suffix in FORM_EXTENSIONS else language_for(path)
        if lang is None or (self.languages and lang.name not in self.languages):
            return False
        if not self.index.index_tests and is_test(lang, path):
            return False
        return not is_denied(lang, path)

    def indexed_source(self, path: str, src: bytes) -> bytes:
        """Forms in text syntax, without their image data: the size limit applies to what gets indexed."""
        if PurePosixPath(path).suffix.lower() in FORM_EXTENSIONS:
            return delphi.form_source(src, self.index.legacy_encoding)
        return src

    def source_kind(self, path: str) -> str:
        """doc, form or code."""
        suffix = PurePosixPath(path).suffix.lower()
        return "doc" if suffix in DOC_EXTENSIONS else "form" if suffix in FORM_EXTENSIONS else "code"

    def readable_text(self, path: str, src: bytes) -> str | None:
        """The text that gets indexed, or None when the file stays out."""
        text = self._readable(path, self.indexed_source(path, src))
        return None if text is None else text.decode(errors="replace")

    def _readable(self, path: str, src: bytes) -> bytes | None:
        if self.skip_reason(path, src) is not None:
            return None
        return normalize_source(src, self.index.legacy_encoding)

    def skip_reason(self, path: str, src: bytes) -> str | None:
        """Why an eligible file stays out of the index. Runs report it: a silent skip looks like a search failure."""
        if not self.eligible(path):
            return "not eligible"
        src = self.indexed_source(path, src)
        if len(src) > self.index.max_file_kb * 1024:
            return "too large"
        if is_generated(src):
            return "generated"
        if not self.index.index_agent_notes and is_agent_note(path):
            return "agent note"
        if normalize_source(src, self.index.legacy_encoding) is None:
            return "binary"
        return None

    def chunk_file(self, path: str, src: bytes) -> list[Chunk]:
        """Chunks of the file, or an empty list if it must not be indexed."""
        src = self.indexed_source(path, src)
        text = self._readable(path, src)
        if text is None:
            return []
        suffix = PurePosixPath(path).suffix.lower()
        if suffix in DOC_EXTENSIONS:
            return self._markdown(path, text)
        if suffix in FORM_EXTENSIONS:
            return self._form(path, src, text)
        lang = language_for(path)
        grammar = grammar_for(lang, path) if lang else None
        if grammar is None:
            return self._window(path, text, lang)
        try:
            return self._code(path, text, lang, grammar)
        except RecursionError:  # a pathologically deep AST
            return self._window(path, text, lang)

    def _root(self, src: bytes, grammar) -> Node:
        if self._parsed is None or self._parsed[0] != src:
            self._parsed = (src, Parser(grammar).parse(src))
        return self._parsed[1].root_node

    def _code(self, path: str, src: bytes, lang: Language, grammar) -> list[Chunk]:
        root = self._root(src, grammar)
        declarations = lang.fallback(src.decode(errors="replace")) if lang.fallback and root.has_error else []
        if declarations:
            return self._sections(path, src, lang, root, declarations)
        kind = "test" if is_test(lang, path) else "code"
        module = lang.module_of(root, path)
        budget = self.index.chunk_budget
        sizes = size_index(src)
        lines = [m.start() for m in re.finditer(b"\n", src)]
        out, children = [], {}
        for s, e in bounded(cast_chunks(root, src, budget, sizes), src, budget, sizes):
            text = src[s:e].decode(errors="replace")
            if not text.strip():
                continue
            inside = enclosing_symbol(lang, root, s, e) or declared_in(lang, root, s, e, cache=children)
            symbol = " > ".join(p for p in (module, inside) if p)
            out.append(_make(lang.comment, path, symbol, lang.name, kind, bisect_right(lines, s - 1) + 1,
                             bisect_right(lines, e - 1) + 1, text))
        return out

    def _sections(self, path: str, src: bytes, lang: Language, root: Node,
                  declarations: list[Declaration]) -> list[Chunk]:
        """A file the grammar cannot parse: chunk boundaries follow the scanned declarations."""
        kind = "test" if is_test(lang, path) else "code"
        module = lang.module_of(root, path)
        budget = self.index.chunk_budget
        lines = src.split(b"\n")
        offsets = [0, *accumulate(len(line) + 1 for line in lines)]
        offsets[-1] = len(src)
        sizes = size_index(src)
        starts = sorted({d.line for d in declarations} | {1})
        by_line = {d.line: d for d in reversed(declarations)}
        spans: list[tuple[int, int]] = []  # first line, line after the last
        for first, after in zip(starts, [*starts[1:], len(lines) + 1]):
            if spans and sizes[offsets[after - 1]] - sizes[offsets[spans[-1][0] - 1]] <= budget:
                spans[-1] = (spans[-1][0], after)
            else:
                spans.append((first, after))
        out = []
        for first, after in spans:
            inside = starts[bisect_right(starts, first) - 1 :]
            owner = next((by_line[n] for n in inside if first <= n < after and n in by_line), None)
            named = ".".join((*owner.owners, owner.name)) if owner else ""
            symbol = " > ".join(p for p in (module, named) if p)
            for s, e in bounded([(offsets[first - 1], offsets[after - 1])], src, budget, sizes):
                text = src[s:e].decode(errors="replace")
                if text.strip():
                    out.append(_make(lang.comment, path, symbol, lang.name, kind, bisect_right(offsets, s),
                                     bisect_right(offsets, max(s, e - 1)), text))
        return out

    def _pieces(self, text: str, first: int, last: int) -> list[tuple[str, int, int]]:
        """The text with its first and last line, cut at line ends when it is over budget * OVERSIZE.

        A window or a section can hold one huge line, such as a SQL dump with an extended INSERT: as one
        chunk it overflows Postgres' tsvector limit (1 MB) and the input limit of hosted embedders.
        """
        data = text.encode()
        budget = self.index.chunk_budget
        if nws(data) <= budget * OVERSIZE:
            return [(text, first, last)]
        sizes = size_index(data)
        return [(data[s:e].decode(errors="replace"), first + data.count(b"\n", 0, s),
                 first + data.count(b"\n", 0, max(s, e - 1))) for s, e in bounded([(0, len(data))], data, budget, sizes)]

    def _window(self, path: str, src: bytes, lang: Language | None) -> list[Chunk]:
        lines = lines_of(src.decode(errors="replace"))
        name = PurePosixPath(path).name
        kind = "test" if is_test(lang, path) else "code"
        comment, language = (lang.comment, lang.name) if lang else ("#", "text")
        size, step = self.index.window_lines, self.index.window_lines - self.index.window_overlap
        out, seen = [], set()
        for start in range(0, max(len(lines) - self.index.window_overlap, 1), step):
            text = "".join(lines[start : start + size])
            for piece, first, last in self._pieces(text, start + 1, min(start + size, len(lines))):
                # the overlap would repeat every piece of a long line that sits in it
                if piece.strip() and (first, last, piece) not in seen:
                    seen.add((first, last, piece))
                    out.append(_make(comment, path, name, language, kind, first, last, piece))
        return out

    def _form(self, path: str, raw: bytes, text: bytes) -> list[Chunk]:
        if delphi.is_binary_form(raw):
            return []
        components = delphi.parse_form(text.decode(errors="replace"))
        if not components:
            return []
        form = components[0].name
        kind = "test" if is_test(BY_NAME["delphi"], path) else "code"
        groups: list[list[delphi.Component]] = [[]]
        for c in components:
            if groups[-1] and nws("\n".join(x.describe() for x in [*groups[-1], c]).encode()) > self.index.chunk_budget:
                groups.append([])
            groups[-1].append(c)
        out = []
        for g in groups:  # one component can hold a large SQL: its description is cut like any other text
            literals = form_literals(g)
            pieces = self._pieces("\n".join(c.describe() for c in g) + "\n", g[0].start_line, g[-1].end_line)
            out += [_make("//", path, form, "delphi", kind, g[0].start_line, g[-1].end_line, piece, literals)
                    for piece, _, _ in pieces if piece.strip()]
        return out

    def _markdown(self, path: str, src: bytes) -> list[Chunk]:
        """One section per heading; small neighboring sections merge up to the budget."""
        lines = lines_of(src.decode(errors="replace"))
        sections: list[tuple[str, int, int]] = []  # (heading trail, first line, last line)
        trail: list[tuple[int, str]] = []
        start, fence = 1, False
        for i, line in enumerate(lines, 1):
            if line.lstrip().startswith(("```", "~~~")):
                fence = not fence
            m = None if fence else HEADING.match(line)
            if m and i > start:
                sections.append((" > ".join(t for _, t in trail), start, i - 1))
                start = i
            if m:
                level = len(m.group(1))
                trail = [(lv, t) for lv, t in trail if lv < level] + [(level, m.group(2))]
        sections.append((" > ".join(t for _, t in trail), start, len(lines)))

        merged, cur = [], None
        for title, s, e in sections:
            if cur and nws("".join(lines[cur[1] - 1 : e]).encode()) <= self.index.chunk_budget:
                cur = (cur[0], cur[1], e)
                continue
            if cur:
                merged.append(cur)
            cur = (title, s, e)
        if cur:
            merged.append(cur)

        doc = PurePosixPath(path).name
        chunks = []
        for title, s, e in merged:
            symbol = f"{doc} > {title}" if title else doc
            for piece, first, last in self._pieces("".join(lines[s - 1 : e]), s, e):
                if piece.strip():
                    chunks.append(_make("//", path, symbol, "markdown", "doc", first, last, piece))
        return chunks

    def extract_symbols(self, path: str, src: bytes) -> list[Symbol]:
        """Every named declaration (class, type, function, method), even the ones sharing a chunk."""
        src = self.indexed_source(path, src)
        text = self._readable(path, src)
        if text is None:
            return []
        if PurePosixPath(path).suffix.lower() in FORM_EXTENSIONS:
            return self._form_symbols(path, src, text)
        lang = language_for(path)
        grammar = grammar_for(lang, path) if lang else None
        if grammar is None:
            return []
        root = self._root(text, grammar)
        kind = "test" if is_test(lang, path) else "code"
        module = lang.module_of(root, path)
        out: list[Symbol] = []

        def walk(node: Node, trail: list[str]) -> None:
            for child in node.children:
                if child.type in lang.scopes:
                    walk(child, [*trail, *_qualified(lang, child)])
                    continue
                names = _qualified(lang, child) if child.type in lang.declarations and lang.accept(child) else []
                if not names:
                    walk(child, trail)
                    continue
                parts = [p for p in [module, *trail, *names] if p]
                signature = child.text.decode(errors="replace").splitlines()[0].strip() if child.text else ""
                out.append(Symbol(path, " > ".join(parts), names[-1], child.type, kind,
                                  child.start_point[0] + 1, child.end_point[0] + 1, signature[:200]))
                walk(child, [*trail, *names])

        try:
            walk(root, [])
        except RecursionError:  # a pathologically deep AST: keep what the walk found
            pass
        if lang.fallback and root.has_error:
            out += self._scanned_symbols(path, text, lang, module, kind, {s.start_line for s in out})
        return out

    def _scanned_symbols(self, path: str, text: bytes, lang: Language, module: str, kind: str,
                         known: set[int]) -> list[Symbol]:
        declarations = lang.fallback(text.decode(errors="replace"))
        lines = text.count(b"\n") + 1
        out = []
        for d, after in zip(declarations, [*[x.line for x in declarations[1:]], lines + 1]):
            if d.line not in known:
                parts = [p for p in (module, *d.owners, d.name) if p]
                out.append(Symbol(path, " > ".join(parts), d.name, d.node_type, kind, d.line, max(d.line, after - 1),
                                  d.text[:200]))
        return out

    def _form_symbols(self, path: str, raw: bytes, text: bytes) -> list[Symbol]:
        if delphi.is_binary_form(raw):
            return []
        components = delphi.parse_form(text.decode(errors="replace"))
        kind = "test" if is_test(BY_NAME["delphi"], path) else "code"
        form = components[0].name if components else ""
        return [Symbol(path, form if c.depth == 0 else f"{form} > {c.name}", c.name, "dfm_object", kind,
                       c.start_line, c.end_line, c.describe().strip()[:200]) for c in components]

"""Audits what the chunker keeps and loses in each cloned repository. Needs no database or embedder.

Run: uv run python benchmark/audit_corpus.py [repo ...]
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path, PurePosixPath
from statistics import median

from tree_sitter import Parser

from ragnar.chunker import Chunker, nws, normalize_source
from ragnar.config import load_settings
from ragnar.indexer import git, list_files, read_blobs
from ragnar.languages import BY_EXTENSION, FORM_EXTENSIONS, grammar_for, is_denied, is_generated, language_for

ROOT = Path(__file__).parent
SOURCE = set(BY_EXTENSION) | set(FORM_EXTENSIONS)


def audit(repo_dir: Path) -> dict:
    settings = load_settings(ROOT)
    chunker = Chunker.from_settings(settings)
    head = git(repo_dir, "rev-parse", "HEAD").decode().strip()
    paths = list_files(repo_dir, head)
    source = [p for p in paths if PurePosixPath(p).suffix.lower() in SOURCE]
    blobs = read_blobs(repo_dir, head, source)
    skipped: dict[str, list[str]] = {"denied": [], "too_big": [], "generated": [], "binary": []}
    chunks, symbols = [], 0
    limit = settings.index.max_file_kb * 1024
    t0 = time.perf_counter()
    for path, src in blobs.items():
        lang = language_for(path)
        if is_denied(lang, path):
            skipped["denied"].append(path)
        elif len(src) > limit:
            skipped["too_big"].append(path)
        elif is_generated(src):
            skipped["generated"].append(path)
        elif normalize_source(src, settings.index.legacy_encoding) is None:
            skipped["binary"].append(path)
        else:
            chunks += chunker.chunk_file(path, src)
            symbols += len(chunker.extract_symbols(path, src))
    seconds = time.perf_counter() - t0
    sizes = [nws(c.content.encode()) for c in chunks]
    budget = settings.index.chunk_budget
    errors = 0
    parsed = 0
    for path, src in blobs.items():
        lang = language_for(path)
        grammar = grammar_for(lang, path) if lang and path.lower().endswith(tuple(lang.extensions)) else None
        text = normalize_source(src, settings.index.legacy_encoding)
        if grammar is None or text is None or len(src) > limit:
            continue
        parsed += 1
        errors += Parser(grammar).parse(text).root_node.has_error
    return {
        "repo": repo_dir.name,
        "files": len(paths),
        "source_files": len(source),
        "languages": Counter(c.language for c in chunks).most_common(),
        "skipped": {k: len(v) for k, v in skipped.items()},
        "skipped_bytes": {k: sum(len(blobs[p]) for p in v) for k, v in skipped.items()},
        "skipped_sample": {k: v[:12] for k, v in skipped.items()},
        "chunks": len(chunks),
        "symbols": symbols,
        "chunk_nws_median": median(sizes) if sizes else 0,
        "chunk_nws_max": max(sizes, default=0),
        "oversized_chunks": sum(s > budget * 1.5 for s in sizes),
        "chunks_without_symbol": sum(not c.symbol for c in chunks),
        "files_with_parse_errors": f"{errors}/{parsed}",
        "seconds": round(seconds, 1),
    }


def main() -> None:
    names = sys.argv[1:] or sorted(p.name for p in (ROOT / "repos").iterdir())
    for name in names:
        print(json.dumps(audit(ROOT / "repos" / name), indent=1))


if __name__ == "__main__":
    main()

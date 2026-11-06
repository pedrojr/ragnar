# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

[Português (Brasil)](CHANGELOG.pt-BR.md)

## [Unreleased]

### Added

- Claude Code plugin at the repository root, with the `ragnarize`, `search`, `index`, `evaluate`, `tune`, `maintain` and `infra` skills.
- `ragnar.toml` settings with presets, validation and per-layer overrides.
- Language profiles for Python, JavaScript, TypeScript, Java, Kotlin, C#, Go, Rust, C, C++, PHP, Ruby, Swift, Scala, Bash, SQL and Delphi.
- Delphi support: UTF-16 and CP-1252 sources, unit and method symbols, and form parsing.
- Configurable retrieval: weighted fusion, symbol boost, reranking, multi-query, HyDE, contextual retrieval, diversity, neighbor chunks and a token budget.
- `ragnar init` and `ragnar doctor`, including a managed `.gitignore` block.
- `ragnar sync` and an index worker that follows repository branches.
- `ragnar eval`, with variant comparison and context token estimates.
- Reference infrastructure in `example/`, with CI templates.
- Documentation in English and Brazilian Portuguese.
- `find_references`, `file_outline` and `read_code` MCP tools. `find_symbol` takes qualified names and matches any part of a name.
- Whole-file list in the fusion (`retrieval.file_weight`), and references for hits beyond the token budget (`retrieval.reference_hits`).
- `index.secret_action`, to leave files with findings out instead of stopping the run. Server scope only.
- `benchmark/`: before and after comparison on pinned open source projects in Delphi, Java, C# and Go.
- Binary Delphi forms (`TPF0`) are read as text, and image data in text forms stays out of chunks and the size limit.
- `exclude` per repository in `repos.toml`.
- An interrupted first index resumes from its embedding checkpoint (migration 006).
- `RAGNAR_BENCH_SUITE` and `RAGNAR_BENCH_DSN` run the benchmark on a private suite, and `benchmark/summarize_suite.py` publishes its aggregates only.
- `RAGNAR_EMBED_THREADS` and `RAGNAR_EMBED_MEMORY` in the example infrastructure.

### Changed

- **Breaking:** renamed the project from `raglib` to Ragnar, including the CLI, environment variables, database, roles and images. Indexes built by the prototype must be rebuilt.
- The secret scan runs on normalized text, so it covers UTF-16 sources.
- `index.max_file_kb` defaults to 2048, and runs report what they leave out and why.
- Lexical ranking weighs terms by rarity, drops English and Portuguese stopwords, and matches by light stemming.
- Portuguese suffixes and accents no longer split a word from its variants, in queries and in identifiers.
- The whole-file list ranks files by BM25 over chunk counts, with a length penalty. `retrieval.file_weight` defaults to 2.0 and `retrieval.rrf_k` to 30.
- The secret scan reads forms as indexed, so image data no longer raises findings.
- History questions drop ticket prefixes and commit types from their subjects.
- `balanced` now returns 5 hits within 2,000 tokens, and `economy` 3 hits within 1,000: in real agent sessions, 4,000 tokens cost more context than grep.
- The agent note and the MCP instructions tell agents when to start with the index. With the previous wording, agents kept using grep.
- Reindexing reuses the vectors of unchanged chunks, and texts reach the embedder sorted by length.
- Migration 005 rewrites `code_chunk`, which rebuilds the vector index. Expect it to take a while on large indexes, and give parallel builds enough shared memory.

### Fixed

- Accented words stay whole in the lexical index.
- Comments stay with the declaration that follows them.
- Folders named `build`, `target` or `Win32` no longer hide real sources.
- Delphi units the grammar cannot parse keep their routine boundaries and symbols.
- Oversized literals split into chunks that fit the budget.
- Chunks holding a whole file now name its declarations, and C# files their namespace.
- A 4xx from the embedder is a clear error, and retries back off.

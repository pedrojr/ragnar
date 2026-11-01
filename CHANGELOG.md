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

### Changed

- **Breaking:** renamed the project from `raglib` to Ragnar, including the CLI, environment variables, database, roles and images. Indexes built by the prototype must be rebuilt.
- The secret scan runs on normalized text, so it covers UTF-16 sources.

### Fixed

- Accented words stay whole in the lexical index.
- Comments stay with the declaration that follows them.

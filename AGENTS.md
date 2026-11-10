# Agent instructions

Guidance for AI agents working on this repository. Humans: see [CONTRIBUTING.md](CONTRIBUTING.md).

## Commands

```bash
uv sync --extra embed      # install
uv run pytest              # unit tests; integration tests skip without a database
uv run ruff check .        # lint
claude plugin validate .   # plugin manifests
```

## Rules

- Code, comments, commits and error messages are in English.
- Documentation goes in pairs. When you change `docs/x.md`, change `docs/pt-BR/x.md`. When you change a root or `example/` document, change its `.pt-BR.md`. Skills are in English only.
- Commits follow Conventional Commits.
- Keep `version` equal in `pyproject.toml`, `src/ragnar/__init__.py` and `.claude-plugin/plugin.json`.
- Keep the block in `hooks/session-start.sh` equal to `ragnar.project.IGNORED`. A test checks both of these.

## Boundaries

- The MCP server stays read-only. It never receives `RAGNAR_DSN_WRITE`.
- A repository's `.ragnar/config.toml` must never reach endpoints, keys or `secret_scan`. See `REPO_OWNED` in `src/ragnar/config.py`.
- Never index the working tree: the indexer reads commits.
- A new language needs a profile in `languages.py` and a case in `tests/test_languages.py`.
- Bump `CHUNKER_VERSION` in `config.py` when chunk boundaries change, so indexes rebuild.
- A schema change is a new file in `src/ragnar/migrations/`. Never edit an applied migration after a release.

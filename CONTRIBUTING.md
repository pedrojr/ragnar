# Contributing

Thanks for helping. This guide covers what you need to get a change merged.

[Português (Brasil)](CONTRIBUTING.pt-BR.md)

## Before you start

Open an issue for anything beyond a small fix, so we can agree on the approach first. Good first contributions: a new language profile, retrieval evidence from your own codebase, and fixes to the docs.

For security problems, follow [SECURITY.md](SECURITY.md) and do not open a public issue.

## Setup

```bash
git clone https://github.com/pedrojr/ragnar
cd ragnar
uv sync --extra embed
uv run pytest
```

Integration tests need Postgres with pgvector. Start the example, then point the tests at it:

```bash
[ -f example/.env ] || uv run ragnar init-env --dir example   # passwords for a fresh clone
cd example && docker compose up -d db-primary && cd ..
set -a; . example/.env; set +a
RAGNAR_TEST_ADMIN_DSN="postgresql://postgres:$POSTGRES_PASSWORD@127.0.0.1:15432/postgres" uv run pytest
```

They create and drop a `ragnar_test` database, and they never touch `ragnar`.

To try the plugin from your clone:

```bash
claude --plugin-dir .
claude plugin validate .
```

## Rules for a change

- **Language.** Code, comments, commit messages and error messages are in English.
- **Documentation goes in pairs.** A page in `docs/` needs its twin in `docs/pt-BR/`, and a root or example document needs its `.pt-BR.md`. Update both in the same pull request. A test enforces that each pair exists.
- **Tests.** New behavior needs a test. `uv run pytest` must pass.
- **Style.** `uv run ruff check .` must pass. Match the code around your change.
- **Retrieval changes need evidence.** Include a `ragnar eval` report. Recall@10 must not drop by more than 0.02.
- **Secrets.** Never add real tokens or passwords, even in tests.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/):

```text
feat(languages): add Elixir profile
fix(indexer): handle file names with spaces
docs: explain binary Delphi forms
```

| Type | Use |
| --- | --- |
| `feat` | New behavior |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `refactor` | Code change with the same behavior |
| `test` | Tests only |
| `chore`, `ci` | Tooling |

Common scopes: `config`, `languages`, `retrieval`, `indexer`, `sync`, `eval`, `mcp`, `plugin`, `example`. Mark breaking changes with `!` and a `BREAKING CHANGE:` footer.

## Pull requests

1. Branch from `main`.
2. Keep each pull request to one purpose.
3. Fill in the template.
4. Add your change to `[Unreleased]` in `CHANGELOG.md` and `CHANGELOG.pt-BR.md`.

## Releases

Ragnar follows [Semantic Versioning](https://semver.org/). To release:

1. Set the version in `pyproject.toml`, `src/ragnar/__init__.py` and `.claude-plugin/plugin.json`. A test keeps them equal.
2. Move `[Unreleased]` to the new version in both changelogs.
3. Tag `vX.Y.Z` and publish a GitHub release.

---
name: ragnarize
description: Sets a project up for Ragnar. Detects languages and the default branch, writes .ragnar/config.toml, creates .rag/, protects .gitignore, connects the MCP server and plans the first index. Use when the user says "ragnarize this project", "ragnarizar", "set up Ragnar", "install the code RAG here", or opens Ragnar in a project with no ragnar.toml.
---

# Ragnarize a project

Goal: leave the project ready to use its code index, with no secret in the repository.

## Inputs

- Project folder: the working directory, unless the user names another.
- Branch the index follows: confirm the detected default with the user.
- Preset: `economy`, `balanced` or `precise`. Default to `balanced`.

## Steps

1. Check the prerequisites with `ragnar --version`. If `uv` is missing, send the user to https://docs.astral.sh/uv/getting-started/installation/ and stop.
2. Ragnarize:
   ```bash
   ragnar init --preset <preset> --branch <branch> --agents-md
   ```
   The command writes `.ragnar/config.toml`, creates `.rag/` with `.env` and `config.local.toml` templates, adds the managed block to `.gitignore`, and appends a search note to `AGENTS.md`. Drop `--agents-md` if the user does not want that file changed.
3. Show the detected languages. Ask the user to confirm them. Edit `project.languages` and `index.exclude` in `.ragnar/config.toml` if vendored or generated code slipped in.
4. For a Delphi project, tell the user about binary forms: forms stored as binary (`TPF0`) cannot be indexed. Run `git grep -lI "^object " -- '*.dfm'` and compare against all `.dfm` files. Suggest converting the rest to text in the IDE.
5. Connect the MCP server. Call the `list_repos` tool.
   - It answers: go to step 6.
   - It fails: the server is unreachable or the token is wrong. Offer the `infra` skill if the user has no server yet. Otherwise tell the user to set `mcp_url` and `mcp_token` in `/plugin` config.
6. Show the files to commit: `.ragnar/config.toml`, `.gitignore`, and `AGENTS.md`. Never commit `.rag/`. Commit only if the user asks. The worker reads `.ragnar/config.toml` from the tracked branch, so `[index]` settings take effect after they reach that branch.
7. Hand over to the `index` skill for the first index.

## Rules

- Never write secrets into `.ragnar/config.toml`. It is committed. API keys stay in environment variables, named through `api_key_env`. On the user's machine they go in `.rag/.env`, which git ignores.
- Personal overrides go in `.rag/config.local.toml`, which git ignores.
- Re-running `ragnar init` is safe. It never overwrites files in `.rag/`, and it keeps an existing `.ragnar/config.toml` unless you pass `--force`, so never pass `--force` without the user's consent.

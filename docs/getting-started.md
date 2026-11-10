# Getting started

This guide takes a project from nothing to an agent searching its index. Plan about 30 minutes, plus the first indexing run.

[Português (Brasil)](pt-BR/getting-started.md)

## Requirements

| Need | Where | Notes |
| --- | --- | --- |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | Your machine | Runs the `ragnar` command. It fetches Python 3.11 or newer if you have none |
| git | Your machine | Ragnar reads commits, never the working tree |
| Docker with Compose | The server machine | Runs Postgres with pgvector, the embedder, the MCP server and the worker. Start with one machine |
| About 6 GB of free memory | The server machine | The bundled embedder uses up to 3 GB while indexing |
| [gitleaks](https://github.com/gitleaks/gitleaks) | Your machine, only to index from a local clone | The worker image already has it. Without it, `ragnar index` runs gitleaks through Docker |

Optional:

- An embedding API instead of the bundled embedder. It needs a key, and it receives your source code.
- A chat model endpoint (`[llm]`) for file summaries, multi-query, HyDE and contextual retrieval. All are off by default.
- A reranker endpoint.
- `uv sync --extra embed`, which installs fastembed, the library behind the bundled embedder. The Docker image already includes it. You need it only to run `ragnar embedder` outside Docker.

Embeddings are always on, and summaries are optional. [Embeddings and summaries](configuration.md#embeddings-and-summaries) explains the difference.

## 1. Bring up the server

Follow [`example/README.md`](../example/README.md), or ask your agent:

> Use the Ragnar infra skill to bring up the server.

When it worked:

- `docker compose ps` shows every service healthy.
- `docker compose logs -f indexer` ends the first build with a line such as `myapp: full at 3f2a9c1b7d10: 412 files, 1830 chunks, 1902.4s`.
- `./scripts/smoke.sh myapp` passes its checks.

You now have an MCP URL (`http://127.0.0.1:8080/mcp` by default), a token (`RAGNAR_MCP_TOKEN` in `example/.env`) and a worker that follows the branches in `example/repos.toml`.

## 2. Connect the plugin

In Claude Code:

```text
/plugin install ragnar --marketplace pedrojr/ragnar
```

Claude Code asks for the MCP URL and token. Enter the values from step 1. You can change them later in `/plugin`, under Ragnar's options.

Using another agent? Follow [Other agents](agents.md), then continue here.

When it worked, asking the agent to call `list_repos` returns your repository and its indexed commit.

## 3. Ragnarize the project

Inside your project:

```text
/ragnar:ragnarize
```

The skill runs `ragnar init`, which detects the languages and the default branch, creates the files below, and adds a managed block to `.gitignore`.

| Path | Commit it? | Content |
| --- | --- | --- |
| `.ragnar/config.toml` | Yes | Project name, branch, preset, languages, and the `[index]` keys a repository may set, commented out with their defaults |
| `.ragnar/eval/golden.jsonl` | Yes | Evaluation questions. Created by `/ragnar:evaluate` |
| `.gitignore` | Yes | The managed block that ignores `.rag/` |
| `.rag/.env` | Never | Secrets for the `ragnar` command, commented out: `RAGNAR_DSN_READ`, `RAGNAR_MCP_TOKEN` and the embedding, rerank and LLM API keys |
| `.rag/config.local.toml` | Never | Your personal overrides |
| `.rag/eval/` | Never | Evaluation reports |

The command loads `.rag/.env` at startup and never replaces a variable already set in your shell. The server never reads it. Compose reads the server's keys from `example/.env`.

When it worked, `ragnar init` prints `.ragnar/config.toml: written`, `.rag/.env: written`, `.rag/config.local.toml: written` and `.gitignore: updated`. Running it again is safe: it keeps existing files.

Commit `.ragnar/` and `.gitignore`. The worker reads `.ragnar/config.toml` from the tracked branch, so `[index]` changes take effect once they reach that branch. See [Configuration](configuration.md).

## 4. Build the first index

If the worker already lists your repository, it starts by itself. Follow it with `docker compose logs -f indexer`.

To index now from a local clone:

```text
/ragnar:index
```

On CPU, the bundled embedder takes about one second per chunk, so a large repository takes a while. Later runs handle only what changed.

The indexer scans for secrets with gitleaks first. A finding stops the run. Rotate the credential, remove it from the repository, and run again.

When it worked, the log shows a line like `myapp: full at <sha>`, and `list_repos` shows the same commit.

## 5. Search

Ask your agent about the code. The `search` skill picks the tool for each question: local tools for exact strings and recent changes, the index for concepts and symbols.

To force a first test:

> Use the Ragnar search skill: where do we handle failed payments?

When it worked, the answer cites `path:line` locations from your repository.

## 6. Measure

Do not assume the index helps. Measure it on your history:

```text
/ragnar:evaluate
```

Then adjust the settings with evidence:

```text
/ragnar:tune
```

See [Evaluation](evaluation.md).

## 7. Keep it healthy

The worker keeps the index current. When something looks wrong:

```text
/ragnar:maintain
```

Run an evaluation again after large refactors or a model change.

## Rolling out to a team

1. Create one token per team: `ragnar token --name team-a --repos myapp`.
2. Add the printed entry to `example/acl.json`, and restart `ragnar-mcp`.
3. Share the MCP URL. Send each token over a secure channel.
4. Read [SECURITY.md](../SECURITY.md) before you expose the server to a network.

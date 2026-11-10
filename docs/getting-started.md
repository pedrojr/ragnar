# Getting started

This guide takes a project from nothing to an agent searching its index. Plan about 30 minutes, plus the first indexing run.

[Português (Brasil)](pt-BR/getting-started.md)

## What you need

- [uv](https://docs.astral.sh/uv/getting-started/installation/) and git on your machine.
- Docker on the machine that runs the server. For most teams, start with one machine.
- About 6 GB of free memory for Docker. The bundled embedder uses up to 3 GB while indexing.

## 1. Install the plugin

In Claude Code:

```text
/plugin install ragnar --marketplace pedrojr/ragnar
```

Claude Code asks for the MCP URL and token. If you have no server yet, keep the default URL and fill in the token after step 2.

Using another agent? Follow [Other agents](agents.md), then continue here.

## 2. Bring up the server

Follow [`example/README.md`](../example/README.md), or ask your agent:

> Use the Ragnar infra skill to bring up the server.

At the end you have:

- an MCP URL, `http://127.0.0.1:8080/mcp` by default;
- a token, `RAGNAR_MCP_TOKEN` in `example/.env`;
- a worker that follows the branches in `example/repos.toml`.

Set the token in `/plugin`, under Ragnar's options.

## 3. Ragnarize the project

Inside your project:

```text
/ragnar:ragnarize
```

The skill runs `ragnar init`, which:

1. detects the languages and the default branch;
2. writes `.ragnar/config.toml`, with every key a repository can set commented out;
3. creates `.rag/` with commented `.env` and `config.local.toml` templates;
4. adds a managed block to `.gitignore`, covering `.rag/`.

Commit `.ragnar/` and `.gitignore`. They belong to the project. Never commit `.rag/`: it holds secrets and local output. See [Configuration](configuration.md).

## 4. Build the first index

If the worker already lists your repository, it starts by itself. Follow it with `docker compose logs -f indexer`.

To index now from a local clone:

```text
/ragnar:index
```

On CPU, the bundled embedder takes about one second per chunk, so a large repository takes a while. Later runs handle only what changed.

The indexer scans for secrets with [gitleaks](https://github.com/gitleaks/gitleaks) first. A finding stops the run. Rotate the credential, remove it from the repository, and run again.

## 5. Search

Ask your agent about the code. The `search` skill picks the tool for each question: local tools for exact strings and recent changes, the index for concepts and symbols.

The skill runs on its own when it fits. To force a first test:

> Use the Ragnar search skill: where do we handle failed payments?

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

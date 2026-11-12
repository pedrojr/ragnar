---
name: infra
description: Brings up the Ragnar infrastructure from the bundled example, with Docker Compose or Kubernetes, connects a repository branch, and hands the MCP URL and token to the plugin. Use for "set up the RAG server", "subir a infraestrutura", "deploy Ragnar", "run the MCP server", or when list_repos cannot reach a server and the user has none.
---

# Bring up the infrastructure

The full walkthrough lives in `${CLAUDE_PLUGIN_ROOT}/example/README.md`. Read it, and follow it with the user. This skill adds the decisions and the checks.

## Decide with the user

1. **Compose or Kubernetes.** Compose suits one team on one machine. Kubernetes suits automatic scaling. Both publish port 8080, so run one at a time.
2. **Embedder.** The bundled CPU embedder keeps code on the user's machines. An external provider is faster, but it sends source code to that provider: make the user confirm this explicitly.
3. **Where it runs.** The example has no TLS. Compose binds to `127.0.0.1`, but on Kubernetes Docker Desktop publishes port 8080 on every network interface. Tell the user. Any shared server needs a TLS proxy and network rules first. See "Limits" in the example README.

## Steps

1. Check `docker`, `uv` and `git`. For Kubernetes, also `kubectl` and the context, and that `docker desktop kubernetes status` reports `running`. On Windows, `failed to start` usually means WSL needs `wsl --update`: see Troubleshooting in the example README.
2. Work in a clone of Ragnar, because the plugin folder changes on each update:
   ```bash
   git clone https://github.com/pedrojr/ragnar && cd ragnar/example
   uv run --project .. ragnar init-env
   ```
   This creates `.env`, `acl.json` and `repos.toml`. Git ignores all three. Never print the contents of `.env` in the conversation.
3. Add the user's repository to `repos.toml` (name, URL, branch). For a private repository, set `token_env` and put the token in `.env`.
4. Start it, following the README section for the chosen target.
5. Watch the first build: `docker compose logs -f indexer`. It ends with a line such as `myapp: full at <sha>`.
6. Confirm: `./scripts/smoke.sh <name>`.
7. Connect the plugin. Tell the user to run `/plugin`, open Ragnar's settings, and set `mcp_url` and `mcp_token`. The token is `RAGNAR_MCP_TOKEN` in `.env`: the user copies it, not you.
8. Reconnect, and confirm with `list_repos`.

## Rules

- Confirm before `docker compose down -v` or `scripts/down.sh`. They remove the index, which takes time to rebuild.
- Never commit `.env`, `acl.json` or `repos.toml`.
- Keep the write password away from the MCP server. Each service gets only the credential it uses.

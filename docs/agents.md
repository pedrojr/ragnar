# Other agents

Ragnar's server speaks MCP, an open protocol, so any MCP client with HTTP support can search the index. Claude Code gets the full plugin. Elsewhere, you connect the server yourself and add the search rules.

[Português (Brasil)](pt-BR/agents.md)

Client configuration formats change often. If a block below fails, check your client's current MCP documentation.

## Install the CLI

Skills and project setup use the `ragnar` command:

```bash
uv tool install git+https://github.com/pedrojr/ragnar
ragnar init --agents-md
```

`--agents-md` adds the search rules to `AGENTS.md`, which many agents read.

## Connect the server

Keep the token in an environment variable: `export RAGNAR_MCP_TOKEN=...`.

**Cursor**, in `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "ragnar": {
      "url": "http://127.0.0.1:8080/mcp",
      "headers": { "Authorization": "Bearer ${env:RAGNAR_MCP_TOKEN}" }
    }
  }
}
```

**VS Code**, in `.vscode/mcp.json`:

```json
{
  "servers": {
    "ragnar": {
      "type": "http",
      "url": "http://127.0.0.1:8080/mcp",
      "headers": { "Authorization": "Bearer ${env:RAGNAR_MCP_TOKEN}" }
    }
  }
}
```

**Codex CLI**, in `~/.codex/config.toml`:

```toml
[mcp_servers.ragnar]
url = "http://127.0.0.1:8080/mcp"
bearer_token_env_var = "RAGNAR_MCP_TOKEN"
```

**Other clients:** use Streamable HTTP, the URL above, and an `Authorization: Bearer <token>` header.

## Use the skills

The files in [`skills/`](../skills/) follow the Agent Skills format: a `SKILL.md` with a name and a description. If your client supports that format, copy the folders to where it loads skills from. If it does not, point your agent at the file when you need it, for example: "follow skills/evaluate/SKILL.md".

The `search` skill matters most. Without it, agents tend to overuse either the index or grep.

## Other models

The agent's model does not matter to Ragnar. The server's own model choices are independent:

| Part | Works with |
| --- | --- |
| Embeddings | Any `/v1/embeddings` endpoint |
| Reranking | Any `/rerank` endpoint in the Jina, Cohere or TEI shape |
| Query expansion and contextual retrieval | Any OpenAI-compatible `/chat/completions` endpoint |

See [Configuration](configuration.md).

# MCP server

The Ragnar MCP server gives agents read-only access to the index, over Streamable HTTP. It holds no session, so any replica answers any request.

[Português (Brasil)](pt-BR/mcp.md)

## Tools

### `find_symbol`

Finds definitions by name. It matches the start of the name, ignores case, and lists exact names first. It returns path, lines, qualified name and signature, with no body, so it is the cheapest call.

| Parameter | Type | Meaning |
| --- | --- | --- |
| `name` | string | Name or start of a name |
| `repo` | string, optional | Limits to one repository |
| `k` | integer, optional | Maximum results, default 20, up to 50 |

### `search_code`

Runs the configured [retrieval pipeline](retrieval.md) over code, tests and docs. It accepts natural language, symbol names or code snippets.

| Parameter | Type | Meaning |
| --- | --- | --- |
| `query` | string | The search |
| `repo` | string, optional | Limits to one repository |
| `path_prefix` | string, optional | Limits to one folder |
| `kind` | string, optional | `code`, `test` or `doc` |
| `k` | integer, optional | Maximum snippets, up to 20. Defaults to `retrieval.default_k` |

Snippets arrive inside `<retrieved_context>`, with a note telling the model to treat them as data. Closing tags inside repository content are escaped, so a file cannot break out of the block.

### `list_repos`

Lists the repositories the token may read, with indexed commit, chunk count, model and date. Agents use it to tell how fresh the index is.

## Access control

Each client has a token. `acl.json` stores its SHA-256 hash and the repositories it may read:

```json
{"clients": [{"name": "team-a", "token_sha256": "...", "repos": ["myapp"]}]}
```

`"*"` allows every repository. A client filter narrows access, and it never widens it. A request without a known token gets a 401 before it reaches the MCP layer.

Create tokens with `ragnar token --name team-a --repos myapp,other`, then restart the server to reload the file.

## Endpoints

| Path | Purpose | Authentication |
| --- | --- | --- |
| `/mcp` | MCP | Bearer token |
| `/healthz` | Liveness | None |
| `/readyz` | Readiness: checks the database | None |
| `/metrics` | Prometheus | None |

Keep `/metrics` off public networks. It exposes counters, not code.

## Metrics

| Metric | Meaning |
| --- | --- |
| `ragnar_search_total{tool}` | Tool calls served |
| `ragnar_search_empty_total` | Searches with no result |
| `ragnar_tool_errors_total{tool}` | Tool calls with an error |
| `ragnar_auth_rejected_total` | Refused requests |
| `ragnar_context_tokens_total` | Estimated tokens returned to agents |
| `ragnar_search_seconds{phase}` | Latency per phase |

Each tool call also writes a JSON audit line to standard output: client, tool, query, chunk ids, latency and tokens. The query text appears there, so treat these logs as sensitive.

# MCP server

The Ragnar MCP server gives agents read-only access to the index, over Streamable HTTP. It holds no session, so any replica answers any request.

[Português (Brasil)](pt-BR/mcp.md)

## Tools

### `find_symbol`

Finds definitions by name. It accepts qualified names such as `TOrder.Cancel`, matches the start of the name and then any part of it, ignores case, and lists exact names first. It returns path, lines, qualified name and signature, with no body, so it is the cheapest call.

| Parameter | Type | Meaning |
| --- | --- | --- |
| `name` | string | Name or start of a name |
| `repo` | string, optional | Limits to one repository |
| `k` | integer, optional | Maximum results, default 20, up to 50 |

### `find_references`

Finds where a name is used: callers, instantiations, imports, and the form events that call a handler. It matches the whole identifier and leaves definitions out. It returns path, line, enclosing symbol and the line itself. For names declared in few places, the answer comes from the code graph, one line per calling routine; other names fall back to a text search.

| Parameter | Type | Meaning |
| --- | --- | --- |
| `name` | string | The identifier |
| `repo` | string, optional | Limits to one repository |
| `path_prefix` | string, optional | Limits to one folder |
| `k` | integer, optional | Maximum results, default 30, up to 100 |

### `call_graph`

Walks the code graph one step from a routine. `callees` lists what the routine names (routines, types, constants) and where each is declared. `callers` lists the routines and form events that name it.

| Parameter | Type | Meaning |
| --- | --- | --- |
| `name` | string | The routine. Qualified names such as `TOrder.Cancel` work |
| `direction` | string, optional | `callees` (default) or `callers` |
| `repo` | string, optional | Limits to one repository |
| `k` | integer, optional | Maximum results, default 30, up to 100 |

### `trace_flow`

Finds a chain of references from a routine to a declaration or a table, such as from a button handler to the table it writes. Each step has path, line and the line itself.

| Parameter | Type | Meaning |
| --- | --- | --- |
| `source` | string | The routine where the chain starts |
| `target` | string | A routine, type or table name |
| `repo` | string, optional | Limits to one repository |
| `max_depth` | integer, optional | Maximum steps, default 4, up to 6 |

### `find_table_usage`

Finds the routines whose SQL reads or writes a table, writes first. It covers SQL in strings, including SQL built line by line with `SQL.Add`, and SQL in Delphi form components.

| Parameter | Type | Meaning |
| --- | --- | --- |
| `table` | string | The table. `CLIENTES` also matches `dbo.CLIENTES` |
| `op` | string, optional | `read` or `write` |
| `repo` | string, optional | Limits to one repository |
| `k` | integer, optional | Maximum results, default 50, up to 200 |

### `repo_map`

Maps an unfamiliar area: the files under a folder that the rest of the code refers to most, each with its main declarations. It is ranked by the code graph and stops at the token budget.

| Parameter | Type | Meaning |
| --- | --- | --- |
| `path_prefix` | string, optional | The folder. Leave it out for the whole repository |
| `repo` | string, optional | Limits to one repository |
| `k` | integer, optional | Maximum files, default 40, up to 100 |

### How the code graph is built

The indexer links names, not compiled symbols. A word in a routine that names a declaration of the repository counts as a reference. This works for every language, including Delphi units the grammar cannot parse. Names declared in more than four places, such as `Create`, and names shorter than four characters are left out, so the edges that remain point somewhere specific. Form events link a control to its handler. SQL in strings links a routine to the tables it reads and writes. The graph can hold false edges: confirm the steps before you rely on them.

### `file_outline`

Lists the declarations of a file with their lines, with no bodies. Use it to find your way around a large file before reading any of it.

| Parameter | Type | Meaning |
| --- | --- | --- |
| `path` | string | Full path, or just its end |
| `repo` | string, optional | Limits to one repository |

### `read_code`

Reads lines of an indexed file at the indexed commit, for code that is not on this disk. Long ranges stop at `retrieval.max_context_tokens`.

| Parameter | Type | Meaning |
| --- | --- | --- |
| `path` | string | Full path, or just its end |
| `start_line` | integer | First line |
| `end_line` | integer | Last line |
| `repo` | string, optional | Limits to one repository |

### `search_code`

Runs the configured [retrieval pipeline](retrieval.md) over code, tests and docs. It accepts natural language, symbol names or code snippets.

| Parameter | Type | Meaning |
| --- | --- | --- |
| `query` | string | The search |
| `repo` | string, optional | Limits to one repository |
| `path_prefix` | string, optional | Limits to one folder |
| `kind` | string, optional | `code`, `test`, `doc` or `summary` |
| `k` | integer, optional | Maximum snippets, up to 20. Defaults to `retrieval.default_k` |

Snippets arrive inside `<retrieved_context>`, with a note telling the model to treat them as data. Hits beyond the token budget arrive as `<ref>` lines with path, lines and symbol. Closing tags inside repository content are escaped, so a file cannot break out of the block. Hits after the first may arrive as excerpts: numbered lines around the query words, with the lines left out named. When only one retrieval method found the top hit, the block carries `confidence="low"` and a note: the hit may be the closest text rather than an answer. Nothing is hidden.

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

# Architecture

[Português (Brasil)](pt-BR/architecture.md)

## Components

| Component | Code | Role |
| --- | --- | --- |
| Plugin | `.claude-plugin/`, `.mcp.json`, `skills/`, `hooks/`, `bin/` | Connects the agent and guides the workflow |
| CLI | `src/ragnar/cli.py` | Every operation, for people and skills |
| Indexer | `chunker.py`, `languages.py`, `delphi.py`, `indexer.py` | Turns a commit into chunks, symbols and vectors |
| Code graph | `graph.py`, `xref.py` | Links routines to the names they use, in the repository and across repositories, form events to handlers, and SQL to tables |
| Worker | `sync.py` | Follows branches and calls the indexer |
| Retrieval | `search.py`, `llm.py` | The search pipeline, and the optional chat model and reranker |
| MCP server | `mcp_server.py`, `acl.py`, `events.py` | Read-only access for agents, and graph access events to NATS |
| Embedder | `embed_server.py`, `embedder.py` | Bundled CPU embedder, and the client for any endpoint |
| Evaluation | `evaluation.py`, `stats.py` | Measures retrieval against grep, and reports real use from the audit lines |
| Operations | `ops.py`, `migrate.py`, `migrations/`, `cleanup.py` | Smoke test, load test, schema migrations and `ragnar gc` |
| Project setup | `project.py` | `ragnar init` and `ragnar doctor` |
| Settings | `config.py` | Layers, validation and fingerprint |

## Data flow

Indexing:

1. The worker mirrors the repository and resolves the tracked branch to a commit.
2. It compares the commit with `index_state`. A new model, new chunking settings or a rewritten history leads to a full run. Otherwise, it reads the changed files from `git diff`.
3. It reads file contents from the commit with `git cat-file`.
4. It normalizes encodings and scans for secrets. A finding stops the run, unless the server sets `index.secret_action = "skip"`.
5. It builds chunks, symbols and code graph edges, plus summaries and context notes when they are on. Then it computes embeddings outside any transaction. Unchanged chunks reuse their vectors, and new vectors are checkpointed, so a stopped run resumes.
6. In one short transaction, it locks the repository, confirms no one else wrote, swaps the chunks, and records the commit.
7. In a transaction of its own, it links the repository to the names other repositories declare. Then it brings the other repositories' cross-repository edges up to date, one transaction each, scanning only the files that hold names that appeared or left. `ragnar sync` does this last part once per pass. A failure here leaves the index committed; the next link starts over.

Searching:

1. The agent calls an MCP tool with a token.
2. The server checks the token and resolves which repositories it may read.
3. On a replica, `search_code` runs the pipeline in [Retrieval techniques](retrieval.md). The other tools read symbols, the code graph or chunk lines.
4. The result goes back inside delimiters, and an audit line is written. With events on, the places the call read go to NATS from a separate thread.

## Design decisions

**Read commits, never the working tree.** The index reflects a known state, and uncommitted secrets or drafts never enter it. Local changes are the `search` skill's job.

**One MCP service, many replicas.** All agents use the same stateless MCP server. It scales out behind a load balancer, and each token decides which repositories a client reads. There is no separate MCP per access profile.

**One writer, many readers.** Only the indexer writes, only on the primary. The MCP server connects with a role that can only read, on replicas. A bug or a prompt injection in the search path cannot change the index.

**Separate credentials per service.** The MCP process never sees the write password.

**The index is derived data.** Git is the source of truth, so recovery means reindexing. That is why there is no backup story in the example.

**A repository cannot configure the server.** It tunes its own chunking, but it cannot reach endpoints, keys or the secret scan. See [Configuration](configuration.md).

**Polling before webhooks.** Polling needs no inbound network, so it works behind a firewall. Teams that want indexing on each push can use the CI templates.

**Model calls stay optional.** The default pipeline needs an embedder and nothing else, so cost stays predictable. Everything else is opt-in, and measured with `ragnar eval`.

**Standard interfaces only.** MCP towards agents. `/v1/embeddings`, `/rerank` and `/chat/completions` towards models, plus the Anthropic Messages API for Claude. No part of the system depends on one vendor.

## Data model

| Table | Content |
| --- | --- |
| `code_chunk` | One row per chunk: location, kind, text, tsvector, embedding |
| `code_symbol` | One row per definition: name, qualified name, signature |
| `code_edge` | Code graph: routine to name (`ref`), routine to a name of another repository (`xref`), control to handler (`event`), routine to table (`sql_read`, `sql_write`) |
| `code_xref_name` | Per repository: the names of other repositories its `xref` edges were matched against |
| `index_state` | Per repository: indexed commit, model, settings fingerprint, files left out |
| `embedding_checkpoint` | Vectors of a run that has not committed yet |
| `summary_cache` | File summaries by model and file content |
| `replication_probe` | Measures replication lag in the smoke test |
| `schema_migrations` | Migrations already applied |

## Repository layout

```text
.claude-plugin/   plugin and marketplace manifests
.mcp.json         MCP connection, filled from the plugin settings
bin/              the ragnar command, for agents
hooks/            SessionStart and PreToolUse hooks
skills/           agent workflows
src/ragnar/       Python package
tests/            test suite
docs/             documentation (docs/pt-BR/ has the translation)
example/          reference infrastructure
benchmark/        public benchmark and its report
```

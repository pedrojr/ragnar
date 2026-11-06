# Configuration

Ragnar reads settings from TOML files and environment variables.

[Português (Brasil)](pt-BR/configuration.md)

## Where settings come from

Later layers win:

1. Built-in defaults.
2. The preset named in `project.preset`.
3. `ragnar.toml`, committed with the project. `RAGNAR_CONFIG` can point to another file.
4. `ragnar.local.toml`, next to it. Git ignores this file, so use it for personal overrides.
5. Environment variables named `RAGNAR__SECTION__KEY`, such as `RAGNAR__RETRIEVAL__MODE=vector`.
6. `--set section.key=value`, on commands that accept it.

An unknown key or an invalid value stops the program with a message that names the setting.

## Who controls what

The project and the server own different settings.

| Scope | File | Applies to |
| --- | --- | --- |
| Project | `ragnar.toml` in the repository | `[index]` and `project.languages` |
| Server | `example/server.toml` on the infrastructure | Everything else |

The worker reads a repository's `ragnar.toml` from the tracked commit, and it uses only those two scopes. A repository cannot change an endpoint or turn off the secret scan, so a hostile commit cannot redirect your code or hide a credential.

## Secrets

Secrets never go in these files. Each section names the environment variable that holds its key, in `api_key_env`.

## Presets

| Preset | Use it for | Changes |
| --- | --- | --- |
| `economy` | Lowest token cost per search. Agents may compensate with more greps | 3 hits, 1,000 tokens, 2 chunks per file |
| `balanced` | The default | No changes |
| `precise` | Hard questions, higher cost | 10 hits, 8,000 tokens, 100 candidates, neighbor chunks, diversity |

## `[project]`

| Key | Default | Meaning |
| --- | --- | --- |
| `name` | `""` | Repository name in the index |
| `branch` | `"main"` | Branch the index follows |
| `preset` | `"balanced"` | `economy`, `balanced` or `precise` |
| `languages` | `[]` | Languages to index. Empty means all |

## `[index]`

| Key | Default | Meaning |
| --- | --- | --- |
| `include` | `[]` | Extra globs, indexed by line windows |
| `exclude` | `[]` | Globs to skip |
| `max_file_kb` | `2048` | Larger files are skipped, and the run reports them |
| `chunk_budget` | `1800` | Non-whitespace characters per chunk |
| `window_lines` | `80` | Lines per chunk, for files without a grammar |
| `window_overlap` | `10` | Lines shared between windows |
| `index_tests` | `true` | Index test files |
| `index_docs` | `true` | Index Markdown |
| `secret_scan` | `true` | Run gitleaks. Server scope only |
| `secret_action` | `"fail"` | On a finding: `fail` stops the run, `skip` leaves those files out. Server scope only |
| `legacy_encoding` | `"cp1252"` | Fallback for files that are not UTF-8 |

Changing any of these, except `secret_scan` and `secret_action`, reindexes the repository in full on the next run. Chunks whose text did not change keep their vectors, so a reindex only pays for what is new.

## `[embedding]`

Any endpoint compatible with `/v1/embeddings` works: the bundled embedder, Ollama, vLLM, LiteLLM, or a hosted provider.

| Key | Default | Meaning |
| --- | --- | --- |
| `provider` | `"http"` | `http`, or `fake` for tests |
| `url` | `"http://127.0.0.1:8001/v1"` | Base URL |
| `model` | `"jinaai/jina-embeddings-v2-base-code"` | Model name |
| `api_key_env` | `"RAGNAR_EMBEDDING_API_KEY"` | Variable with the key |
| `dim` | `768` | Vector dimension |
| `send_dimensions` | `false` | Sends `dimensions`, for models with shortened vectors |
| `doc_prefix` | `""` | Text added before each document |
| `query_prefix` | `""` | Text added before each query |
| `batch` | `16` | Texts per request |
| `concurrency` | `2` | Parallel requests while indexing |

To change the model, set `model` and `dim`. The next run reindexes in full. A different dimension also needs `ragnar migrate --resize-embeddings`, which clears the index. The HNSW index supports up to 2,000 dimensions: for larger models, use `send_dimensions` to shorten the vectors.

A hosted provider receives your source code. Choose accordingly.

## `[retrieval]`

| Key | Default | Meaning |
| --- | --- | --- |
| `mode` | `"hybrid"` | `hybrid`, `vector` or `lexical` |
| `default_k` | `5` | Hits when the caller does not set `k` |
| `candidates` | `50` | Hits per list, before fusion |
| `rrf_k` | `30` | RRF smoothing constant |
| `vector_weight` | `1.0` | Weight of the vector list |
| `lexical_weight` | `1.0` | Weight of the lexical list |
| `symbol_weight` | `1.0` | Weight of exact symbol matches. `0` turns it off |
| `file_weight` | `2.0` | Weight of the whole-file list. `0` turns it off |
| `ef_search` | `100` | HNSW search width |
| `max_chunks_per_file` | `3` | Cap per file. `0` turns it off |
| `diversity` | `0.0` | `0` is pure relevance, `1` is pure novelty |
| `neighbor_chunks` | `0` | Adjacent chunks added around each hit |
| `max_context_tokens` | `2000` | Estimated token budget. `0` turns it off |
| `reference_hits` | `8` | Hits that did not fit, returned as path and symbol only |

## `[rerank]`

Any endpoint with the `/rerank` request shape used by Jina, Cohere and TEI works.

| Key | Default | Meaning |
| --- | --- | --- |
| `provider` | `"none"` | `none` or `http` |
| `url` | `""` | Full endpoint URL |
| `model` | `""` | Model name |
| `api_key_env` | `"RAGNAR_RERANK_API_KEY"` | Variable with the key |
| `candidates` | `30` | Hits sent to the reranker |

## `[query]`

| Key | Default | Meaning |
| --- | --- | --- |
| `strategy` | `"none"` | `none`, `multi_query` or `hyde`. The last two need `[llm]` |
| `variants` | `3` | Rewrites for `multi_query` |

## `[contextual]`

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Writes a context note per chunk at index time. Needs `[llm]` |
| `max_file_chars` | `20000` | File text sent with each chunk |
| `concurrency` | `4` | Parallel model calls |

## `[llm]`

Any OpenAI-compatible `/chat/completions` endpoint works.

| Key | Default | Meaning |
| --- | --- | --- |
| `url` | `""` | Base URL |
| `model` | `""` | Model name |
| `api_key_env` | `"RAGNAR_LLM_API_KEY"` | Variable with the key |
| `temperature` | `0.0` | Sampling temperature |
| `max_tokens` | `300` | Reply limit |
| `timeout` | `60.0` | Seconds |

## Runtime variables

These configure a process, not retrieval.

| Variable | Used by | Meaning |
| --- | --- | --- |
| `RAGNAR_DSN_WRITE` | Indexer, migrations | Primary connection |
| `RAGNAR_DSN_READ` | MCP server, evaluation | Replica connection |
| `RAGNAR_ACL_FILE` | MCP server | Path to `acl.json` |
| `RAGNAR_CONFIG` | All | Path to the settings file |
| `RAGNAR_POOL_MAX` | MCP server | Database connections, default `10` |
| `RAGNAR_POOL_MAX_LIFETIME` | MCP server | Connection lifetime in seconds, default `120` |
| `RAGNAR_EMBED_THREADS` | Embedder | CPU threads |
| `RAGNAR_EMBED_MAX_TOKENS` | Embedder | Input cut, default `512` |
| `RAGNAR_EMBED_BATCH` | Embedder | Internal batch, default `8` |
| `RAGNAR_VENV` | `bin/ragnar` | Environment folder for the plugin's CLI |

# Configuration

Ragnar reads settings from TOML files and environment variables.

[Português (Brasil)](pt-BR/configuration.md)

## Where settings come from

Later layers win:

1. Built-in defaults.
2. The preset named in `project.preset`.
3. `.ragnar/config.toml`, committed with the project. `RAGNAR_CONFIG` can point to another file.
4. `.rag/config.local.toml`. Git ignores the `.rag/` folder, so use this file for personal overrides.
5. Environment variables named `RAGNAR__SECTION__KEY`, such as `RAGNAR__RETRIEVAL__MODE=vector`.
6. `--set section.key=value`, on commands that accept it.

An unknown key or an invalid value stops the program with a message that names the setting.

## Who controls what

The project and the server own different settings.

| Scope | File | Applies to |
| --- | --- | --- |
| Project | `.ragnar/config.toml` in the repository | `[index]` and `project.languages` |
| Server | `example/server.toml` on the infrastructure | Everything else |

The worker reads a repository's `.ragnar/config.toml` from the tracked commit, and it uses only those two scopes. A repository cannot change an endpoint or turn off the secret scan, so a hostile commit cannot redirect your code or hide a credential.

## Secrets

Secrets never go in these files. Each section names the environment variable that holds its key, in `api_key_env`.

On a developer machine, put those variables in `.rag/.env`. The CLI loads that file at startup and never replaces a variable that is already set. The `mcp` and `embedder` commands never read it.

## Embeddings and summaries

Two steps turn code into something searchable. One is always on. The other is optional.

| | Embeddings | Summaries |
| --- | --- | --- |
| Default | On, always | Off |
| What it does | Turns each code chunk into a vector. Search compares the vector of your question with those of the chunks, so it finds code by meaning | A chat model writes a short description of each code file. The description is searched as an extra chunk of kind `summary` |
| When it runs | At index time for every chunk, and at search time for the question | At index time only |
| Model | `jinaai/jina-embeddings-v2-base-code`, 768 dimensions, bundled and run on CPU | Ollama, Claude Haiku, or any OpenAI-compatible chat model you choose |
| Cost | CPU time, about one second per chunk. No fee with the bundled embedder | One chat call per file of 30 lines or more, sending up to 12,000 characters and getting up to 300 tokens back. Cached by file content, so only new or changed files cost a call |
| Improves | Finding code from a question in natural language | Questions in business words that the code never uses, as in legacy code with poor names |
| Settings | `[embedding]` | `[summaries]` and `[llm]` |
| Key | `RAGNAR_EMBEDDING_API_KEY`, only for a hosted API | `RAGNAR_LLM_API_KEY`, for Anthropic or another hosted API |
| Sends code to | Nobody with the bundled embedder. The provider, with a hosted API | Nobody with Ollama. The provider, with a hosted API |

To turn summaries on with Ollama running on the host, in `example/server.toml`:

```toml
[summaries]
enabled = true
provider = "ollama"                           # or "anthropic", for Claude Haiku
url = "http://host.docker.internal:11434/v1"  # leave out for "anthropic"
exclude = ["vendor/"]                         # folders that get no summary
```

Then restart with `docker compose restart indexer`. The next run reindexes the repository, and unchanged chunks keep their vectors. To turn summaries off, set `enabled = false`. Summaries are a server setting: a repository cannot turn them on from its own `.ragnar/config.toml`, but it can leave folders out with `index.summary_exclude`. Enabling them without a model stops the program with an error.

[File summaries](summaries.md) walks through Ollama and Haiku step by step, with a preview command and troubleshooting.

`[summaries]` can name its own provider and model. Keys it leaves empty come from `[llm]`, which serves multi-query, HyDE and contextual retrieval. See [Retrieval techniques](retrieval.md).

Under Compose, set the keys in `example/.env`. `compose.yaml` passes `RAGNAR_EMBEDDING_API_KEY` to every app service, `RAGNAR_LLM_API_KEY` to `indexer` (summaries and contextual retrieval) and `ragnar-mcp` (query strategies), and `RAGNAR_RERANK_API_KEY` to `ragnar-mcp`.

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
| `index_agent_notes` | `false` | Index instructions written for agents (`AGENTS.md`, `CLAUDE.md`, Copilot and Cursor rules). Agents already read them |
| `secret_scan` | `true` | Run gitleaks. Server scope only |
| `secret_action` | `"fail"` | On a finding: `fail` stops the run, `skip` leaves those files out. Server scope only |
| `legacy_encoding` | `"cp1252"` | Fallback for files that are not UTF-8 |
| `summary_exclude` | `[]` | Directories (ending in `/`) and globs that get no [file summary](summaries.md#leave-folders-out). Adds to `summaries.exclude` |

Changing any of these, except `secret_scan` and `secret_action`, reindexes the repository in full on the next run. Chunks whose text did not change keep their vectors, so a reindex only pays for what is new.

## `[embedding]`

Any endpoint compatible with `/v1/embeddings` works: the bundled embedder, Ollama, vLLM, LiteLLM, or a hosted provider. See [Embeddings and summaries](#embeddings-and-summaries).

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
| `literal_weight` | `1.0` | Weight of the literal list, used when the question quotes text: chunks whose string literals hold every quoted word. `0` turns it off |
| `graph_weight` | `0.0` | Weight of the graph list: what the best hits call, and who calls them. `0` turns it off |
| `graph_seeds` | `5` | Best hits whose graph neighbors join the graph list |
| `doc_weight` | `0.5` | Scales the fused score of documentation hits. Below 1, code wins over overview documents that mention every module |
| `rank_normalization` | `1` | How lexical ranking discounts long chunks: `1` divides by 1 + log(length), `2` by length. `2` helped one large Delphi monorepo and hurt smaller projects |
| `ef_search` | `100` | HNSW search width |
| `max_chunks_per_file` | `3` | Cap per file. `0` turns it off |
| `diversity` | `0.0` | `0` is pure relevance, `1` is pure novelty |
| `neighbor_chunks` | `0` | Adjacent chunks added around each hit |
| `max_context_tokens` | `2000` | Estimated token budget. `0` turns it off |
| `reference_hits` | `8` | Hits that did not fit, returned as path and symbol only |
| `excerpts` | `true` | Hits after the first keep only the numbered lines around the query words, and say which lines they left out |

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

## `[summaries]`

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Writes a summary of each code file at index time. See [File summaries](summaries.md) |
| `provider` | `""` | `ollama`, `anthropic` or `openai`. Empty: use `[llm]` |
| `url` | `""` | Base URL. Empty: the provider default, or `llm.url` when `provider` is empty |
| `model` | `""` | Model name. Empty: the provider default, or `llm.model` when `provider` is empty |
| `api_key_env` | `""` | Variable with the key. Empty: `llm.api_key_env` |
| `language` | `""` | Language of the summaries, such as `"Brazilian Portuguese"`. Empty: the model chooses |
| `exclude` | `[]` | Directories (ending in `/`) and globs that get no summary |
| `min_lines` | `30` | Shorter files get no summary |
| `max_chars` | `12000` | File text sent to the model |
| `concurrency` | `4` | Parallel model calls |

## `[llm]`

`provider` picks the API. `openai` works with any `/chat/completions` endpoint, `ollama` uses Ollama's compatible `/v1` API, and `anthropic` uses the Anthropic Messages API.

| Provider | Default `url` | Default `model` |
| --- | --- | --- |
| `openai` | none | none |
| `ollama` | `http://127.0.0.1:11434/v1` | `qwen3:8b` |
| `anthropic` | `https://api.anthropic.com/v1` | `claude-haiku-5-5` |

| Key | Default | Meaning |
| --- | --- | --- |
| `provider` | `"openai"` | `openai`, `ollama` or `anthropic` |
| `url` | `""` | Base URL. Empty: the provider default |
| `model` | `""` | Model name. Empty: the provider default |
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

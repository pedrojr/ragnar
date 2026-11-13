# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

[Português (Brasil)](CHANGELOG.pt-BR.md)

## [Unreleased]

### Added

- Claude Code plugin at the repository root, with the `ragnarize`, `search`, `index`, `evaluate`, `tune`, `maintain` and `infra` skills.
- `.ragnar/config.toml` settings with presets, validation and per-layer overrides.
- Language profiles for Python, JavaScript, TypeScript, Java, Kotlin, C#, Go, Rust, C, C++, PHP, Ruby, Swift, Scala, Bash, SQL and Delphi.
- Delphi support: UTF-16 and CP-1252 sources, unit and method symbols, and form parsing.
- Configurable retrieval: weighted fusion, symbol boost, reranking, multi-query, HyDE, contextual retrieval, diversity, neighbor chunks and a token budget.
- `ragnar init` and `ragnar doctor`, including a managed `.gitignore` block.
- `ragnar sync` and an index worker that follows repository branches.
- `ragnar eval`, with variant comparison and context token estimates.
- Reference infrastructure in `example/`, with CI templates.
- Documentation in English and Brazilian Portuguese.
- `find_references`, `file_outline` and `read_code` MCP tools. `find_symbol` takes qualified names and matches any part of a name.
- Whole-file list in the fusion (`retrieval.file_weight`), and references for hits beyond the token budget (`retrieval.reference_hits`).
- `index.secret_action`, to leave files with findings out instead of stopping the run. Server scope only.
- `benchmark/`: before and after comparison on pinned open source projects in Delphi, Java, C# and Go.
- Binary Delphi forms (`TPF0`) are read as text, and image data in text forms stays out of chunks and the size limit.
- `llm.provider` and `summaries.provider` (`openai`, `ollama`, `anthropic`), with default URLs and models. The `anthropic` provider calls the Messages API, for Claude Haiku.
- `[summaries]` takes its own `provider`, `url`, `model` and `api_key_env`, falling back to `[llm]`, plus `language` for the language of the summaries.
- `summaries.exclude` on the server and `index.summary_exclude` in a repository leave folders out of file summaries.
- `ragnar summarize`, to preview summaries of local files with the configured model.
- [File summaries](docs/summaries.md) guide.
- `summaries.timeout` (300 seconds): summaries run at index time and no longer share the short `llm.timeout` that guards searches.
- `ragnar search --kind`, the same filter `search_code` has.
- `exclude` per repository in `repos.toml`.
- An interrupted first index resumes from its embedding checkpoint (migration 006).
- `RAGNAR_BENCH_SUITE` and `RAGNAR_BENCH_DSN` run the benchmark on a private suite, and `benchmark/summarize_suite.py` publishes its aggregates only.
- `RAGNAR_EMBED_THREADS` and `RAGNAR_EMBED_MEMORY` in the example infrastructure.
- `retrieval.rank_normalization`, the length discount of lexical ranking, formerly a constant.
- String literals (messages, form captions, SQL) get their own lexical field and a literal list in the fusion (`retrieval.literal_weight`). The list runs only when the question quotes text, and all quoted words must appear in the literals. Migration 007 rewrites `code_chunk`, and the chunker version change triggers a full reindex that reuses vectors.
- Delphi event handlers carry the component, class, caption and form of the controls that fire them, so screen words reach the `.pas`. Words for screen parts (botão, grade, duplo clique) also search the names code gives them.
- Excerpts (`retrieval.excerpts`, on by default): hits after the first keep only numbered lines around the query words, so more hits fit the token budget. `search_code` tells agents to read line ranges instead of whole files.
- `retrieval.doc_weight` scales documentation hits in the fusion, so overview documents stop taking the first place from code. It defaults to 0.5: +0.02 of MRR on the public benchmark, neutral on a large Delphi monorepo.
- Code graph (migration 008): the indexer links routines to the declarations they name, form events to their handlers, and routines to the tables their SQL reads or writes. New MCP tools `call_graph`, `trace_flow` and `find_table_usage`; `find_references` answers from the graph when it can. `retrieval.graph_weight` adds the graph neighbors of the best hits to the fusion (off by default).
- `search_code` marks results with `confidence="low"` and a short note when only one retrieval method found the top hit. Nothing is hidden.
- `repo_map` MCP tool: the files of a folder that the rest of the code refers to most, with their main declarations.
- File summaries (`[summaries]`, off by default, migration 009): a chat model writes two or three sentences per code file in the words a team would use, searched as chunks of kind `summary`. Cached by file content.
- `ragnar stats`: a usage report from the MCP audit lines (calls, tokens, empty and low-confidence searches, repeated questions), and with `--labels`, questions from real use: a search followed by `read_code` on a file it returned. Audit lines now carry the paths a search returned.

### Changed

- `ragnar init` writes the search rules into a managed block in `AGENTS.md`, and in `CLAUDE.md` when the project has one, by default. `--no-agent-notes` turns it off; `--agents-md` still parses. A rerun updates the block and replaces the older appended note. `ragnar doctor` warns when neither file has the rules.
- The plugin's `SessionStart` hook also runs after `/clear`, resume and compaction, and tells the agent that the project is indexed and that the `search` skill picks the tool. A new `PreToolUse` hook adds a one-line reminder of the index tools to the first `Grep`, `Glob` or shell search of a session. Both run only in projects with `.ragnar/config.toml` and never block a call.
- The `search` skill starts with three questions that pick between local tools and the index, covers shell `grep`, `rg` and `find`, and says how to check a low-confidence or possibly stale hit.
- **Breaking:** a project keeps Ragnar's files in two folders. `.ragnar/` is committed and holds `config.toml` (formerly the root `ragnar.toml`) and the golden questions in `eval/`. `.rag/` is ignored by git and holds `config.local.toml` (formerly `ragnar.local.toml`), a `.env` file the CLI loads, and generated reports. Ragnar no longer reads the root files, and `ragnar doctor` points them out. The `mcp` and `embedder` commands never load `.rag/.env`.
- `ragnar init` writes every key a repository can set into `.ragnar/config.toml`, commented out with its default, and creates the `.rag/` templates. `ragnar eval run` saves its report to `.rag/eval/report.json` by default.
- **Breaking:** renamed the project from `raglib` to Ragnar, including the CLI, environment variables, database, roles and images. Indexes built by the prototype must be rebuilt.
- The secret scan runs on normalized text, so it covers UTF-16 sources.
- `index.max_file_kb` defaults to 2048, and runs report what they leave out and why.
- Lexical ranking weighs terms by rarity, drops English and Portuguese stopwords, and matches by light stemming.
- Portuguese suffixes and accents no longer split a word from its variants, in queries and in identifiers.
- The whole-file list ranks files by BM25 over chunk counts, with a length penalty. `retrieval.file_weight` defaults to 2.0 and `retrieval.rrf_k` to 30.
- The secret scan reads forms as indexed, so image data no longer raises findings.
- History questions drop ticket prefixes and commit types from their subjects.
- `balanced` now returns 5 hits within 2,000 tokens, and `economy` 3 hits within 1,000: in real agent sessions, 4,000 tokens cost more context than grep.
- The agent note and the MCP instructions tell agents when to start with the index. With the previous wording, agents kept using grep.
- Reindexing reuses the vectors of unchanged chunks, and texts reach the embedder sorted by length.
- Migration 005 rewrites `code_chunk`, which rebuilds the vector index. Expect it to take a while on large indexes, and give parallel builds enough shared memory.
- `ragnar sync --watch` rereads `repos.toml` before each pass, so new repositories need no restart. A file that stops parsing keeps the previous list.
- Agent instruction files (`AGENTS.md`, `CLAUDE.md`, Copilot and Cursor rules) stay out of the index unless `index.index_agent_notes` is set: agents already read them, and they matched almost any question.
- The chunk total behind term rarity is cached until an indexer writes, instead of counted on every search.
- `index.include` and `index.exclude` accept a directory written with a trailing `/`, as `index.summary_exclude` does. Files already indexed under such a directory leave the index with `ragnar index --full`.
- In the Kubernetes example, the MCP server and the worker read `server.toml`, and `scripts/up.sh` passes the model keys from `.env` to them. `k8s/base/embed.env` holds every embedding setting, the URL included.
- History questions skip commit messages that say nothing, and drop from their answers the files changed by more than 5% of the commits. The benchmark also reports whether any right file is in the top 10 (`hit10`) and records the confidence of each search.

### Fixed

- Each file summary reaches the cache as the model returns it, and a failed model call leaves only that file without a summary. Before, one timeout stopped the whole indexing run and lost every summary already written.
- A database error in one repository of `ragnar sync` no longer stops the worker: the other repositories and the next pass still run.
- Accented words stay whole in the lexical index.
- Comments stay with the declaration that follows them.
- Folders named `build`, `target` or `Win32` no longer hide real sources.
- Delphi units the grammar cannot parse keep their routine boundaries and symbols.
- Oversized literals split into chunks that fit the budget.
- Chunks holding a whole file now name its declarations, and C# files their namespace.
- A 4xx from the embedder is a clear error, and retries back off.
- The token budget skips a hit that does not fit and keeps the smaller hits after it.
- The grep baseline keeps accented words whole and also searches their unaccented form, so Portuguese questions get a fair comparison.
- `ragnar migrate --resize-embeddings` also clears the code graph. Before, a token that listed a repository by name could still read its old edges.
- `ragnar sync --repo` applies to every pass of `--watch`, and a name missing from `repos.toml` is an error.
- `ragnar stats` reads audit lines with a prefix, such as the output of `docker compose logs`.
- `ragnar smoke` and `ragnar load` report a missing `RAGNAR_MCP_TOKEN` as an error message.
- A contextual note stays on one line, so `read_code` and excerpts keep the right line numbers.
- The Kubernetes migration Job reads the embedding dimension from `embed.env`.
- `scripts/index.sh --target k8s` runs the indexer in a temporary pod, through the Services. Before, it went through `kubectl port-forward`, which exits when Postgres closes a forwarded connection, so the run failed after its first queries. A local clone travels as a git bundle.
- `scripts/index.sh` under Compose uses `server.toml`, like the worker, so the worker's next pass does not reindex in full.
- An unreachable database is an error message from the CLI, not a traceback.
- `ragnar load` opens a new MCP session every 10 seconds. A kept-alive connection stays on the pod it first reached, so before, the pods the autoscaler added got no traffic. A failed connection now counts as an error instead of ending the test.
- The example docs and `SECURITY.md` say that Docker Desktop publishes the Kubernetes `LoadBalancer` on every network interface, and that WSL needs cgroup v2 for recent Kubernetes versions.

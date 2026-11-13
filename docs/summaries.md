# File summaries

[Português (Brasil)](pt-BR/summaries.md)

A file summary is a short paragraph a chat model writes about one code file, in the words the team uses for the business. For a Delphi unit full of `btnTrancarClick` and `TB_MATR`, the summary may read "suspends a student's enrollment for the term". Ragnar indexes that paragraph as one more chunk of kind `summary`, so a question asked in business words finds the file even when the code never uses those words.

Summaries are off by default (`summaries.enabled = false`). This page shows how to turn them on with Ollama on your own machine or with Claude Haiku through the Anthropic API, how to leave folders out, and how to check the result.

## How it works

1. At index time, Ragnar picks every code file of 30 lines or more. Tests, documentation and the folders you exclude are skipped.
2. It sends the first 12,000 characters of each file to the model, with the names the file declares and the names it refers to.
3. The model answers with two or three sentences.
4. Ragnar stores the answer in a cache keyed by the file content and the model. A file that did not change never costs a second call, even in a full reindex.
5. The summary is embedded and searched like any other chunk. `read_code`, `file_outline` and the line tools ignore it.

## Choose a provider

| | Ollama | Anthropic (Haiku) |
| --- | --- | --- |
| Where the model runs | Your machine or server | Anthropic's API |
| Code leaves your network | No | Yes, to Anthropic |
| Cost | Hardware and time. A 7 to 8B model on a GPU takes a few seconds per file; on CPU, much longer | Per token. Each file sends up to 12,000 characters and gets up to 300 tokens back |
| Key | None | An Anthropic API key |
| Default model | `qwen3:8b` | `claude-haiku-5-5` |
| Default URL | `http://127.0.0.1:11434/v1` | `https://api.anthropic.com/v1` |

Set `summaries.provider` and Ragnar fills in the URL and the model. Any other OpenAI-compatible endpoint (vLLM, LiteLLM, a hosted API) also works with `provider = "openai"`, but then you must set `url` and `model` yourself.

## Step by step with Ollama

1. Install Ollama from <https://ollama.com> and pull the model:

   ```bash
   ollama pull qwen3:8b
   ```

2. Check that it answers:

   ```bash
   curl http://127.0.0.1:11434/v1/models
   ```

3. Turn summaries on. For a local test, in `.rag/config.local.toml`:

   ```toml
   [summaries]
   enabled = true
   provider = "ollama"
   language = "Brazilian Portuguese"   # optional: the language of the summaries
   ```

   Under Compose, put the same block in `example/server.toml` and point the URL at the host, because `127.0.0.1` inside the container is the container itself:

   ```toml
   [summaries]
   enabled = true
   provider = "ollama"
   url = "http://host.docker.internal:11434/v1"
   ```

   The `indexer` service maps `host.docker.internal` to the host, on Linux as well. If Ollama runs as a Compose service of its own, use `http://ollama:11434/v1`.

4. Preview a few files before indexing anything. See [Preview before indexing](#preview-before-indexing).

5. Index. See [Apply and verify](#apply-and-verify).

To use another model, pull it and set `model = "qwen2.5-coder:7b"`, for example. Reasoning models print a `<think>` block before the answer; Ragnar removes it.

## Step by step with Claude Haiku

1. Create an API key at <https://console.anthropic.com>.

2. Store the key in an environment variable. Never write it in a TOML file. On a developer machine, in `.rag/.env`:

   ```bash
   RAGNAR_LLM_API_KEY=sk-ant-...
   ```

   Under Compose, put the same line in `example/.env`. `compose.yaml` passes `RAGNAR_LLM_API_KEY` to the `indexer`.

3. Turn summaries on, in `.rag/config.local.toml` or `example/server.toml`:

   ```toml
   [summaries]
   enabled = true
   provider = "anthropic"
   language = "Brazilian Portuguese"   # optional
   ```

4. Preview and index, as in the next sections.

If `[llm]` already uses `RAGNAR_LLM_API_KEY` for another provider, give the summaries their own variable with `api_key_env = "RAGNAR_SUMMARIES_API_KEY"`, and pass that variable to the `indexer` in `compose.yaml`.

## Leave folders out

Two lists apply, and a file that matches either one gets no summary. Its code is still indexed.

| Setting | Who sets it | Where |
| --- | --- | --- |
| `summaries.exclude` | The server owner | `example/server.toml` or `.rag/config.local.toml` |
| `index.summary_exclude` | Each repository | The `.ragnar/config.toml` committed in the repository |

Each entry is a path relative to the repository root:

- A directory, written with a trailing `/`: `"legacy/"` covers everything under `legacy`.
- A glob: `"*/generated/*"` or `"src/reports/*.pas"`. The `*` also crosses `/`.

```toml
# example/server.toml
[summaries]
enabled = true
provider = "ollama"
exclude = ["vendor/", "third_party/"]
```

```toml
# .ragnar/config.toml, in the repository
[index]
summary_exclude = ["generated/", "src/old_reports/"]
```

Leave out code that nobody asks about in business terms: vendored libraries, generated code, migrations, dead modules. Each excluded file saves one model call.

A repository can only add exclusions. It cannot turn summaries on or choose the provider, the model or the key: those stay with the server.

## Preview before indexing

`ragnar summarize` sends local files to the configured model and prints the summaries. It writes nothing to the index and needs no database.

```bash
ragnar summarize src/Secretaria/uTrancamento.pas src/Mensalidades/uMulta.pas
```

```text
provider: ollama, model: qwen3:8b, url: http://127.0.0.1:11434/v1
## src/Secretaria/uTrancamento.pas
Registra o trancamento da matrícula de um aluno ...
```

Run it from the repository root, so the paths match the exclusion lists. To try a setting without editing a file, add `--set`:

```bash
ragnar summarize src/Secretaria/uTrancamento.pas --set summaries.provider=anthropic --set 'summaries.language="English"'
```

## Apply and verify

1. Under Compose, restart the indexer: `docker compose restart indexer`. Locally, run `ragnar index . myapp`.
2. The first run after the change reindexes the repository in full. Chunks whose text did not change keep their vectors, so only the summaries cost time. The log shows `summaries: N files to the model, M cached`.
3. Ask a question in business words:

   ```bash
   ragnar search "trancamento de matrícula" --repo myapp
   ```

   Summary hits carry the symbol `(summary)`. To get only summaries, pass `--kind summary`, or `kind = "summary"` to `search_code` through MCP.

Changing the model, `min_lines`, `language`, `summaries.exclude` or `index.summary_exclude` triggers a full reindex on the next run. Summaries already in the cache for the same model and language are reused.

## Settings

All keys of `[summaries]`. Empty keys fall back to `[llm]`; see [Configuration](configuration.md#summaries).

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Writes a summary of each code file at index time |
| `provider` | `""` | `ollama`, `anthropic` or `openai`. Empty: use `[llm]` |
| `url` | `""` | Base URL. Empty: the provider default, or `llm.url` when `provider` is empty |
| `model` | `""` | Model name. Empty: the provider default, or `llm.model` when `provider` is empty |
| `api_key_env` | `""` | Variable with the key. Empty: `llm.api_key_env` (`RAGNAR_LLM_API_KEY`) |
| `language` | `""` | Language of the summaries, as words: `"Brazilian Portuguese"`. Empty: the model chooses |
| `exclude` | `[]` | Directories (ending in `/`) and globs left out of summaries |
| `min_lines` | `30` | Shorter files get no summary |
| `max_chars` | `12000` | File text sent to the model |
| `concurrency` | `4` | Parallel model calls. Lower it for Ollama on a small machine |
| `timeout` | `300.0` | Seconds per model call. `0`: use `llm.timeout`, which guards searches and stays short |

`temperature` and `max_tokens` come from `[llm]`. A call that times out leaves only that file without a summary, and the run goes on. The file gets its summary when it changes, or on the next `ragnar index --full`, which asks the model only for the files missing from the cache. After three failed calls in a row, Ragnar treats the model as down and stops the run. The index keeps the previous commit, the summaries written so far stay in the cache, and the next run picks up from there.

When `summaries.provider` is set, the section stands alone: `url` and `model` never come from `[llm]`. This lets summaries run on Haiku while multi-query runs on Ollama, or the reverse.

## Troubleshooting

| Message | Cause | Fix |
| --- | --- | --- |
| `summaries.enabled requires a chat model` | No provider and no `[llm]` | Set `summaries.provider` |
| `the anthropic provider needs an API key: set RAGNAR_LLM_API_KEY` | The variable is empty in this process | Add it to `.rag/.env` or `example/.env` |
| `chat model unavailable at http://127.0.0.1:11434/v1 (ollama): ... refused` | Ollama is not running, or the indexer runs in a container | Start Ollama, or use `url = "http://host.docker.internal:11434/v1"` |
| `chat model unavailable ... 404` | The model is not pulled, or the name is wrong | `ollama pull <model>`, or check `model` |
| `chat model unavailable ... timed out` | The model is slow on this machine | Raise `summaries.timeout` or lower `summaries.concurrency` |

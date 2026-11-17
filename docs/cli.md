# Command line

Every `ragnar` command and its options. Run `ragnar <command> --help` for the same list.

[Português (Brasil)](pt-BR/cli.md)

Commands other than `mcp`, `embedder` and `embed-preload` first read `.rag/.env` in the current folder. A variable already set in the environment wins over the file. The variables are in [Configuration](configuration.md#runtime-variables).

`--set SECTION.KEY=VALUE` overrides one setting for a single run, such as `--set retrieval.default_k=10`. Repeat it for more settings. The commands that take it are marked below.

## Project

### `ragnar init`

Ragnarizes a project: writes `.ragnar/config.toml`, `.rag/`, the managed `.gitignore` block and the search rules for agents.

| Option | Default | Meaning |
| --- | --- | --- |
| `--dir` | `.` | Project folder |
| `--preset` | `balanced` | `economy`, `balanced` or `precise` |
| `--branch` | The repository's default branch | Branch the index follows |
| `--name` | Folder name | Repository name in the index |
| `--no-agent-notes` | Off | Leaves `AGENTS.md` and `CLAUDE.md` alone |
| `--force` | Off | Rewrites an existing `.ragnar/config.toml` |

### `ragnar doctor`

Checks the health of a ragnarized project. It fails when git tracks `.rag/.env` or `.rag/config.local.toml`, since the CLI does not trust them then.

| Option | Default | Meaning |
| --- | --- | --- |
| `--dir` | `.` | Project folder |
| `--indexed-commit` | None | Commit that `list_repos` reports, to measure how far behind the index is |

### `ragnar summarize FILE...`

Shows the summaries the configured model would write for local files. Writes nothing. Paths are relative to the repository root. Takes `--set`.

## Indexing

### `ragnar index SOURCE REPO`

Builds or updates the index of one repository. `SOURCE` is a local path or a git URL, and `REPO` is the name in the index. Needs `RAGNAR_DSN_WRITE`. Takes `--set`.

| Option | Default | Meaning |
| --- | --- | --- |
| `--ref` | `project.branch` | Branch or commit |
| `--full` | Off | Reindexes everything |
| `--no-secret-scan` | Off | Skips gitleaks. For tests only |

### `ragnar sync`

Indexes the branches listed in `repos.toml`. Needs `RAGNAR_DSN_WRITE`. Takes `--set`.

| Option | Default | Meaning |
| --- | --- | --- |
| `--config` | `repos.toml` | Path to the repository list |
| `--repo` | All | Only this repository. Must be in the list |
| `--watch` | Off | Keeps polling |
| `--interval` | `60` | Seconds between passes with `--watch` |

### `ragnar migrate`

Applies pending migrations on the primary. Needs `RAGNAR_DSN_WRITE`.

| Option | Meaning |
| --- | --- |
| `--resize-embeddings` | Clears the index and the code graph, and applies `embedding.dim`. Use it after a model change |

## Search and measurement

### `ragnar search QUERY`

Searches the read database directly, for debugging. Prints the hits as `search_code` would, and the time per phase on standard error. Needs `RAGNAR_DSN_READ`. Takes `--set`.

| Option | Default | Meaning |
| --- | --- | --- |
| `--repo` | Required | Repository name in the index |
| `-k` | `retrieval.default_k` | Number of hits |
| `--path-prefix` | None | Only paths under this prefix |
| `--kind` | All | `code`, `test`, `doc` or `summary` |

### `ragnar eval golden REPO SNAPSHOT`

Builds questions from the commits after `SNAPSHOT`, in the local repository `REPO`. See [Evaluation](evaluation.md).

| Option | Default | Meaning |
| --- | --- | --- |
| `--branch` | `main` | Branch whose commits become questions |
| `--out` | `.ragnar/eval/golden.jsonl` | Output file |

### `ragnar eval run REPO SNAPSHOT INDEX_NAME`

Compares grep, the current settings and their variants on the questions. `INDEX_NAME` is the name of the snapshot in the index. Needs `RAGNAR_DSN_READ`. Takes `--set`.

| Option | Default | Meaning |
| --- | --- | --- |
| `--golden` | `.ragnar/eval/golden.jsonl` | Question file |
| `--variant` | None | `NAME:SECTION.KEY=VALUE[,...]`, another configuration to score. Repeatable |
| `--out` | `.rag/eval/report.json` | Report file, as JSON |

### `ragnar stats LOG`

Usage report from the MCP server's audit lines. `LOG` is a file with the server's output, or `-` for standard input. See [MCP server](mcp.md#metrics).

| Option | Meaning |
| --- | --- |
| `--labels FILE` | Writes the questions that a later `read_code` answered, for `ragnar eval run` |

## Servers

### `ragnar mcp` and `ragnar embedder`

Run the MCP server and the bundled embedder.

| Option | Default | Meaning |
| --- | --- | --- |
| `--host` | `0.0.0.0` | Address to listen on. Use `127.0.0.1` to keep it local |
| `--port` | `8080` for `mcp`, `8000` for `embedder` | Port |

### `ragnar embed-preload`

Downloads the embedding model into `FASTEMBED_CACHE_PATH`. The image build runs it.

## Example infrastructure

### `ragnar init-env`

Creates `.env` with random passwords and tokens, plus `acl.json` and `repos.toml`. Clients added by hand to `acl.json` stay.

| Option | Default | Meaning |
| --- | --- | --- |
| `--dir` | `.` | Target folder |
| `--force` | Off | Recreates an existing `.env` |

### `ragnar token`

Creates a token and prints its `acl.json` entry. The token is printed once and is not stored.

| Option | Meaning |
| --- | --- |
| `--name` | Client name. Required |
| `--repos` | Comma-separated repositories, or `*` for all. Required |

### `ragnar smoke`

End-to-end check through MCP, plus the write barriers. Reads `RAGNAR_MCP_TOKEN`, and also `RAGNAR_MCP_TOKEN_RESTRICTED`, `RAGNAR_DSN_WRITE` and `RAGNAR_DSN_READ` when they are set.

| Option | Default | Meaning |
| --- | --- | --- |
| `--url` | `http://127.0.0.1:8080/mcp` | MCP endpoint |
| `--repo` | Required | Repository to search |
| `--replica-host` | None | A replica host, to check that it refuses writes |

### `ragnar load`

Search load through MCP. Reads `RAGNAR_MCP_TOKEN`.

| Option | Default | Meaning |
| --- | --- | --- |
| `--url` | `http://127.0.0.1:8080/mcp` | MCP endpoint |
| `--concurrency` | `8` | Parallel clients |
| `--duration` | `120` | Seconds |
| `--rate` | `0` | Total searches per second. `0` runs as fast as possible |
| `--queries` | Built-in list | File with one query per line |

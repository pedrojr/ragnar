# Benchmark

Measures Ragnar against what an agent does without it, on pinned open source projects in Delphi, Java, C# and Go.

[Português (Brasil)](README.pt-BR.md)

Clones and run output stay out of git, in `repos/` and `work/`. The questions, the commit pins and the report are versioned. The report comes in two languages: [English](report.html) and [Portuguese](report.pt-BR.html).

## What it measures

Every question runs twice on the same snapshot.

| Arm | What it does |
| --- | --- |
| Before Ragnar | One `git grep` per term, files ranked by distinct terms, then the top five files read in turn until the answer shows up. This is the best case for grep: a real agent cannot always tell it has the answer |
| After Ragnar | One `search_code` call, or `find_symbol` for a symbol question |

`agent.py` adds a third measurement: Claude Code answers a sample of questions, with local tools only and with the Ragnar tools too. Scripted arms show retrieval quality. Agent runs show whether that quality reaches a real session.

## Questions

| Source | Where | How the answer is known |
| --- | --- | --- |
| Curated | `questions/curated/` | Written by reading the code. Each has an expected file and evidence, a regular expression checked against the snapshot |
| History | `questions/history/` | The subject of a commit after the snapshot, and the files it changed |

Curated questions cover concepts, symbols, callers, cross-file flows, configuration, Portuguese questions about English code, Delphi forms, and questions with no answer in the repository.

**Questions split into `dev` and `holdout` by a hash of their id.** Failures of `dev` guided the fixes. `holdout` stays unread until the final report, so tuning cannot inflate it.

## Metrics

| Metric | Meaning |
| --- | --- |
| MRR | How high the first expected file ranks. 1 means first place |
| Recall@5, Recall@10 | Share of expected files in the top 5 or 10 |
| Solved | The answer reached the agent: the expected file appeared, and its evidence was in what the arm returned |
| Tokens | What the arm put in the context, at four characters per token |

## Running

You need Docker, `uv`, about 3 GB of disk, and a few hours on a CPU: embedding is the slow step. Each command is safe to run again.

```bash
uv sync --extra embed
docker compose -f benchmark/compose.yaml up -d --wait
RAGNAR_EMBED_THREADS=10 uv run ragnar embedder --host 127.0.0.1 --port 8001 &

uv run python benchmark/bench.py prepare                  # clones, snapshots, history questions
uv run python benchmark/validate_questions.py             # curated answers against the snapshots
uv run python benchmark/bench.py index --label final      # empty database, then every repository
uv run python benchmark/bench.py evaluate --label final
uv run python benchmark/report.py
```

`PYTHONPATH` selects the Ragnar under test, so an older tree runs through the same scripts:

```bash
git worktree add benchmark/work/v0-src <commit>
PYTHONPATH=benchmark/work/v0-src/src uv run python benchmark/bench.py index --label v0
```

`RAGNAR_BENCH_SUITE` points the same scripts at another folder, with its own `repos.toml`, `questions/`, `repos/` and `work/`. Use it for a private repository: its code, questions and results stay out of this repository. `RAGNAR_BENCH_DSN` points them at another Postgres server. It is an admin DSN with `{db}` in place of the database name, which the scripts fill in, such as `postgresql://postgres:<POSTGRES_PASSWORD>@127.0.0.1:15432/{db}` for the primary of the example infrastructure. `RAGNAR_BENCH_EMBEDDER` points them at another embeddings endpoint, instead of `http://127.0.0.1:8001/v1`.

```bash
RAGNAR_BENCH_SUITE=/path/to/private-suite uv run python benchmark/bench.py prepare
```

For agent runs, start an MCP server on the benchmark database with `benchmark/serve.py`, which listens on port 18080. Set the same `RAGNAR_BENCH_TOKEN` in both shells. Runs spend model usage, so the question count is explicit and each run has a budget cap:

```bash
RAGNAR_BENCH_TOKEN=<any string> uv run python benchmark/serve.py --db final
uv run python benchmark/agent.py --label final --questions 3 --model haiku
```

## Limits

- Commit subjects are imperfect questions, and they favor files that change often.
- A question answered by a large file favors grep: that file holds every word.
- The scripted grep arm reads Portuguese questions through an English `grep_query`, written by hand. Agent runs show what a real translation costs.
- Latency here comes from one database with no replicas, while the CPU was also embedding. It is not a production figure.

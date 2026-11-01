# Evaluation

Ragnar measures itself against the alternative: an agent with grep. Do this before you trust the index, and before you change the model, the chunking or the retrieval settings.

[Português (Brasil)](pt-BR/evaluation.md)

The `evaluate` and `tune` skills run this for you. This page explains what they do.

## Method

Each commit is a real question with a known answer. Its message is the query, and the files it changed are the expected result.

1. Pick a cutoff date. Index the repository as it was then, under another name, such as `myapp-snap`.
2. Build questions from the commits after that date. The snapshot predates them, so the answers cannot leak into the index.
3. Run the same questions through grep and through each configuration.
4. Compare them in pairs, question by question.

```bash
SNAP=$(git rev-list -1 --before="2026-09-01" main)
./scripts/index.sh /path/to/myapp myapp-snap --ref "$SNAP"     # from example/
ragnar eval golden /path/to/myapp "$SNAP" --branch main
ragnar eval run /path/to/myapp "$SNAP" myapp-snap
```

`ragnar eval run` needs `RAGNAR_DSN_READ` and the embedding settings.

## Reading the report

```text
42 questions
system           Recall@10    MRR   tokens
grep                 0.512  0.301    18400
configured           0.690  0.455     2900
configured vs_grep: recall gain +0.178, 95% CI [+0.081, +0.270]
```

| Column | Meaning |
| --- | --- |
| Recall@10 | Share of the expected files found in the top 10 |
| MRR | How high the first expected file appears. 1 means first place |
| tokens | Context per question. See the note below |
| 95% CI | Range of the gain, from a paired bootstrap |

These numbers illustrate the format. They are not a benchmark.

**A gain counts only if its whole interval is above zero.** An interval that contains zero is no evidence of gain.

**Set the decision rule before running.** Otherwise it is easy to rationalize any result.

**Below 30 questions, the report says the result is indicative.** Add questions written by your team, one JSON object per line:

```json
{"id": "q1", "query": "where do we retry failed payments?", "relevant": ["src/payments/retry.py"]}
```

**The tokens column compares orders of magnitude.** For grep, it estimates reading the top five files in full, which is what grep leaves for the agent to do. For Ragnar, it is the context actually returned. Both use four characters per token.

## Comparing configurations

`--variant` scores other settings on the same questions:

```bash
ragnar eval run /path/to/myapp "$SNAP" myapp-snap \
  --variant economy:project.preset=economy \
  --variant wide:retrieval.candidates=100,retrieval.neighbor_chunks=1
```

Each variant reports its gain over grep and over the current configuration. Settings under `[index]` change the chunks, so they need a snapshot built with those values.

## Limits

- Commit messages are imperfect questions. They read differently from what a developer would ask.
- Recall measures retrieval, not whether a task got done. The final test is whether your team finishes work faster and spends less.
- History reflects the past. Run the evaluation again after large changes.

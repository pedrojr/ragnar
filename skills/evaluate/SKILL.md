---
name: evaluate
description: Measures whether the Ragnar index helps. Compares the retrieval pipeline with a grep baseline on a golden set built from the git history, and reports Recall@10, MRR, context tokens and a paired confidence interval. Use for "evaluate the RAG", "is the index helping?", "avaliar o RAG", "measure recall", or before changing the model, the chunking or the retrieval settings.
---

# Evaluate the index

Each commit becomes a question: its message is the query, and the files it changed are the expected answer.

## Inputs

- Local repository path, and the branch.
- Cutoff date T. Default: 60 days ago. Questions come only from commits after T.
- Decision rule, **set before running**. Default: adopt only if the Recall@10 gain has a 95% confidence interval entirely above zero.

## Rules

1. **No leakage.** Index a snapshot of the repository at T under another name (`<name>-snap`). Never evaluate on the live index: the answer is already in it.
2. **Same questions for every system.** The comparison is paired.
3. **A small sample decides nothing.** With fewer than 30 questions, report the numbers as indicative. Suggest adding questions written by the team, in the format of `.ragnar/eval/golden.jsonl`.
4. **Leave production alone.** The snapshot has its own name. Offer to remove it at the end.

## Steps

The commands need access to the primary, the replicas and the embedder. Run the first step through the infrastructure scripts, and set `RAGNAR_DSN_READ` for the others (ask the user for the DSN; with the example Compose on one machine, it is the `ragnar_reader` role on `127.0.0.1:15432`).

1. Snapshot at T, and its index:
   ```bash
   SNAP=$(git -C <repo> rev-list -1 --before="<T>" <branch>)
   cd "$RAGNAR_HOME/example" && ./scripts/index.sh <repo> <name>-snap --ref "$SNAP"
   ```
2. Golden set from the commits after the snapshot:
   ```bash
   ragnar eval golden <repo> "$SNAP" --branch <branch>
   ```
3. Comparison:
   ```bash
   ragnar eval run <repo> "$SNAP" <name>-snap --out .rag/eval/report.json
   ```
4. Report a table: questions, Recall@10, MRR and tokens per system, the gain with its interval, and the decision under the rule.

## Reading the result

- An interval that contains zero is no evidence of gain. Check whether the golden set is small before drawing conclusions.
- The token column is a comparison of orders of magnitude. For grep, it estimates reading the top files in full, the cost grep leaves to the agent. For Ragnar, it is the context actually returned.
- Recall measures retrieval, not whether the task got done. Good retrieval does not guarantee a good answer.

Use the `tune` skill to compare settings.

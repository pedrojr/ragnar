---
name: tune
description: Chooses Ragnar's retrieval techniques with evidence. Compares settings (hybrid weights, code graph, reranking, query expansion, contextual retrieval, file summaries, token budget) on the project's own golden set and recommends the cheapest one that holds quality. Use for "tune the RAG", "otimizar a busca", "reduce token cost", "the results are bad", "try a reranker", "which preset should I use?".
---

# Tune retrieval

Goal: the cheapest configuration that does not lose quality. Change one thing at a time, and measure it.

## Before starting

A golden set and a snapshot index must exist. If they do not, run the `evaluate` skill first. Set the rule first. Default: a change is accepted if Recall@10 does not drop by more than 0.02 and it lowers tokens, or if it raises recall with an interval entirely above zero.

## Steps

1. Read `docs/configuration.md` and `docs/retrieval.md` in the plugin (`${CLAUDE_PLUGIN_ROOT}/docs/`) for the available settings.
2. Compare variants on the same questions. Each one is `name:section.key=value[,section.key=value]`:
   ```bash
   ragnar eval run <repo> "$SNAP" <name>-snap \
     --variant economy:project.preset=economy \
     --variant vector:retrieval.mode=vector \
     --variant lexical:retrieval.mode=lexical \
     --variant wide:retrieval.candidates=100,retrieval.neighbor_chunks=1 \
     --variant graph:retrieval.graph_weight=0.5 \
     --out .rag/eval/tune.json
   ```
3. Read each `vs_configured` line. It shows the gain over the current settings, with its interval.
4. Try the options that need a model only if the simple ones fall short, since they add latency and cost on every search:
   - Reranker: `rerank.provider=http`, with `rerank.url` and `rerank.model`.
   - Query expansion: `query.strategy=multi_query` or `hyde`, with `[llm]`.
   - Contextual retrieval: `contextual.enabled=true`, with `[llm]`. It changes the index, so it needs a new snapshot built with that setting. It costs one model call per chunk: estimate that with the user first.
   - File summaries: `summaries.enabled=true`, with `summaries.provider` or `[llm]`. Like contextual retrieval, it needs a new snapshot. It costs one model call per code file of 30 lines or more, cached by file content. Preview a few files first with `ragnar summarize <files>`.
5. Recommend one configuration, with the table that supports it.
6. Apply it after the user agrees. Retrieval settings go in the server's `server.toml`, and chunking settings in the project's `.ragnar/config.toml`. Restart the MCP server for the first; the second causes a full reindex.

## Rules

- Do not stack techniques without measuring each one. Their gains rarely add up.
- Never tune on the production index or on questions from before the snapshot.
- Fewer than 30 questions: state that the recommendation is indicative.

## Symptoms

| Symptom | First thing to try |
| --- | --- |
| Too many tokens per search | `project.preset=economy`, or lower `retrieval.max_context_tokens` |
| Finds the right file but the wrong part | `retrieval.neighbor_chunks=1`, or a smaller `index.chunk_budget` |
| Misses exact names | Raise `retrieval.lexical_weight` or `retrieval.symbol_weight` |
| Misses concepts | Raise `retrieval.vector_weight`, or try a reranker |
| All results come from one file | Lower `retrieval.max_chunks_per_file`, or raise `retrieval.diversity` |
| Generated code in the results | Add it to `index.exclude` |
| Misses the other end of a flow across files | `retrieval.graph_weight=0.5`, and the `trace_flow` and `call_graph` tools |
| Business questions miss legacy code with poor names | File summaries, on a cheap model |

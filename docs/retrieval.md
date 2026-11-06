# Retrieval techniques

Ragnar exposes the current RAG techniques as settings. Start with the defaults, then let [`ragnar eval`](evaluation.md) decide what to add: techniques cost money or latency, and their gains depend on the codebase.

[Português (Brasil)](pt-BR/retrieval.md)

## The pipeline

```mermaid
flowchart LR
    q[Question] --> x[Query expansion]
    x --> v[Vector list]
    x --> l[Lexical list]
    x --> w[Whole-file list]
    q --> s[Symbol list]
    v --> f[RRF fusion]
    l --> f
    w --> f
    s --> f
    f --> r[Rerank]
    r --> d[File cap + diversity]
    d --> n[Neighbor chunks]
    n --> b[Token budget]
    b --> c[References for the rest]
    c --> a[Answer]
```

Stages with an optional model are off by default.

## At query time

| Technique | Setting | Cost | Best for |
| --- | --- | --- | --- |
| Hybrid search | `retrieval.mode` | None | Almost every codebase. Keep it unless an evaluation says otherwise |
| Weighted RRF | `retrieval.*_weight`, `rrf_k` | None | Favoring names or concepts |
| Symbol boost | `retrieval.symbol_weight` | None | Questions that name a class or function |
| Whole-file list | `retrieval.file_weight` | Two more queries | Large files, where the words of a question sit far apart |
| Reranking | `[rerank]` | One model call per search | Top results in the wrong order |
| Multi-query | `query.strategy = "multi_query"` | One chat call and more embeddings per search | Vague questions |
| HyDE | `query.strategy = "hyde"` | One chat call per search | Natural-language questions on code with little documentation |
| Per-file cap | `retrieval.max_chunks_per_file` | None | One large file crowding out the rest |
| Diversity (MMR) | `retrieval.diversity` | None | Near-duplicate hits |
| Neighbor chunks | `retrieval.neighbor_chunks` | More tokens per search | Right spot, too little context |
| Token budget | `retrieval.max_context_tokens` | None | Keeping agent costs predictable |
| References | `retrieval.reference_hits` | About 25 tokens each | Keeping recall when the budget runs out |

### How the lists combine

**Reciprocal Rank Fusion** ranks by position, not score, so vector and lexical results combine without calibration. A chunk scores `weight / (rrf_k + rank)` per list. Chunks found by several lists rise.

**Lexical ranking** follows BM25 on plain Postgres. Each term weighs by its rarity, English and Portuguese stopwords drop out, words in the path and symbol score above the body, and light stemming lets "escaped" find `EscapeString` and "cancelamento" find `CancelarPedido`. Accents do not count: "configuração" and "configuracao" match the same code. A term in more than a quarter of the chunks ranks results, but it does not select them.

**The whole-file list** covers what chunks cannot: a chunk rarely holds every word of a question. Files holding the most query weight across their chunks join the fusion through their best chunk. This gives Ragnar the strength grep has on large files. Files are scored like BM25 documents, with chunks as the unit: a file gains from more chunks holding a term, and loses a little for its size. Without that penalty, a monorepo's utility units of 20 000 lines hold every word and win every question.

**Symbol boost** reads identifiers from the question and adds chunks that define them, so "where is `TOrder.Cancel` used?" lands on the definition. In a sentence, only words that read like code count.

**MMR** here measures overlap between chunk terms, not vectors. It costs no extra database work.

**The token budget** is an estimate of four characters per token. It keeps the order of magnitude right across models, and the first hit is always kept. Hits that do not fit return as references, with path, lines and symbol, so an agent still knows where to look next.

## At index time

| Technique | Setting | Cost | Best for |
| --- | --- | --- | --- |
| AST chunking (cAST) | On for languages with a grammar | None | Chunks that follow functions and classes |
| Line windows | `index.window_lines`, `window_overlap` | None | Files without a grammar |
| Identifier splitting | Always on | None | Matching `getUserById` with "user by id" |
| Comment attachment | Always on | None | Keeping documentation with its code |
| Form extraction | Delphi forms | None | Finding business logic in `.dfm` files |
| Contextual retrieval | `contextual.enabled` | One chat call per chunk | Chunks that make no sense alone |

Contextual retrieval has the highest cost here, and it applies to every chunk in a full reindex. Use a cheap model, and measure a sample first.

## Starting points

| Goal | Try |
| --- | --- |
| Reduce cost | `project.preset = "economy"` |
| Better top results, moderate cost | A reranker |
| Vague questions | `query.strategy = "multi_query"` |
| Legacy code with poor naming | `retrieval.vector_weight = 1.5`, `contextual.enabled = true` |

Measure each change. Gains rarely add up.

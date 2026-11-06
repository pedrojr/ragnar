"""Optional model calls, through any OpenAI-compatible /chat/completions endpoint, and reranking
through any /rerank endpoint (the Jina, Cohere and TEI request shape).

Nothing here is required: the default pipeline runs without a chat model or a reranker.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import httpx

from .chunker import Chunk, split_identifiers
from .config import Contextual, Llm, RagnarError, Rerank, Settings, Summaries

CONTEXT_PROMPT = """<document path="{path}">
{document}
</document>
<chunk>
{chunk}
</chunk>
Write one or two sentences that place this chunk within the document, to improve search retrieval.
Name the feature or responsibility it belongs to. Answer with the sentences only."""
SUMMARY_PROMPT = """<file path="{path}">
{code}
</file>
Declarations: {declarations}
Refers to: {refers}
Write two or three sentences on what this file does: the feature, the screens, data or tables it handles,
and its main routines. Use the words a person on the team would use when asking about it. Answer with
the sentences only."""
MULTI_QUERY_PROMPT = """Rewrite this code search question in {n} different ways, using the terms a developer
would expect to find in source code. Write one per line, with no numbering.

Question: {query}"""
HYDE_PROMPT = """Write a short, plausible code snippet that answers this question. Answer with code only.

Question: {query}"""


class Chat:
    def __init__(self, cfg: Llm):
        self.cfg = cfg
        api_key = os.environ.get(cfg.api_key_env, "")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(base_url=cfg.url.rstrip("/"), headers=headers, timeout=cfg.timeout)

    def complete(self, prompt: str) -> str:
        try:
            r = self._client.post("/chat/completions", json={
                "model": self.cfg.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": self.cfg.temperature,
                "max_tokens": self.cfg.max_tokens,
            })
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise RagnarError(f"chat model unavailable at {self.cfg.url}: {exc}") from None


class Reranker:
    def __init__(self, cfg: Rerank):
        self.cfg = cfg
        api_key = os.environ.get(cfg.api_key_env, "")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(headers=headers, timeout=30)

    def rerank(self, query: str, documents: list[str]) -> list[int]:
        """Document indexes, best first."""
        try:
            r = self._client.post(self.cfg.url, json={"model": self.cfg.model, "query": query, "documents": documents})
            r.raise_for_status()
            results = sorted(r.json()["results"], key=lambda x: x["relevance_score"], reverse=True)
            return [x["index"] for x in results]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise RagnarError(f"reranker unavailable at {self.cfg.url}: {exc}") from None


def chat_from(settings: Settings) -> Chat | None:
    needed = settings.query.strategy != "none" or settings.contextual.enabled or settings.summaries.enabled
    return Chat(settings.llm) if needed else None


def reranker_from(settings: Settings) -> Reranker | None:
    return Reranker(settings.rerank) if settings.rerank.provider == "http" else None


def expand_query(chat: Chat, strategy: str, variants: int, query: str) -> list[str]:
    """Extra texts to search with, besides the original question."""
    if strategy == "hyde":
        return [chat.complete(HYDE_PROMPT.format(query=query))]
    lines = chat.complete(MULTI_QUERY_PROMPT.format(n=variants, query=query)).splitlines()
    return [line.strip() for line in lines if line.strip()][:variants]


def contextualize(chat: Chat, cfg: Contextual, chunks: list[Chunk], documents: dict[str, bytes]) -> list[Chunk]:
    """Contextual retrieval: each chunk gets a model-written sentence on where it belongs.

    The sentence is embedded and searched with the chunk. It costs one model call per chunk,
    so run it on a cheap model and only after an evaluation shows the gain.
    """
    def one(c: Chunk) -> Chunk:
        document = documents.get(c.path, b"").decode(errors="replace")[: cfg.max_file_chars]
        note = chat.complete(CONTEXT_PROMPT.format(path=c.path, document=document, chunk=c.content))
        content = f"{note}\n{c.content}"
        return Chunk(c.path, c.symbol, c.language, c.kind, c.start_line, c.end_line, content,
                     c.search_text + "\n" + note + "\n" + split_identifiers(note), c.literals)

    with ThreadPoolExecutor(max_workers=max(1, cfg.concurrency)) as pool:
        return list(pool.map(one, chunks))


def summarize(chat: Chat, cfg: Summaries, files: list[dict]) -> list[str]:
    """One summary per file: {"path", "text", "declarations", "refers"} in, sentences out, in order.

    A question in business words ("onde calcula o desconto do convênio?") rarely shares words with the
    code. A summary written in those words gives the file a way in (Meta-RAG, Code-Craft).
    """
    def one(f: dict) -> str:
        return chat.complete(SUMMARY_PROMPT.format(path=f["path"], code=f["text"][: cfg.max_chars],
                                                   declarations=f["declarations"] or "(none)",
                                                   refers=f["refers"] or "(none)"))

    with ThreadPoolExecutor(max_workers=max(1, cfg.concurrency)) as pool:
        return list(pool.map(one, files))

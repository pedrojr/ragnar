"""Embedder local em Python (fastembed + ONNX Runtime, CPU) com API compatível com /v1/embeddings.

Rodar: ragnar embedder  (precisa do extra "embed": pip install ragnar[embed])
O modelo fica embutido na imagem; o pod novo sobe sem download. Sem estado: escala por CPU.
"""

from __future__ import annotations

import os

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

TEXTS = Counter("ragnar_embed_texts_total", "Textos transformados em vetor")
LATENCY = Histogram("ragnar_embed_seconds", "Latência por requisição", buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30))
MAX_INPUTS = 256


def build_app() -> Starlette:
    from fastembed import TextEmbedding

    model_name = os.environ.get("EMBED_MODEL", "jinaai/jina-embeddings-v2-base-code")
    threads = int(os.environ.get("EMBED_THREADS", "0")) or None
    model = TextEmbedding(model_name, cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"), threads=threads)
    # O modelo aceita 8192 tokens, mas o custo cresce com o quadrado do tamanho. Em CPU, 512 tokens
    # deixam a indexação 4x mais rápida que 1024; o BM25 continua vendo o chunk inteiro.
    max_tokens = int(os.environ.get("EMBED_MAX_TOKENS", "512"))
    tokenizer = getattr(model.model, "tokenizer", None)
    if tokenizer is not None:
        tokenizer.enable_truncation(max_length=max_tokens)
    batch_size = int(os.environ.get("EMBED_BATCH", "8"))

    def embed(texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in model.embed(texts, batch_size=batch_size)]

    async def embeddings(request: Request) -> Response:
        body = await request.json()
        texts = body.get("input")
        if isinstance(texts, str):
            texts = [texts]
        if not isinstance(texts, list) or not texts or not all(isinstance(t, str) for t in texts):
            return JSONResponse({"error": "input deve ser texto ou lista de textos"}, status_code=400)
        if len(texts) > MAX_INPUTS:
            return JSONResponse({"error": f"no máximo {MAX_INPUTS} textos por requisição"}, status_code=400)
        if body.get("model") not in (None, model_name):
            return JSONResponse({"error": f"este embedder serve só {model_name}"}, status_code=400)
        with LATENCY.time():
            vectors = await run_in_threadpool(embed, texts)
        TEXTS.inc(len(texts))
        return JSONResponse({
            "object": "list",
            "model": model_name,
            "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)],
        })

    async def models(_: Request) -> Response:
        return JSONResponse({"object": "list", "data": [{"id": model_name, "object": "model"}]})

    async def healthz(_: Request) -> Response:
        return PlainTextResponse("ok")

    async def metrics(_: Request) -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return Starlette(routes=[
        Route("/v1/embeddings", embeddings, methods=["POST"]),
        Route("/v1/models", models, methods=["GET"]),
        Route("/healthz", healthz, methods=["GET"]),
        Route("/metrics", metrics, methods=["GET"]),
    ])


def preload() -> None:
    """Baixa o modelo para FASTEMBED_CACHE_PATH (usado no build da imagem)."""
    from fastembed import TextEmbedding

    TextEmbedding(os.environ.get("EMBED_MODEL", "jinaai/jina-embeddings-v2-base-code"),
                  cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"))

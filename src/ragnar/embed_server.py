"""Local Python embedder (fastembed + ONNX Runtime, CPU) with a /v1/embeddings compatible API.

Run: ragnar embedder  (needs the "embed" extra: pip install ragnar-rag[embed])
The model is baked into the image, so a new pod starts without a download. Stateless: scales on CPU.
"""

from __future__ import annotations

import os

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from .config import load_settings

TEXTS = Counter("ragnar_embed_texts_total", "Texts turned into vectors")
LATENCY = Histogram("ragnar_embed_seconds", "Latency per request", buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30))
MAX_INPUTS = 256


def build_app() -> Starlette:
    from fastembed import TextEmbedding

    model_name = load_settings().embedding.model
    threads = int(os.environ.get("RAGNAR_EMBED_THREADS", "0")) or None
    model = TextEmbedding(model_name, cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"), threads=threads)
    # The model accepts 8192 tokens, but the cost grows with the square of the length. On CPU,
    # 512 tokens index 4x faster than 1024; the lexical index still sees the whole chunk.
    max_tokens = int(os.environ.get("RAGNAR_EMBED_MAX_TOKENS", "512"))
    tokenizer = getattr(model.model, "tokenizer", None)
    if tokenizer is not None:
        tokenizer.enable_truncation(max_length=max_tokens)
    batch_size = int(os.environ.get("RAGNAR_EMBED_BATCH", "8"))

    def embed(texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in model.embed(texts, batch_size=batch_size)]

    async def embeddings(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return JSONResponse({"error": "the body must be a JSON object"}, status_code=400)
        texts = body.get("input")
        if isinstance(texts, str):
            texts = [texts]
        if not isinstance(texts, list) or not texts or not all(isinstance(t, str) for t in texts):
            return JSONResponse({"error": "input must be a string or a list of strings"}, status_code=400)
        if len(texts) > MAX_INPUTS:
            return JSONResponse({"error": f"at most {MAX_INPUTS} texts per request"}, status_code=400)
        if body.get("model") not in (None, model_name):
            return JSONResponse({"error": f"this embedder serves only {model_name}"}, status_code=400)
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
    """Downloads the model to FASTEMBED_CACHE_PATH (used while building the image)."""
    from fastembed import TextEmbedding

    TextEmbedding(load_settings().embedding.model, cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"))

"""Embedding client: any endpoint compatible with /v1/embeddings.

Works with the bundled embedder (ragnar.embed_server), Ollama, vLLM, LiteLLM, or an
external provider. FakeEmbedder produces deterministic vectors for tests.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import time
from functools import lru_cache

import httpx

from .config import Embedding, RagnarError, Settings, load_settings

ATTEMPTS = 3
BACKOFF_SECONDS = 0.5


def to_pgvector(vector: list[float]) -> str:
    """pgvector text format: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


class Embedder:
    def __init__(self, cfg: Embedding, timeout: float = 300):
        self.model, self.dim, self.batch = cfg.model, cfg.dim, cfg.batch
        self.doc_prefix, self.query_prefix = cfg.doc_prefix, cfg.query_prefix
        self.send_dimensions = cfg.send_dimensions
        api_key = os.environ.get(cfg.api_key_env, "")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(base_url=cfg.url.rstrip("/"), headers=headers, timeout=timeout)
        self.embed_query = lru_cache(maxsize=1024)(self._embed_query)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            out += self._embed([self.doc_prefix + t for t in texts[i : i + self.batch]])
        return out

    def _embed_query(self, text: str) -> tuple[float, ...]:
        return tuple(self._embed([self.query_prefix + text])[0])

    def ping(self) -> None:
        self._client.get("/models").raise_for_status()

    def _embed(self, texts: list[str]) -> list[list[float]]:
        body: dict = {"model": self.model, "input": texts}
        if self.send_dimensions:
            body["dimensions"] = self.dim
        last: Exception | None = None
        for attempt in range(ATTEMPTS):  # a network failure or a 5xx is often a replica restarting
            if attempt:
                time.sleep(BACKOFF_SECONDS * 2 ** (attempt - 1))
            try:
                r = self._client.post("/embeddings", json=body)
                if r.status_code >= 500:
                    last = RagnarError(f"embedder answered {r.status_code}")
                    continue
                if r.status_code >= 400:
                    raise RagnarError(f"embedder refused the request ({r.status_code}): {r.text[:200]}")
                data = sorted(r.json()["data"], key=lambda d: d["index"])
                vectors = [d["embedding"] for d in data]
                if any(len(v) != self.dim for v in vectors):
                    raise RagnarError(
                        f"model {self.model} returned dimension {len(vectors[0])}, expected {self.dim} (embedding.dim)"
                    )
                return vectors
            except httpx.TransportError as exc:
                last = exc
        raise RagnarError(f"embedder unavailable at {self._client.base_url}: {last}")


class FakeEmbedder:
    """Hashes words into `dim` positions and normalizes. Texts that share words end up close."""

    def __init__(self, dim: int = 768):
        self.model, self.dim = "fake-hash", dim

    def _vector(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            h = int.from_bytes(hashlib.blake2b(word.encode(), digest_size=8).digest(), "big")
            v[h % self.dim] += 1.0 if (h >> 32) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in v))
        if norm == 0:  # a null vector has an undefined cosine distance in pgvector
            v[0], norm = 1.0, 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> tuple[float, ...]:
        return tuple(self._vector(text))

    def ping(self) -> None:
        return None


QUERY_TIMEOUT = 10.0  # one query vector; a search should not wait the indexing timeout for it


def from_settings(settings: Settings | None = None, timeout: float = 300) -> Embedder | FakeEmbedder:
    cfg = (settings or load_settings()).embedding
    return FakeEmbedder(cfg.dim) if cfg.provider == "fake" else Embedder(cfg, timeout)

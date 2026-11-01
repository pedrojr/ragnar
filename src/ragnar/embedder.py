"""Cliente de embeddings: qualquer endpoint compatível com /v1/embeddings.

Serve para o embedder deste exemplo (ragnar.embed_server), Ollama, vLLM, LiteLLM
ou provedor externo. FakeEmbedder gera vetores determinísticos para testes.
"""

from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache

import httpx

from .config import EmbedConfig, RagError, embed_config


def to_pgvector(vector: list[float]) -> str:
    """Formato de texto do pgvector: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


class Embedder:
    def __init__(self, cfg: EmbedConfig, batch: int = 16, timeout: float = 300):
        self.model, self.dim, self.batch = cfg.model, cfg.dim, batch
        self.doc_prefix, self.query_prefix = cfg.doc_prefix, cfg.query_prefix
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
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
        last: Exception | None = None
        for _ in range(2):  # uma nova tentativa em falha de rede ou 5xx
            try:
                r = self._client.post("/embeddings", json={"model": self.model, "input": texts})
                if r.status_code >= 500:
                    last = RagError(f"embedder respondeu {r.status_code}")
                    continue
                r.raise_for_status()
                data = sorted(r.json()["data"], key=lambda d: d["index"])
                vectors = [d["embedding"] for d in data]
                if any(len(v) != self.dim for v in vectors):
                    raise RagError(
                        f"modelo {self.model} devolveu dimensão {len(vectors[0])}, esperado {self.dim} (EMBED_DIM)"
                    )
                return vectors
            except httpx.TransportError as exc:
                last = exc
        raise RagError(f"embedder indisponível em {self._client.base_url}: {last}")


class FakeEmbedder:
    """Hash das palavras em `dim` posições, normalizado. Textos com palavras em comum ficam próximos."""

    def __init__(self, dim: int = 768):
        self.model, self.dim = "fake-hash", dim

    def _vector(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            h = int.from_bytes(hashlib.blake2b(word.encode(), digest_size=8).digest(), "big")
            v[h % self.dim] += 1.0 if (h >> 32) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in v))
        if norm == 0:  # vetor nulo dá distância de cosseno indefinida no pgvector
            v[0], norm = 1.0, 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> tuple[float, ...]:
        return tuple(self._vector(text))

    def ping(self) -> None:
        return None


def from_env() -> Embedder | FakeEmbedder:
    cfg = embed_config()
    if cfg.provider == "fake":
        return FakeEmbedder(cfg.dim)
    if cfg.provider != "http":
        raise RagError(f"EMBED_PROVIDER inválido: {cfg.provider} (use http ou fake)")
    return Embedder(cfg)

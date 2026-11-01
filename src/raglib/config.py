"""Configuração por variável de ambiente.

Leitura e escrita usam DSNs separados: só o indexador e a migração recebem
RAG_DSN_WRITE (primário); o MCP recebe só RAG_DSN_READ (réplicas).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class RagError(Exception):
    """Erro previsto: a mensagem vai para o usuário ou para o modelo como está."""


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise RagError(f"defina a variável de ambiente {name}")
    return value


def read_dsn() -> str:
    return env("RAG_DSN_READ")


def write_dsn() -> str:
    return env("RAG_DSN_WRITE")


@dataclass(frozen=True)
class EmbedConfig:
    provider: str  # http | fake
    url: str
    model: str
    api_key: str
    dim: int
    doc_prefix: str
    query_prefix: str


def embed_config() -> EmbedConfig:
    provider = os.environ.get("EMBED_PROVIDER", "http")
    return EmbedConfig(
        provider=provider,
        url=os.environ.get("EMBED_URL", "http://127.0.0.1:8001/v1"),
        model=os.environ.get("EMBED_MODEL", "jinaai/jina-embeddings-v2-base-code"),
        api_key=os.environ.get("EMBED_KEY", ""),
        dim=int(os.environ.get("EMBED_DIM", "768")),
        doc_prefix=os.environ.get("EMBED_DOC_PREFIX", ""),
        query_prefix=os.environ.get("EMBED_QUERY_PREFIX", ""),
    )

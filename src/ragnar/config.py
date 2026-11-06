"""Settings, resolved in this order (last wins):

defaults < preset < ragnar.toml < ragnar.local.toml < RAGNAR__SECTION__KEY environment variables.

Secrets never live in the files: a section names the environment variable that holds
the key (api_key_env). Reads and writes use separate DSNs: only the indexer and the
migrations receive RAGNAR_DSN_WRITE (primary); the MCP server receives only
RAGNAR_DSN_READ (replicas).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_FILE = "ragnar.toml"
LOCAL_CONFIG_FILE = "ragnar.local.toml"
CHUNKER_VERSION = 5  # bump when chunk boundaries or chunk fields change: forces a full reindex


class RagnarError(Exception):
    """Expected error: the message reaches the user or the model as it is."""


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise RagnarError(f"set the environment variable {name}")
    return value


def read_dsn() -> str:
    return env("RAGNAR_DSN_READ")


def write_dsn() -> str:
    return env("RAGNAR_DSN_WRITE")


@dataclass(frozen=True)
class Project:
    name: str = ""
    branch: str = "main"
    preset: str = "balanced"
    languages: tuple[str, ...] = ()  # empty: every language in the registry


@dataclass(frozen=True)
class Index:
    include: tuple[str, ...] = ()  # extra globs, indexed with the line-window chunker
    exclude: tuple[str, ...] = ()
    max_file_kb: int = 2048  # legacy units reach megabytes, and they are what people need to find
    chunk_budget: int = 1800  # non-whitespace characters per chunk
    window_lines: int = 80  # line-window chunker, for files without a grammar
    window_overlap: int = 10
    index_tests: bool = True
    index_docs: bool = True
    index_agent_notes: bool = False  # AGENTS.md, CLAUDE.md, copilot-instructions: agents already read them
    secret_scan: bool = True
    secret_action: str = "fail"  # fail: stop the run | skip: leave the files with findings out
    legacy_encoding: str = "cp1252"  # fallback for sources that are not UTF-8


@dataclass(frozen=True)
class Embedding:
    provider: str = "http"  # http | fake
    url: str = "http://127.0.0.1:8001/v1"
    model: str = "jinaai/jina-embeddings-v2-base-code"
    api_key_env: str = "RAGNAR_EMBEDDING_API_KEY"
    dim: int = 768
    send_dimensions: bool = False  # send "dimensions" for models with shortened vectors
    doc_prefix: str = ""
    query_prefix: str = ""
    batch: int = 16
    concurrency: int = 2


@dataclass(frozen=True)
class Retrieval:
    mode: str = "hybrid"  # hybrid | vector | lexical
    default_k: int = 5
    candidates: int = 50  # per list, before fusion
    rrf_k: int = 30
    vector_weight: float = 1.0
    lexical_weight: float = 1.0
    symbol_weight: float = 1.0  # exact symbol names in the query; 0 turns it off
    file_weight: float = 2.0  # files holding the query words across chunks; 0 turns it off
    literal_weight: float = 1.0  # quoted text in the question: chunks whose literals hold it; 0 turns it off
    graph_weight: float = 0.0  # neighbors of the best hits in the code graph; 0 turns it off
    graph_seeds: int = 5  # best hits whose neighbors join the graph list
    doc_weight: float = 0.5  # scales the fused score of documentation hits; below 1, code wins ties
    rank_normalization: int = 1  # ts_rank length normalization: 1 divides by 1 + log(length), 2 by length
    ef_search: int = 100
    max_chunks_per_file: int = 3  # 0 turns it off
    diversity: float = 0.0  # 0 = pure relevance, 1 = pure novelty (MMR)
    neighbor_chunks: int = 0  # adjacent chunks added around each hit
    max_context_tokens: int = 2000  # 0 turns it off. In real agent sessions, 4000 cost more context than grep
    reference_hits: int = 8  # hits that did not fit, returned as path and symbol only
    excerpts: bool = True  # hits after the first keep only the lines around the query words


@dataclass(frozen=True)
class Rerank:
    provider: str = "none"  # none | http
    url: str = ""
    model: str = ""
    api_key_env: str = "RAGNAR_RERANK_API_KEY"
    candidates: int = 30


@dataclass(frozen=True)
class Query:
    strategy: str = "none"  # none | multi_query | hyde
    variants: int = 3


@dataclass(frozen=True)
class Contextual:
    enabled: bool = False
    max_file_chars: int = 20000
    concurrency: int = 4


@dataclass(frozen=True)
class Summaries:
    enabled: bool = False  # one model call per file the first time; cached by file content afterwards
    min_lines: int = 30  # shorter files explain themselves
    max_chars: int = 12000  # of the file sent to the model
    concurrency: int = 4


@dataclass(frozen=True)
class Llm:
    url: str = ""
    model: str = ""
    api_key_env: str = "RAGNAR_LLM_API_KEY"
    temperature: float = 0.0
    max_tokens: int = 300
    timeout: float = 60.0


@dataclass(frozen=True)
class Settings:
    project: Project = field(default_factory=Project)
    index: Index = field(default_factory=Index)
    embedding: Embedding = field(default_factory=Embedding)
    retrieval: Retrieval = field(default_factory=Retrieval)
    rerank: Rerank = field(default_factory=Rerank)
    query: Query = field(default_factory=Query)
    contextual: Contextual = field(default_factory=Contextual)
    summaries: Summaries = field(default_factory=Summaries)
    llm: Llm = field(default_factory=Llm)


PRESETS: dict[str, dict[str, dict[str, Any]]] = {
    "economy": {"retrieval": {"default_k": 3, "max_context_tokens": 1000, "max_chunks_per_file": 2}},
    "balanced": {},
    "precise": {"retrieval": {"default_k": 10, "candidates": 100, "ef_search": 200, "neighbor_chunks": 1,
                              "diversity": 0.2, "max_context_tokens": 8000}},
}
SECTIONS = {f.name: f.default_factory for f in dataclasses.fields(Settings)}
CHOICES = {
    ("project", "preset"): tuple(PRESETS),
    ("embedding", "provider"): ("http", "fake"),
    ("index", "secret_action"): ("fail", "skip"),
    ("retrieval", "mode"): ("hybrid", "vector", "lexical"),
    ("rerank", "provider"): ("none", "http"),
    ("query", "strategy"): ("none", "multi_query", "hyde"),
}
# A repository may tune how it is chunked, but it never picks endpoints or turns off
# the secret scan: the index worker runs with credentials the repository does not own.
SERVER_OWNED = {"secret_scan", "secret_action"}
REPO_OWNED = {"index": {f.name for f in dataclasses.fields(Index)} - SERVER_OWNED}


def _coerce(section: str, key: str, type_name: str, value: Any) -> Any:
    wrong = RagnarError(f"{section}.{key} must be {type_name}, got {value!r}")
    if type_name == "bool":
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        if not isinstance(value, bool):
            raise wrong
        return value
    if type_name == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            raise wrong from None
    if type_name == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            raise wrong from None
    if type_name == "str":
        return str(value)
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise wrong
    return tuple(value)


def _build(values: dict[str, dict[str, Any]]) -> Settings:
    sections = {}
    for name, factory in SECTIONS.items():
        data = dict(values.get(name, {}))
        known = {f.name: f for f in dataclasses.fields(factory)}
        unknown = sorted(set(data) - set(known))
        if unknown:
            raise RagnarError(f"unknown setting {name}.{unknown[0]}; valid keys: {', '.join(sorted(known))}")
        kwargs = {k: _coerce(name, k, str(known[k].type).split("[")[0].replace("tuple", "list"), v)
                  for k, v in data.items()}
        sections[name] = factory(**kwargs)
    unknown = sorted(set(values) - set(SECTIONS))
    if unknown:
        raise RagnarError(f"unknown section [{unknown[0]}]; valid sections: {', '.join(SECTIONS)}")
    settings = Settings(**sections)
    _validate(settings)
    return settings


def _validate(s: Settings) -> None:
    for (section, key), allowed in CHOICES.items():
        value = getattr(getattr(s, section), key)
        if value not in allowed:
            raise RagnarError(f"{section}.{key} must be one of {', '.join(allowed)}, got {value!r}")
    if s.retrieval.rank_normalization not in (0, 1, 2, 4, 8, 16, 32):
        raise RagnarError("retrieval.rank_normalization must be a ts_rank normalization flag: 0, 1, 2, 4, 8, 16 or 32")
    if s.retrieval.doc_weight < 0:
        raise RagnarError("retrieval.doc_weight must not be negative")
    if not 0 <= s.retrieval.diversity <= 1:
        raise RagnarError("retrieval.diversity must be between 0 and 1")
    if s.rerank.provider == "http" and not (s.rerank.url and s.rerank.model):
        raise RagnarError("rerank.provider = http requires rerank.url and rerank.model")
    if (s.query.strategy != "none" or s.contextual.enabled or s.summaries.enabled) and not (s.llm.url and s.llm.model):
        raise RagnarError("query.strategy, contextual.enabled and summaries.enabled require llm.url and llm.model")
    if s.index.window_overlap >= s.index.window_lines:
        raise RagnarError("index.window_overlap must be smaller than index.window_lines")


def _merge(base: dict[str, dict[str, Any]], extra: dict[str, Any]) -> None:
    for section, values in extra.items():
        if not isinstance(values, dict):
            raise RagnarError(f"[{section}] must be a section, got {values!r}")
        base.setdefault(section, {}).update(values)


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise RagnarError(f"invalid TOML in {path}: {exc}") from None


def parse_override(text: str) -> tuple[str, str, Any]:
    """'retrieval.mode=vector' -> ('retrieval', 'mode', 'vector')."""
    path, sep, raw = text.partition("=")
    section, dot, key = path.strip().partition(".")
    if not sep or not dot or not key:
        raise RagnarError(f"expected section.key=value, got {text!r}")
    try:
        value = tomllib.loads(f"v = {raw.strip()}")["v"]
    except tomllib.TOMLDecodeError:
        value = raw.strip()
    return section, key, value


def _env_overrides(environ: dict[str, str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, value in environ.items():
        parts = name.split("__")
        if len(parts) == 3 and parts[0] == "RAGNAR":
            out.setdefault(parts[1].lower(), {})[parts[2].lower()] = value
    return out


def load_settings(root: str | Path | None = None, overrides: list[str] | None = None,
                  environ: dict[str, str] | None = None) -> Settings:
    environ = dict(os.environ) if environ is None else environ
    explicit = environ.get("RAGNAR_CONFIG")
    base = Path(root) if root is not None else Path.cwd()
    main_file = Path(explicit) if explicit else base / CONFIG_FILE
    if explicit and not main_file.is_file():
        raise RagnarError(f"RAGNAR_CONFIG points to a missing file: {main_file}")

    layers: list[dict[str, Any]] = []
    if main_file.is_file():
        layers.append(_read_toml(main_file))
    local = main_file.parent / LOCAL_CONFIG_FILE
    if local.is_file():
        layers.append(_read_toml(local))
    layers.append(_env_overrides(environ))
    extra: dict[str, dict[str, Any]] = {}
    for item in overrides or []:
        section, key, value = parse_override(item)
        extra.setdefault(section, {})[key] = value
    layers.append(extra)

    preset = next((layer["project"]["preset"] for layer in reversed(layers)
                   if "preset" in layer.get("project", {})), "balanced")
    if preset not in PRESETS:
        raise RagnarError(f"project.preset must be one of {', '.join(PRESETS)}, got {preset!r}")
    merged: dict[str, dict[str, Any]] = {}
    _merge(merged, PRESETS[preset])
    for layer in layers:
        _merge(merged, layer)
    return _build(merged)


def with_repo_config(settings: Settings, toml_text: str | None) -> Settings:
    """Applies the [index] section of a repository's own ragnar.toml, and nothing else."""
    if not toml_text:
        return settings
    try:
        data = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as exc:
        raise RagnarError(f"invalid {CONFIG_FILE} in the repository: {exc}") from None
    chosen = {k: v for k, v in data.get("index", {}).items() if k in REPO_OWNED["index"]}
    project = {"languages": data["project"]["languages"]} if "languages" in data.get("project", {}) else {}
    merged = {name: dataclasses.asdict(getattr(settings, name)) for name in SECTIONS}
    merged["index"].update(chosen)
    merged["project"].update(project)
    return _build(merged)


def index_fingerprint(settings: Settings) -> str:
    """Changes when an already indexed repository needs a full reindex."""
    data = {
        "chunker": CHUNKER_VERSION,
        "index": {k: v for k, v in dataclasses.asdict(settings.index).items() if k not in SERVER_OWNED},
        "languages": settings.project.languages,
        "doc_prefix": settings.embedding.doc_prefix,
        "contextual": settings.contextual.enabled and settings.llm.model,
        "summaries": settings.summaries.enabled and [settings.llm.model, settings.summaries.min_lines],
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]

"""Settings: layer order, validation, and what a repository may change."""

from __future__ import annotations

import pytest

from ragnar.config import PRESETS, RagnarError, index_fingerprint, load_settings, parse_override, with_repo_config


def write(tmp_path, text, name="ragnar.toml"):
    (tmp_path / name).write_text(text, encoding="utf-8")


def test_defaults_without_a_file(tmp_path):
    s = load_settings(tmp_path, environ={})
    assert (s.project.branch, s.project.preset, s.retrieval.mode) == ("main", "balanced", "hybrid")


def test_layers_apply_in_order(tmp_path):
    write(tmp_path, '[project]\npreset = "economy"\n[retrieval]\ncandidates = 40\nrrf_k = 10\n')
    write(tmp_path, "[retrieval]\nrrf_k = 20\n", "ragnar.local.toml")
    s = load_settings(tmp_path, overrides=["retrieval.candidates=70"], environ={"RAGNAR__RETRIEVAL__DEFAULT_K": "9"})
    assert s.retrieval.max_chunks_per_file == PRESETS["economy"]["retrieval"]["max_chunks_per_file"]
    assert s.retrieval.rrf_k == 20  # local file beats ragnar.toml
    assert s.retrieval.default_k == 9  # environment beats the preset
    assert s.retrieval.candidates == 70  # command line beats everything


def test_explicit_config_path(tmp_path):
    write(tmp_path, "[retrieval]\nrrf_k = 33\n", "other.toml")
    assert load_settings(environ={"RAGNAR_CONFIG": str(tmp_path / "other.toml")}).retrieval.rrf_k == 33
    with pytest.raises(RagnarError, match="missing file"):
        load_settings(environ={"RAGNAR_CONFIG": str(tmp_path / "nope.toml")})


@pytest.mark.parametrize("text, message", [
    ("[retrieval]\nmodee = 1\n", "unknown setting retrieval.modee"),
    ("[retreival]\nmode = 'vector'\n", r"unknown section \[retreival\]"),
    ("[retrieval]\nmode = 'magic'\n", "retrieval.mode must be one of"),
    ("[retrieval]\ncandidates = 'many'\n", "retrieval.candidates must be int"),
    ("[retrieval]\ndiversity = 2\n", "between 0 and 1"),
    ("[rerank]\nprovider = 'http'\n", "requires rerank.url"),
    ("[query]\nstrategy = 'hyde'\n", "require llm.url"),
    ("[contextual]\nenabled = true\n", "require llm.url"),
    ("[index]\nwindow_lines = 5\nwindow_overlap = 5\n", "window_overlap"),
    ("[project]\npreset = 'turbo'\n", "project.preset must be one of"),
    ("[retrieval\n", "invalid TOML"),
])
def test_invalid_settings_explain_the_problem(tmp_path, text, message):
    write(tmp_path, text)
    with pytest.raises(RagnarError, match=message):
        load_settings(tmp_path, environ={})


def test_lists_accept_commas_from_the_environment(tmp_path):
    s = load_settings(tmp_path, environ={"RAGNAR__INDEX__EXCLUDE": "a/**, b/**"})
    assert s.index.exclude == ("a/**", "b/**")


def test_parse_override_keeps_types():
    assert parse_override("retrieval.diversity=0.5") == ("retrieval", "diversity", 0.5)
    assert parse_override("retrieval.mode=vector") == ("retrieval", "mode", "vector")
    with pytest.raises(RagnarError):
        parse_override("nokey")


def test_repository_config_cannot_change_endpoints_or_the_secret_scan(tmp_path):
    base = load_settings(tmp_path, environ={})
    s = with_repo_config(base, """
[project]
languages = ["delphi"]
[index]
chunk_budget = 900
secret_scan = false
[embedding]
url = "https://attacker.example/v1"
""")
    assert s.index.chunk_budget == 900 and s.project.languages == ("delphi",)
    assert s.index.secret_scan is True
    assert s.embedding.url == base.embedding.url
    assert with_repo_config(base, None) is base


def test_fingerprint_follows_chunking_settings_only(tmp_path):
    base = load_settings(tmp_path, environ={})
    assert index_fingerprint(base) == index_fingerprint(load_settings(tmp_path, ["retrieval.rrf_k=5"], {}))
    assert index_fingerprint(base) != index_fingerprint(load_settings(tmp_path, ["index.chunk_budget=900"], {}))

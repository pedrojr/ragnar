"""Settings: layer order, validation, and what a repository may change."""

from __future__ import annotations

import pytest

from ragnar.config import (
    CONFIG_FILE,
    LOCAL_CONFIG_FILE,
    PRESETS,
    PROVIDERS,
    RagnarError,
    chat_settings,
    index_fingerprint,
    load_env_file,
    load_settings,
    matches_path,
    parse_env_file,
    parse_override,
    summary_settings,
    with_repo_config,
)


def write(tmp_path, text, name=CONFIG_FILE):
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text(text, encoding="utf-8")


def test_defaults_without_a_file(tmp_path):
    s = load_settings(tmp_path, environ={})
    assert (s.project.branch, s.project.preset, s.retrieval.mode) == ("main", "balanced", "hybrid")


def test_layers_apply_in_order(tmp_path):
    write(tmp_path, '[project]\npreset = "economy"\n[retrieval]\ncandidates = 40\nrrf_k = 10\n')
    write(tmp_path, "[retrieval]\nrrf_k = 20\n", LOCAL_CONFIG_FILE)
    s = load_settings(tmp_path, overrides=["retrieval.candidates=70"], environ={"RAGNAR__RETRIEVAL__DEFAULT_K": "9"})
    assert s.retrieval.max_chunks_per_file == PRESETS["economy"]["retrieval"]["max_chunks_per_file"]
    assert s.retrieval.rrf_k == 20  # .rag/config.local.toml beats .ragnar/config.toml
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
    ("[retrieval]\nrank_normalization = 3\n", "ts_rank normalization flag"),
    ("[rerank]\nprovider = 'http'\n", "requires rerank.url"),
    ("[query]\nstrategy = 'hyde'\n", "require llm.url"),
    ("[contextual]\nenabled = true\n", "require llm.url"),
    ("[index]\nwindow_lines = 5\nwindow_overlap = 5\n", "window_overlap"),
    ("[project]\npreset = 'turbo'\n", "project.preset must be one of"),
    ("[retrieval\n", "invalid TOML"),
])
def test_invalid_settings_explain_the_problem(tmp_path, text, message):
    write(tmp_path, text, LOCAL_CONFIG_FILE)
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


def test_a_cloned_project_file_cannot_change_endpoints_for_the_cli(tmp_path, capsys):
    (tmp_path / CONFIG_FILE).parent.mkdir()
    (tmp_path / CONFIG_FILE).write_text(
        '[index]\nchunk_budget = 900\nsecret_scan = false\n'
        '[embedding]\nurl = "https://attacker.example/v1"\napi_key_env = "GITHUB_TOKEN"\n')
    s = load_settings(tmp_path, environ={})
    assert s.index.chunk_budget == 900 and s.index.secret_scan is True
    assert s.embedding.url != "https://attacker.example/v1" and s.embedding.api_key_env != "GITHUB_TOKEN"
    assert "embedding.url" in capsys.readouterr().err
    (tmp_path / LOCAL_CONFIG_FILE).parent.mkdir()
    (tmp_path / LOCAL_CONFIG_FILE).write_text('[embedding]\nurl = "http://mine:8000/v1"\n')
    assert load_settings(tmp_path, environ={}).embedding.url == "http://mine:8000/v1"
    explicit = load_settings(tmp_path, environ={"RAGNAR_CONFIG": str(tmp_path / CONFIG_FILE)})
    assert explicit.index.secret_scan is False  # a file the operator names is trusted


def test_files_with_a_bom_and_quoted_values_with_comments(tmp_path):
    (tmp_path / CONFIG_FILE).parent.mkdir()
    (tmp_path / CONFIG_FILE).write_text("﻿[index]\nchunk_budget = 700\n", encoding="utf-8")
    assert load_settings(tmp_path, environ={}).index.chunk_budget == 700
    assert parse_env_file('A="q" # note\nB=x # note\n') == {"A": "q", "B": "x"}
    (tmp_path / ".rag").mkdir()
    (tmp_path / ".rag" / ".env").write_text("﻿RAGNAR_DSN_READ=x\n", encoding="utf-8")
    environ: dict[str, str] = {}
    load_env_file(tmp_path, environ)
    assert environ == {"RAGNAR_DSN_READ": "x"}


@pytest.mark.parametrize("override", ["embedding.batch=0", "retrieval.default_k=3.9", "retrieval.default_k=true"])
def test_bad_numbers_are_refused(tmp_path, override):
    with pytest.raises(RagnarError):
        load_settings(tmp_path, overrides=[override], environ={})


@pytest.mark.parametrize("text, message", [
    ("[index]\nchunk_budget = 0\n", "must be positive"),
    ("[index]\nmax_file_kb = -1\n", "must be positive"),
    ("[index]\nwindow_lines = 0\nwindow_overlap = 0\n", "must be positive"),
    ("[index]\nwindow_overlap = -1\n", "0 or more"),
    ("[index]\nlegacy_encoding = 'nope'\n", "unknown encoding"),
    ("[index]\nlegacy_encoding = 'hex'\n", "unknown encoding"),  # a codec, but not a text encoding
    ("index = 5\n", "must be tables"),
    ('project = "languages"\n', "must be tables"),
    ("[index]\nchunk_budget = 'big'\n", "must be int"),
    ('[project]\nlanguages = ["Python"]\n', "unknown 'Python'"),  # names are lower case: it would index nothing
])
def test_a_bad_repository_config_is_a_clear_error(tmp_path, text, message):
    # the worker indexes many repositories: one bad commit must fail with a RagnarError, never hang or crash it
    with pytest.raises(RagnarError, match=message):
        with_repo_config(load_settings(tmp_path, environ={}), text)


def test_fingerprint_follows_chunking_settings_only(tmp_path):
    base = load_settings(tmp_path, environ={})
    assert index_fingerprint(base) == index_fingerprint(load_settings(tmp_path, ["retrieval.rrf_k=5"], {}))
    assert index_fingerprint(base) != index_fingerprint(load_settings(tmp_path, ["index.chunk_budget=900"], {}))


def test_providers_fill_in_url_and_model(tmp_path):
    s = load_settings(tmp_path, ["llm.provider=ollama"], {})
    assert (chat_settings(s).url, chat_settings(s).model) == PROVIDERS["ollama"]
    s = load_settings(tmp_path, ["llm.provider=anthropic", 'llm.model="claude-x"'], {})
    assert (chat_settings(s).url, chat_settings(s).model) == (PROVIDERS["anthropic"][0], "claude-x")


def test_summaries_fall_back_to_llm_key_by_key(tmp_path):
    s = load_settings(tmp_path, ['llm.url="http://llm/v1"', 'llm.model="big"', 'summaries.model="small"',
                                 "summaries.enabled=true"], {})
    m = summary_settings(s)
    assert (m.provider, m.url, m.model, m.api_key_env) == ("openai", "http://llm/v1", "small", "RAGNAR_LLM_API_KEY")


def test_summaries_with_their_own_provider_ignore_the_llm_endpoint(tmp_path):
    s = load_settings(tmp_path, ['llm.url="http://ollama:11434/v1"', 'llm.model="qwen3:8b"', "summaries.enabled=true",
                                 "summaries.provider=anthropic", 'summaries.api_key_env="ANTHROPIC_API_KEY"'], {})
    m = summary_settings(s)
    assert (m.provider, m.url, m.model, m.api_key_env) == ("anthropic", *PROVIDERS["anthropic"], "ANTHROPIC_API_KEY")
    assert chat_settings(s).url == "http://ollama:11434/v1"


def test_summaries_have_their_own_timeout(tmp_path):
    s = load_settings(tmp_path, ['llm.url="http://llm/v1"', 'llm.model="m"', "summaries.enabled=true"], {})
    assert (summary_settings(s).timeout, chat_settings(s).timeout) == (300.0, 60.0)
    s = load_settings(tmp_path, ['llm.url="http://llm/v1"', 'llm.model="m"', "summaries.timeout=0", "llm.timeout=20"], {})
    assert summary_settings(s).timeout == 20.0


@pytest.mark.parametrize("text, message", [
    ("[summaries]\ntimeout = -1\n", "summaries.timeout positive or 0"),
    ("[summaries]\nenabled = true\n", "summaries.enabled requires a chat model"),
    ("[summaries]\nprovider = 'gpt'\n", "summaries.provider must be one of"),
    ("[llm]\nprovider = 'gpt'\n", "llm.provider must be one of"),
])
def test_invalid_summary_settings(tmp_path, text, message):
    write(tmp_path, text, LOCAL_CONFIG_FILE)  # the committed file may not name providers
    with pytest.raises(RagnarError, match=message):
        load_settings(tmp_path, environ={})


def test_summary_exclusions_reach_the_fingerprint_and_the_repository_may_add_its_own(tmp_path):
    on = ["summaries.enabled=true", "summaries.provider=ollama"]
    base = load_settings(tmp_path, on, {})
    assert index_fingerprint(base) != index_fingerprint(load_settings(tmp_path, [*on, 'summaries.exclude=["old/"]'], {}))
    s = with_repo_config(base, '[index]\nsummary_exclude = ["vendor/"]\n[summaries]\nprovider = "anthropic"\n')
    assert s.index.summary_exclude == ("vendor/",) and s.summaries.provider == "ollama"


def test_matches_path_reads_globs_and_directories():
    assert matches_path(("legacy/",), "legacy/a/b.pas")
    assert not matches_path(("legacy/",), "legacy2/b.pas")
    assert matches_path(("*/generated/*",), "app/generated/x.py")
    assert not matches_path((), "a.py")


def test_root_files_of_the_old_layout_are_not_read(tmp_path):
    write(tmp_path, "[retrieval]\nrrf_k = 11\n", "ragnar.toml")
    write(tmp_path, "[retrieval]\nrrf_k = 12\n", "ragnar.local.toml")
    assert load_settings(tmp_path, environ={}).retrieval.rrf_k not in (11, 12)


def test_env_file_parsing():
    text = """
# comment
RAGNAR_MCP_TOKEN=abc
export RAGNAR_DSN_READ = "postgresql://r:p@h/db"
RAGNAR_LLM_API_KEY='with # hash'
RAGNAR_RERANK_API_KEY=key # trailing comment
not a variable
1BAD=x
EMPTY=
"""
    assert parse_env_file(text) == {
        "RAGNAR_MCP_TOKEN": "abc",
        "RAGNAR_DSN_READ": "postgresql://r:p@h/db",
        "RAGNAR_LLM_API_KEY": "with # hash",
        "RAGNAR_RERANK_API_KEY": "key",
        "EMPTY": "",
    }


def test_env_file_never_replaces_the_real_environment(tmp_path):
    write(tmp_path, "RAGNAR_MCP_TOKEN=from-file\nRAGNAR_DSN_READ=from-file\n", ".rag/.env")
    environ = {"RAGNAR_MCP_TOKEN": "from-shell"}
    assert load_env_file(tmp_path, environ) == ["RAGNAR_DSN_READ"]
    assert environ == {"RAGNAR_MCP_TOKEN": "from-shell", "RAGNAR_DSN_READ": "from-file"}
    assert load_env_file(tmp_path / "missing", environ) == []

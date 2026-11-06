"""Index worker: repos.toml, private repositories, and isolation between repositories."""

from __future__ import annotations

import base64
import threading

import pytest

from ragnar.config import RagnarError, load_settings
from ragnar.embedder import FakeEmbedder
from ragnar.indexer import Result
from ragnar.sync import RepoSource, git_auth_env, load_repos, sync_once, watch


def test_load_repos(tmp_path):
    path = tmp_path / "repos.toml"
    path.write_text('[[repo]]\nname = "a"\nurl = "https://x/a.git"\n\n'
                    '[[repo]]\nname = "b"\nurl = "https://x/b.git"\nbranch = "develop"\ntoken_env = "T"\n',
                    encoding="utf-8")
    assert load_repos(path) == [RepoSource("a", "https://x/a.git"), RepoSource("b", "https://x/b.git", "develop", "T")]


def test_repo_excludes_reach_the_indexer(tmp_path):
    path = tmp_path / "repos.toml"
    path.write_text('[[repo]]\nname = "a"\nurl = "u"\nexclude = ["vendor/*", "lib/*"]\n', encoding="utf-8")
    repos = load_repos(path)
    assert repos[0].exclude == ("vendor/*", "lib/*")
    seen = []

    def indexer(url, name, dsn, emb, settings, **_):
        seen.append(settings.index.exclude)
        return Result(name, "unchanged", "0" * 40, 0, 0, 0, 0.0)

    sync_once(repos, "dsn", FakeEmbedder(), load_settings(environ={}), indexer=indexer)
    assert seen == [("vendor/*", "lib/*")]


@pytest.mark.parametrize("text", ['[[repo]]\nname = "a"\n', '[[repo]]\nname = "a"\nurl = "u"\ntoken = "secret"\n'])
def test_load_repos_rejects_bad_entries(tmp_path, text):
    path = tmp_path / "repos.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(RagnarError):
        load_repos(path)


def test_template_loads_with_no_repositories(tmp_path):
    from ragnar.cli import REPOS_TEMPLATE

    path = tmp_path / "repos.toml"
    path.write_text(REPOS_TEMPLATE, encoding="utf-8")
    assert load_repos(path) == []


def test_git_auth_keeps_the_token_out_of_the_url(monkeypatch):
    assert git_auth_env(RepoSource("a", "u")) is None
    monkeypatch.setenv("GIT_TOKEN_X", "s3cret")
    env = git_auth_env(RepoSource("a", "https://x/a.git", token_env="GIT_TOKEN_X"))
    assert env["GIT_CONFIG_KEY_0"] == "http.extraHeader"
    assert base64.b64decode(env["GIT_CONFIG_VALUE_0"].split()[-1]) == b"x-access-token:s3cret"
    monkeypatch.delenv("GIT_TOKEN_X")
    with pytest.raises(RagnarError, match="GIT_TOKEN_X is empty"):
        git_auth_env(RepoSource("a", "u", token_env="GIT_TOKEN_X"))


def test_one_failure_does_not_stop_the_other_repositories(tmp_path):
    calls = []

    def fake_index(url, name, dsn, emb, settings, ref, git_env):
        calls.append((name, ref))
        if name == "bad":
            raise RagnarError("secret found")
        return Result(name, "unchanged", "a" * 40, 0, 0, 0, 0.0)

    repos = [RepoSource("bad", "u"), RepoSource("good", "u", "develop")]
    results, errors = sync_once(repos, "dsn", FakeEmbedder(8), load_settings(tmp_path, environ={}), fake_index)
    assert calls == [("bad", "main"), ("good", "develop")]
    assert [r.repo for r in results] == ["good"] and errors == ["bad: secret found"]


def test_watch_stops_on_request(tmp_path):
    stop = threading.Event()
    stop.set()
    watch([], "dsn", FakeEmbedder(8), load_settings(tmp_path, environ={}), 60, stop)

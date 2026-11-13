"""Keeps the index following a branch: the worker behind the example infrastructure.

Reads a repos.toml, mirrors each repository and indexes what changed since the last
indexed commit. Polling needs no inbound network, so it works behind a firewall.

    [[repo]]
    name = "myapp"
    url = "https://github.com/acme/myapp.git"
    branch = "main"
    token_env = "RAGNAR_GIT_TOKEN"   # optional, for private repositories
    exclude = ["vendor/*"]           # optional; index.exclude in the repository's .ragnar/config.toml replaces it
"""

from __future__ import annotations

import base64
import dataclasses
import os
import signal
import sys
import threading
import tomllib
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psycopg

from .config import RagnarError, Settings
from .embedder import Embedder, FakeEmbedder
from .indexer import Result, index


@dataclass(frozen=True)
class RepoSource:
    name: str
    url: str
    branch: str = "main"
    token_env: str = ""
    exclude: tuple[str, ...] = ()  # index globs; index.exclude in the repository's .ragnar/config.toml replaces them


def load_repos(path: str | Path) -> list[RepoSource]:
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise RagnarError(f"cannot read {path}: {exc}") from None
    except tomllib.TOMLDecodeError as exc:
        raise RagnarError(f"invalid TOML in {path}: {exc}") from None
    repos = []
    for entry in data.get("repo", []):
        unknown = sorted(set(entry) - {"name", "url", "branch", "token_env", "exclude"})
        if unknown or "name" not in entry or "url" not in entry:
            raise RagnarError(f"each [[repo]] needs name and url, and only branch, token_env and exclude besides: {entry}")
        wrong = [k for k in ("name", "url", "branch", "token_env") if k in entry and not isinstance(entry[k], str)]
        if wrong or not entry["name"]:
            raise RagnarError(f"name, url, branch and token_env of each [[repo]] must be text, and name not empty: {entry}")
        if any(r.name == entry["name"] for r in repos):
            # two entries with one name would overwrite each other's index on every pass
            raise RagnarError(f"repository {entry['name']!r} appears twice in {path}")
        exclude = entry.get("exclude", [])
        if not isinstance(exclude, list) or not all(isinstance(g, str) for g in exclude):
            # a string would become its characters, and a lone "*" among them excludes every file
            raise RagnarError(f"exclude of repo {entry['name']!r} must be a list of globs, such as [\"vendor/**\"]")
        repos.append(RepoSource(**{**entry, "exclude": tuple(exclude)}))
    return repos


def git_auth_env(source: RepoSource) -> dict[str, str] | None:
    """Git settings for a private repository.

    The token travels in an environment-only header: it never reaches the URL, the mirror's
    git config, the process list, or an error message. GitHub and GitLab accept this form.
    """
    if not source.token_env:
        return None
    token = os.environ.get(source.token_env)
    if not token:
        raise RagnarError(f"{source.name}: environment variable {source.token_env} is empty")
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}"}


def sync_once(repos: list[RepoSource], dsn: str, emb: Embedder | FakeEmbedder, settings: Settings,
              indexer=index) -> tuple[list[Result], list[str]]:
    """One pass over every repository. One failure does not stop the others."""
    results, errors = [], []
    for source in repos:
        try:
            own = settings
            if source.exclude:
                own = dataclasses.replace(settings, index=dataclasses.replace(settings.index, exclude=source.exclude))
            result = indexer(source.url, source.name, dsn, emb, own, ref=source.branch,
                             git_env=git_auth_env(source))
            results.append(result)
            print(f"{result.repo}: {result.mode} at {result.commit_sha[:12]}: {result.files} files, "
                  f"{result.chunks} chunks, {result.seconds}s", flush=True)
        except (RagnarError, psycopg.Error) as exc:
            # a database that drops mid-pass fails this repository; the next ones and the next pass still run
            message = str(exc).strip() if isinstance(exc, RagnarError) else f"database error: {str(exc).strip()}"
            errors.append(f"{source.name}: {message}")
            print(f"error: {source.name}: {message}", file=sys.stderr, flush=True)
        except Exception as exc:  # a bug, or input nobody foresaw: log it and keep the worker serving the others
            errors.append(f"{source.name}: unexpected {type(exc).__name__}: {exc}")
            print(f"error: {source.name}: unexpected error", file=sys.stderr, flush=True)
            traceback.print_exc()
    return results, errors


def watch(repos: list[RepoSource] | Callable[[], list[RepoSource]], dsn: str, emb: Embedder | FakeEmbedder,
          settings: Settings, interval: float, stop: threading.Event | None = None) -> None:
    """Polls until stopped. The index keeps serving the last good commit when a pass fails.

    `repos` may be a function that reads repos.toml: it runs before each pass, so a repository added
    to the file is picked up without a restart. A file that stops parsing keeps the last good list.
    """
    stop = stop or threading.Event()
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    current: list[RepoSource] = [] if callable(repos) else repos
    while not stop.is_set():
        if callable(repos):
            try:
                current = repos()
            except RagnarError as exc:
                print(f"error: {exc}; keeping the previous repository list", file=sys.stderr, flush=True)
        sync_once(current, dsn, emb, settings)
        stop.wait(interval)

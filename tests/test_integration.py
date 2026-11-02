"""Tests with a real Postgres + pgvector. Skipped without RAGNAR_TEST_ADMIN_DSN.

With the example Compose running:
  RAGNAR_TEST_ADMIN_DSN="postgresql://postgres:$POSTGRES_PASSWORD@127.0.0.1:15432/postgres" uv run pytest -m integration
Creates and drops the ragnar_test database; never touches the ragnar database.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from ragnar.acl import hash_token
from ragnar.config import load_settings
from ragnar.embedder import FakeEmbedder
from ragnar.indexer import index
from ragnar.migrate import migrate
from ragnar.search import Pipeline, file_outline, find_references, find_symbol, list_repos, read_code

from .test_unit import _git, make_repo

ADMIN = os.environ.get("RAGNAR_TEST_ADMIN_DSN")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not ADMIN, reason="RAGNAR_TEST_ADMIN_DSN is not set")]


@pytest.fixture(scope="module")
def dsn():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS ragnar_test WITH (FORCE)")
        c.execute("CREATE DATABASE ragnar_test")
    test_dsn = make_conninfo(ADMIN, dbname="ragnar_test")
    migrate(test_dsn)
    yield test_dsn
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS ragnar_test WITH (FORCE)")


def test_full_incremental_and_unchanged(dsn, tmp_path):
    repo, emb = make_repo(tmp_path), FakeEmbedder(768)
    settings = load_settings(tmp_path, environ={})
    r = index(str(repo), "demo", dsn, emb, settings, scan=False)
    assert (r.mode, r.files) == ("full", 2) and r.chunks >= 2

    assert index(str(repo), "demo", dsn, emb, settings, scan=False).mode == "unchanged"

    (repo / "widget" / "label.go").write_text("package widget\n\nfunc NewLabel() string { return \"x\" }\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "label")
    r = index(str(repo), "demo", dsn, emb, settings, scan=False)
    assert (r.mode, r.files, r.deleted_chunks) == ("incremental", 1, 0)

    with psycopg.connect(dsn) as conn:
        pipeline = Pipeline(load_settings(environ={}), emb)
        hits, timing = pipeline.search(conn, ["demo"], "NewButton label", k=5)
        assert hits and hits[0]["path"] == "widget/button.go" and {"embed", "sql"} <= set(timing)
        hits, _ = pipeline.search(conn, ["demo"], "NewButton", k=5, kind="doc")
        assert all(h["kind"] == "doc" for h in hits)
        names = [s["symbol"] for s in find_symbol(conn, ["demo"], "new")]
        assert names == ["widget > NewLabel", "widget > NewButton"]  # same weight: shorter name first
        assert [r["repo"] for r in list_repos(conn, None)] == ["demo"]
        assert list_repos(conn, ["other"]) == []
        assert [s["symbol"] for s in find_symbol(conn, ["demo"], "Button.Refresh")] == ["widget > Button > Refresh"]
        assert [s["symbol"] for s in find_symbol(conn, ["demo"], "efresh")] == ["widget > Button > Refresh"]
        outline = file_outline(conn, ["demo"], "button.go")
        assert outline and all(o["path"] == "widget/button.go" for o in outline)
        read = read_code(conn, ["demo"], "button.go", 1, 1)
        assert read["path"] == "widget/button.go" and read["lines"][0] == (1, "package widget")
        assert read_code(conn, ["demo"], "missing.go", 1, 1) is None


def test_find_references_leaves_definitions_out(dsn, tmp_path):
    repo, emb = tmp_path / "refs", FakeEmbedder(768)
    repo.mkdir()
    (repo / "main.go").write_text("package main\n\nfunc helper() {}\n\nfunc run() {\n\thelper()\n}\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    index(str(repo), "refs", dsn, emb, load_settings(tmp_path, environ={}), scan=False)
    with psycopg.connect(dsn) as conn:
        hits = find_references(conn, ["refs"], "helper")
    assert [(h["line"], h["text"]) for h in hits] == [(6, "helper()")]


def test_settings_change_reuses_vectors(dsn, tmp_path):
    repo, emb = make_repo(tmp_path), FakeEmbedder(768)
    first = load_settings(tmp_path, environ={})
    index(str(repo), "reuse", dsn, emb, first, scan=False)
    changed = load_settings(tmp_path, overrides=["index.window_lines=50"], environ={})
    r = index(str(repo), "reuse", dsn, emb, changed, scan=False)
    assert r.mode == "full" and r.reused_vectors == r.chunks > 0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_mcp_over_http(dsn, tmp_path, monkeypatch):
    import uvicorn

    from ragnar.ops import mcp_client

    index(str(make_repo(tmp_path)), "demo", dsn, FakeEmbedder(768), load_settings(tmp_path, environ={}), scan=False, full=True)
    acl = tmp_path / "acl.json"
    acl.write_text(json.dumps({"clients": [
        {"name": "t", "token_sha256": hash_token("tok"), "repos": ["demo"]},
        {"name": "r", "token_sha256": hash_token("restricted"), "repos": ["other"]},
    ]}))
    monkeypatch.setenv("RAGNAR_DSN_READ", dsn)
    monkeypatch.setenv("RAGNAR_ACL_FILE", str(acl))
    monkeypatch.setenv("RAGNAR__EMBEDDING__PROVIDER", "fake")
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config("ragnar.mcp_server:build_app", factory=True, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}/mcp"

    async def calls():
        async with mcp_client(url, "tok") as c:
            found = (await c.call_tool("search_code", {"query": "NewButton"})).content[0].text
            sym = (await c.call_tool("find_symbol", {"name": "Refresh"})).content[0].text
            denied = await c.call_tool("search_code", {"query": "x", "repo": "other"})
        async with mcp_client(url, "restricted") as c:
            empty = (await c.call_tool("list_repos", {})).content[0].text
        return found, sym, denied, empty

    try:
        found, sym, denied, empty = asyncio.run(calls())
    finally:
        server.should_exit = True
        thread.join(5)
    assert '<chunk repo="demo" path="widget/button.go"' in found
    assert "demo:widget/button.go:9-11  widget > Button > Refresh" in sym
    assert denied.is_error and "no permission" in denied.content[0].text
    assert empty == "No repository indexed for this token."

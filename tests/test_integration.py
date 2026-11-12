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
from ragnar.migrate import migrate, resize_embeddings
from ragnar.ops import TOOLS
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


def test_a_quoted_message_finds_the_code_that_raises_it(dsn, tmp_path):
    repo, emb = tmp_path / "msgs", FakeEmbedder(768)
    repo.mkdir()
    (repo / "credit.go").write_text('package credit\n\n// Check runs on every order.\nfunc Check(limit int) error {\n'
                                    '\treturn errors.New("customer over credit limit")\n}\n')
    (repo / "limit.go").write_text("package credit\n\n// limit of the customer credit, over the order total\n"
                                   "func Limit(customer string) int { return 0 }\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    settings = load_settings(tmp_path, overrides=['retrieval.mode="lexical"', "retrieval.file_weight=0"], environ={})
    index(str(repo), "msgs", dsn, emb, settings, scan=False)
    with psycopg.connect(dsn) as conn:
        pipeline = Pipeline(settings, emb)
        hits, _ = pipeline.search(conn, ["msgs"], 'error "customer over credit limit" when saving', k=2)
        assert hits[0]["path"] == "credit.go" and pipeline.literal_ids[:1] == [hits[0]["id"]]


ORDER_FORM = """object FrmOrder: TFrmOrder
  Caption = 'Pedidos'
  object btnSave: TButton
    Caption = 'Gravar'
    OnClick = btnSaveClick
  end
end
"""
ORDER_UNIT = """unit Order;

interface

type
  TFrmOrder = class(TForm)
    procedure btnSaveClick(Sender: TObject);
  end;

implementation

uses OrderRepo;

procedure TFrmOrder.btnSaveClick(Sender: TObject);
begin
  SaveOrderRecord(1);
end;

end.
"""
REPO_UNIT = """unit OrderRepo;

interface

procedure SaveOrderRecord(Id: Integer);

implementation

procedure SaveOrderRecord(Id: Integer);
begin
  Query.SQL.Add('insert into PEDIDOS (ID)');
  Query.SQL.Add('values (:ID)');
  Query.ExecSQL;
end;

end.
"""


def test_code_graph_links_a_button_to_the_table_it_writes(dsn, tmp_path):
    from ragnar.search import callees, edge_references, repo_map, table_usage, trace_flow

    repo, emb = tmp_path / "shop", FakeEmbedder(768)
    repo.mkdir()
    for name, text in (("Order.dfm", ORDER_FORM), ("Order.pas", ORDER_UNIT), ("OrderRepo.pas", REPO_UNIT)):
        (repo / name).write_text(text)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    settings = load_settings(tmp_path, overrides=["retrieval.graph_weight=1"], environ={})
    index(str(repo), "shop", dsn, emb, settings, scan=False)
    with psycopg.connect(dsn) as conn:
        refs = edge_references(conn, ["shop"], "SaveOrderRecord")
        assert [(r["path"], r["line"], r["edge"]) for r in refs] == [("Order.pas", 16, "ref")]
        assert refs[0]["symbol"].endswith("btnSaveClick")
        events = edge_references(conn, ["shop"], "TFrmOrder.btnSaveClick")
        assert [(r["path"], r["edge"]) for r in events] == [("Order.dfm", "event")]
        assert "saveorderrecord" in {c["name"] for c in callees(conn, ["shop"], "TFrmOrder.btnSaveClick")}
        writes = table_usage(conn, ["shop"], "pedidos", "write")
        assert [(w["path"], w["line"], w["table"]) for w in writes] == [("OrderRepo.pas", 11, "pedidos")]
        assert {m["path"] for m in repo_map(conn, ["shop"])} == {"Order.pas", "OrderRepo.pas"}  # each referenced once
        steps = trace_flow(conn, ["shop"], "btnSaveClick", "PEDIDOS")
        assert [s["to"] for s in steps] == ["saveorderrecord", "pedidos"]
        one_seed = load_settings(tmp_path, overrides=["retrieval.graph_weight=1", "retrieval.graph_seeds=1"], environ={})
        pipeline = Pipeline(one_seed, emb)
        hits, _ = pipeline.search(conn, ["shop"], "btnSaveClick", k=5)
        assert pipeline.graph_ids and hits[0]["id"] not in pipeline.graph_ids


def test_file_summaries_are_cached_and_stay_out_of_line_tools(dsn, tmp_path, monkeypatch):
    class FakeChat:
        calls = 0

        def complete(self, prompt: str) -> str:
            FakeChat.calls += 1
            return "Draws the order screen button and refreshes its label."

    monkeypatch.setattr("ragnar.indexer.summary_chat_from", lambda settings: FakeChat())
    repo, emb = make_repo(tmp_path), FakeEmbedder(768)
    settings = load_settings(tmp_path, overrides=["summaries.enabled=true", "summaries.min_lines=3",
                                                  'llm.url="http://llm"', 'llm.model="m"'], environ={})
    index(str(repo), "sums", dsn, emb, settings, scan=False)
    first = FakeChat.calls
    index(str(repo), "sums", dsn, emb, settings, scan=False, full=True)
    assert first >= 1 and FakeChat.calls == first  # the second run reads the cache
    with psycopg.connect(dsn) as conn:
        kinds = dict(conn.execute("SELECT path, kind FROM code_chunk WHERE repo = 'sums' AND kind = 'summary'").fetchall())
        assert "widget/button.go" in kinds
        read = read_code(conn, ["sums"], "widget/button.go", 1, 1)
        assert read["lines"][0] == (1, "package widget")


def test_excluded_directories_get_no_summary(dsn, tmp_path, monkeypatch):
    class FakeChat:
        def complete(self, prompt: str) -> str:
            return "Draws the order screen button."

    monkeypatch.setattr("ragnar.indexer.summary_chat_from", lambda settings: FakeChat())
    repo, emb = make_repo(tmp_path), FakeEmbedder(768)
    settings = load_settings(tmp_path, overrides=["summaries.enabled=true", "summaries.min_lines=3",
                                                  'summaries.exclude=["widget/"]',
                                                  'llm.url="http://llm"', 'llm.model="m"'], environ={})
    index(str(repo), "sumsx", dsn, emb, settings, scan=False)
    with psycopg.connect(dsn) as conn:
        paths = [p for (p,) in conn.execute("SELECT path FROM code_chunk WHERE repo = 'sumsx' AND kind = 'summary'")]
    assert not any(p.startswith("widget/") for p in paths)


def test_settings_change_reuses_vectors(dsn, tmp_path):
    repo, emb = make_repo(tmp_path), FakeEmbedder(768)
    first = load_settings(tmp_path, environ={})
    index(str(repo), "reuse", dsn, emb, first, scan=False)
    changed = load_settings(tmp_path, overrides=["index.window_lines=50"], environ={})
    r = index(str(repo), "reuse", dsn, emb, changed, scan=False)
    assert r.mode == "full" and r.reused_vectors == r.chunks > 0


def test_interrupted_build_resumes_from_its_checkpoint(dsn, tmp_path):
    repo = make_repo(tmp_path)
    settings = load_settings(tmp_path, overrides=["embedding.batch=1"], environ={})

    class Failing(FakeEmbedder):
        calls = 0

        def embed_documents(self, texts):
            Failing.calls += 1
            if Failing.calls > 1:
                raise RuntimeError("embedder died")
            return super().embed_documents(texts)

    with pytest.raises(RuntimeError):
        index(str(repo), "resume", dsn, Failing(768), settings, scan=False)
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM embedding_checkpoint").fetchone()[0] == 1
    r = index(str(repo), "resume", dsn, FakeEmbedder(768), settings, scan=False)
    assert r.mode == "full" and r.reused_vectors == 1
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM embedding_checkpoint").fetchone()[0] == 0


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
            refs = (await c.call_tool("find_references", {"name": "Button"})).content[0].text
            outline = (await c.call_tool("file_outline", {"path": "button.go"})).content[0].text
            code = (await c.call_tool("read_code", {"path": "button.go", "start_line": 1, "end_line": 1})).content[0].text
            tools = {t.name for t in (await c.list_tools()).tools}
            denied = await c.call_tool("search_code", {"query": "x", "repo": "other"})
        async with mcp_client(url, "restricted") as c:
            empty = (await c.call_tool("list_repos", {})).content[0].text
        return found, sym, denied, empty, refs, outline, code, tools

    try:
        found, sym, denied, empty, refs, outline, code, tools = asyncio.run(calls())
    finally:
        server.should_exit = True
        thread.join(5)
    assert '<chunk repo="demo" path="widget/button.go"' in found
    assert "demo:widget/button.go:9-11  widget > Button > Refresh" in sym
    assert denied.is_error and "no permission" in denied.content[0].text
    assert empty == "No repository indexed for this token."
    assert "demo:widget/button.go:" in refs and "[code]" in refs
    assert outline.startswith("demo:widget/button.go\n") and "widget > Button > Refresh" in outline
    assert code == '<code repo="demo" path="widget/button.go">\n1: package widget\n</code>'
    assert tools == TOOLS


def test_resize_embeddings_clears_every_index_table(dsn, tmp_path, monkeypatch):
    # runs last: it empties the module's database
    index(str(make_repo(tmp_path)), "demo", dsn, FakeEmbedder(768), load_settings(tmp_path, environ={}), scan=False, full=True)
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO code_edge (repo, path, line, src_symbol, dst_name, kind, text, commit_sha) "
                     "VALUES ('demo', 'a.go', 1, '', 'thing', 'ref', 'thing()', 'x')")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RAGNAR__EMBEDDING__DIM", "768")
    assert resize_embeddings(dsn) == 768
    with psycopg.connect(dsn) as conn:
        for table in ("code_chunk", "code_symbol", "code_edge", "index_state", "embedding_checkpoint"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table

import json
import time

import pytest

from ragnar.config import Events, RagnarError, load_settings
from ragnar.events import EVENTS, NatsSink, NullSink, messages, node, sink_from, subject


def test_subject_keeps_one_token_per_repository():
    assert subject("ragnar.mcp", "billing-api", "search_code") == "ragnar.mcp.billing-api.search_code"
    # a dot in the name would split it into two tokens; wildcards would match other subjects
    assert subject("ragnar.mcp", "acme.web", "trace_flow") == "ragnar.mcp.acme_web.trace_flow"
    assert subject("ragnar.mcp", "a*b>c", "x") == "ragnar.mcp.a_b_c.x"


def test_node_leaves_out_what_the_tool_did_not_return():
    assert node("r", "a.py", "map") == {"repo": "r", "path": "a.py", "step": "map"}
    assert node("r", "a.py", "table", 4, 4, "", "orders") == {
        "repo": "r", "path": "a.py", "step": "table", "start_line": 4, "end_line": 4, "target": "orders"}


def test_messages_go_one_per_repository_without_the_query():
    nodes = [node("app", "a.py", "caller", 3, 3, "checkout"), node("lib", "b.py", "callee", 1, 1, "total"),
             node("app", "c.py", "caller", 9, 9)]
    out = messages("ragnar.mcp", "call_graph", "team-a", 12, nodes, max_nodes=1, ts="2026-11-18T10:00:00+0000")
    assert [s for s, _ in out] == ["ragnar.mcp.app.call_graph", "ragnar.mcp.lib.call_graph"]
    app = json.loads(out[0][1])
    assert app == {"schema": 1, "ts": "2026-11-18T10:00:00+0000", "tool": "call_graph", "client": "team-a", "ms": 12,
                   "repo": "app", "truncated": True,
                   "nodes": [{"path": "a.py", "step": "caller", "start_line": 3, "end_line": 3, "symbol": "checkout"}]}
    assert json.loads(out[1][1])["truncated"] is False
    assert messages("p", "t", "c", 1, [], 10) == []


def test_events_are_off_by_default_and_validated(tmp_path):
    settings = load_settings(tmp_path, environ={})
    assert settings.events.provider == "none" and isinstance(sink_from(settings.events), NullSink)
    on = load_settings(tmp_path, environ={"RAGNAR__EVENTS__PROVIDER": "nats", "RAGNAR__EVENTS__URL": "nats://n:4222"})
    assert (on.events.provider, on.events.url) == ("nats", "nats://n:4222")
    for key, value in (("PROVIDER", "kafka"), ("QUEUE", "0"), ("SUBJECT_PREFIX", "ragnar.*"), ("SUBJECT_PREFIX", "a..b"),
                       ("SUBJECT_PREFIX", ".ragnar"), ("SUBJECT_PREFIX", "rag\tnar")):
        with pytest.raises(RagnarError):
            load_settings(tmp_path, environ={f"RAGNAR__EVENTS__{key}": value})


def test_a_committed_project_file_cannot_set_events(tmp_path, capsys):
    (tmp_path / ".ragnar").mkdir()
    (tmp_path / ".ragnar" / "config.toml").write_text(
        '[events]\nprovider = "nats"\nurl = "nats://attacker:4222"\ntoken_env = "RAGNAR_DSN_WRITE"\n', encoding="utf-8")
    settings = load_settings(tmp_path, environ={})
    assert settings.events == Events()
    assert "events.token_env" in capsys.readouterr().err


def test_an_unreachable_broker_drops_instead_of_blocking():
    before = EVENTS.labels("dropped")._value.get()
    sink = NatsSink(Events(provider="nats", url="nats://127.0.0.1:1", queue=2), environ={})
    t0 = time.perf_counter()
    for _ in range(5):
        sink.publish("search_code", "c", 1, [node("r", "a.py", "hit", 1, 2)])
    assert time.perf_counter() - t0 < 0.5
    assert EVENTS.labels("dropped")._value.get() - before >= 3


def test_close_does_not_wait_for_a_broker_it_never_reached():
    sink = NatsSink(Events(provider="nats", url="nats://127.0.0.1:1"), environ={})
    sink.publish("search_code", "c", 1, [node("r", "a.py", "hit", 1, 2)])
    t0 = time.perf_counter()
    sink.close()
    assert time.perf_counter() - t0 < 1


def test_the_server_process_exits_and_sends_what_it_queued(tmp_path):
    """With a live broker: a process that published and returns must exit by itself, with the message sent."""
    import asyncio
    import os
    import subprocess
    import sys

    url = os.environ.get("RAGNAR_TEST_NATS_URL")
    if not url:
        pytest.skip("RAGNAR_TEST_NATS_URL is not set")
    import nats

    prefix = f"exittest{os.getpid()}"
    script = tmp_path / "publish.py"
    script.write_text(
        "import time\n"
        "from ragnar.config import Events\n"
        "from ragnar.events import node, sink_from\n"
        f"sink = sink_from(Events(provider='nats', url={url!r}, subject_prefix={prefix!r}))\n"
        "while not sink.connected.is_set():\n"
        "    time.sleep(0.05)\n"
        "sink.publish('find_symbol', 'c', 1, [node('demo', 'a.py', 'symbol', 1, 2)])\n", encoding="utf-8")

    async def run() -> list[str]:
        nc = await nats.connect(url)
        got: list[str] = []

        async def keep(m):
            got.append(m.subject)

        await nc.subscribe(f"{prefix}.>", cb=keep)
        await nc.flush()
        proc = await asyncio.to_thread(subprocess.run, [sys.executable, str(script)], timeout=20)
        assert proc.returncode == 0
        for _ in range(40):
            if got:
                break
            await asyncio.sleep(0.05)
        await nc.close()
        return got

    assert asyncio.run(run()) == [f"{prefix}.demo.find_symbol"]

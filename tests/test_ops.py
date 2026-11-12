"""Load generator: it survives failed connections and opens new sessions while it runs."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from ragnar import ops


def test_load_reconnects_and_counts_a_failed_connection(monkeypatch, capsys):
    sessions = []

    class FakeClient:
        async def call_tool(self, name, args):
            await asyncio.sleep(0.01)
            return SimpleNamespace(is_error=False)

    @asynccontextmanager
    async def fake_client(url, token):
        sessions.append(url)
        if len(sessions) == 1:
            raise ConnectionError("pod is being replaced")
        yield FakeClient()

    monkeypatch.setattr(ops, "mcp_client", fake_client)
    monkeypatch.setattr(ops, "SESSION_SECONDS", 0.2)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(ops.asyncio, "sleep", lambda s: real_sleep(min(s, 0.05)))
    asyncio.run(ops._load("http://mcp", "tok", 1, 1.0, ["q"], 0))
    total = capsys.readouterr().out.strip().splitlines()[-1]
    assert total.startswith("total ") and "errors 1," in total
    assert len(sessions) >= 3  # the failed one, then a new session every SESSION_SECONDS

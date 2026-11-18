"""Graph access events: where in the index each MCP tool call read, published to NATS.

One message per repository a call touched, on the subject <subject_prefix>.<repo>.<tool>, so a consumer
subscribes by topic: ragnar.mcp.billing.> for one repository, ragnar.mcp.*.trace_flow for one tool.
The message lists the nodes the call returned (file, symbol, lines, and the step that reached them),
never the query text.

Publishing only observes. A call puts its messages on a bounded queue and returns; a thread with its own
event loop sends them. When NATS is down or slow, the queue fills and new messages are dropped and
counted (ragnar_events_total{result="dropped"}): a tool call never waits for the broker.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import sys
import threading
import time
from collections.abc import Mapping

from prometheus_client import Counter

from .config import Events

EVENTS = Counter("ragnar_events_total", "Graph access events", ["result"])
SCHEMA = 1
# NATS subjects are tokens split by "."; a repository name may hold a dot
NOT_IN_TOKEN = str.maketrans({".": "_", "*": "_", ">": "_", " ": "_"})


def subject(prefix: str, repo: str, tool: str) -> str:
    return f"{prefix}.{repo.translate(NOT_IN_TOKEN)}.{tool}"


def node(repo: str, path: str, step: str, start_line: int | None = None, end_line: int | None = None,
         symbol: str | None = None, target: str | None = None) -> dict:
    """A place a tool call returned. step: how the call reached it (hit, symbol, reference, caller,
    callee, trace, table, map, outline, read)."""
    out = {"repo": repo, "path": path, "step": step, "start_line": start_line, "end_line": end_line,
           "symbol": symbol or None, "target": target}
    return {k: v for k, v in out.items() if v is not None}


def messages(prefix: str, tool: str, client: str, ms: int, nodes: list[dict], max_nodes: int,
             ts: str | None = None) -> list[tuple[str, bytes]]:
    """(subject, payload) per repository touched, in the order the call returned them."""
    by_repo: dict[str, list[dict]] = {}
    for n in nodes:
        by_repo.setdefault(n["repo"], []).append({k: v for k, v in n.items() if k != "repo"})
    ts = ts or time.strftime("%Y-%m-%dT%H:%M:%S%z")
    out = []
    for repo, found in by_repo.items():
        payload = {"schema": SCHEMA, "ts": ts, "tool": tool, "client": client, "ms": ms, "repo": repo,
                   "nodes": found[:max_nodes], "truncated": len(found) > max_nodes}
        out.append((subject(prefix, repo, tool), json.dumps(payload, ensure_ascii=False).encode()))
    return out


class NullSink:
    def publish(self, tool: str, client: str, ms: int, nodes: list[dict]) -> None:
        pass

    def close(self) -> None:
        pass


class NatsSink:
    """Sends from a daemon thread. Reconnects on its own; drops what does not fit the queue."""

    def __init__(self, cfg: Events, environ: Mapping[str, str] | None = None):
        environ = os.environ if environ is None else environ
        self.cfg = cfg
        self.auth = {k: environ[name] for k, name in (("user", cfg.user_env), ("password", cfg.password_env),
                                                      ("token", cfg.token_env)) if name and environ.get(name)}
        self.queue: queue.Queue = queue.Queue(maxsize=cfg.queue)
        self.thread = threading.Thread(target=lambda: asyncio.run(self._main()), name="ragnar-events", daemon=True)
        self.thread.start()

    def publish(self, tool: str, client: str, ms: int, nodes: list[dict]) -> None:
        for item in messages(self.cfg.subject_prefix, tool, client, ms, nodes, self.cfg.max_nodes):
            try:
                self.queue.put_nowait(item)
            except queue.Full:
                EVENTS.labels("dropped").inc()

    def close(self) -> None:
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass
        self.thread.join(timeout=5)

    async def _main(self) -> None:
        import nats  # the events extra; imported here so the server starts without it when events are off

        loop = asyncio.get_running_loop()
        nc = None
        warned = False
        while True:
            if nc is None:
                try:
                    nc = await nats.connect(servers=[self.cfg.url], connect_timeout=5, max_reconnect_attempts=-1,
                                            reconnect_time_wait=2, name="ragnar-mcp", **self.auth)
                    warned = False
                except Exception as exc:  # noqa: BLE001 - any failure means: try again later
                    if not warned:
                        print(f"events: cannot reach {self.cfg.url}: {exc}; retrying", file=sys.stderr, flush=True)
                        warned = True
                    await asyncio.sleep(5)
                    continue
            item = await loop.run_in_executor(None, self.queue.get)
            if item is None:
                await nc.drain()
                return
            try:
                await nc.publish(*item)
                EVENTS.labels("published").inc()
            except Exception:  # noqa: BLE001 - a full outbound buffer while reconnecting, a closed connection
                EVENTS.labels("dropped").inc()
                if nc.is_closed:
                    nc = None


def sink_from(cfg: Events) -> NullSink | NatsSink:
    return NatsSink(cfg) if cfg.provider == "nats" else NullSink()

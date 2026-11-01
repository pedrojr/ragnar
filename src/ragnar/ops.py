"""End-to-end verification (smoke) and load generator, both real MCP clients."""

from __future__ import annotations

import asyncio
import random
import statistics
import time
from contextlib import asynccontextmanager

import httpx
import psycopg
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from psycopg.conninfo import conninfo_to_dict, make_conninfo

TOOLS = {"search_code", "find_symbol", "list_repos"}
DEFAULT_QUERIES = [
    "how is the main entry point wired", "where is input validated", "error handling for failed requests",
    "database connection setup", "configuration loading", "authentication and permissions",
    "retry logic", "logging setup", "tests for the main flow", "public API routes",
    "func New", "Config", "Service", "handle error", "parse",
]


@asynccontextmanager
async def mcp_client(url: str, token: str):
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    try:
        async with Client(streamable_http_client(url, http_client=http)) as client:
            yield client
    finally:
        await http.aclose()


def _text(result) -> str:
    return "\n".join(getattr(c, "text", "") for c in result.content)


async def _smoke(url: str, token: str, restricted: str | None, repo: str,
                 write_dsn: str | None, read_dsn: str | None, replica_host: str | None) -> bool:
    ok = True

    def check(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"[{'ok' if passed else 'FAILED'}] {name}" + (f": {detail}" if detail else ""))

    async with httpx.AsyncClient(timeout=10) as http:
        r = await http.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        check("no token gets 401", r.status_code == 401, f"status {r.status_code}")

    async with mcp_client(url, token) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        check("tools exposed", names == TOOLS, ", ".join(sorted(names)))
        repos = _text(await client.call_tool("list_repos", {}))
        listed = repos.startswith(f"{repo}:") or f"\n{repo}:" in repos
        check(f"list_repos shows {repo}", listed, repos.splitlines()[0] if repos else "")
        t0 = time.perf_counter()
        res = await client.call_tool("search_code", {"query": DEFAULT_QUERIES[0], "repo": repo, "k": 5})
        body = _text(res)
        first = next((line for line in body.splitlines() if line.startswith("<chunk")), "")
        check("search_code returns snippets with path:line", not res.is_error and 'path="' in first,
              f"{(time.perf_counter() - t0) * 1000:.0f} ms, {first[:110]}")
        res = await client.call_tool("find_symbol", {"name": "New", "repo": repo, "k": 3})
        check("find_symbol answers", not res.is_error, _text(res).splitlines()[0][:110] if _text(res) else "")

    if restricted:
        async with mcp_client(url, restricted) as client:
            res = await client.call_tool("search_code", {"query": "x", "repo": repo})
            check(f"restricted token cannot read {repo}", res.is_error, _text(res)[:110])

    if write_dsn and read_dsn:
        primary_host = conninfo_to_dict(write_dsn)["host"]
        marker = time.time()
        with psycopg.connect(write_dsn, autocommit=True) as w:
            w.execute("""INSERT INTO replication_probe (id, written_at) VALUES (1, to_timestamp(%s))
                         ON CONFLICT (id) DO UPDATE SET written_at = EXCLUDED.written_at""", (marker,))
        seen, t0 = False, time.perf_counter()
        with psycopg.connect(read_dsn, autocommit=True) as r:
            while time.perf_counter() - t0 < 10 and not seen:
                row = r.execute("SELECT extract(epoch FROM written_at) FROM replication_probe WHERE id = 1").fetchone()
                seen = row is not None and abs(float(row[0]) - marker) < 0.001
                if not seen:
                    time.sleep(0.05)
        check("write on the primary reaches the replica", seen, f"{(time.perf_counter() - t0) * 1000:.0f} ms")

        def must_fail(name: str, dsn: str) -> None:
            try:
                with psycopg.connect(dsn) as c:
                    c.execute("INSERT INTO replication_probe (id, written_at) VALUES (2, now())")
                check(name, False, "INSERT accepted")
            except (psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction) as exc:
                check(name, True, str(exc).splitlines()[0])

        must_fail("ragnar_reader cannot write, even on the primary", make_conninfo(read_dsn, host=primary_host))
        if replica_host:
            must_fail("replica refuses writes, even from ragnar_writer", make_conninfo(write_dsn, host=replica_host))
    return ok


def smoke(url: str, token: str, restricted: str | None, repo: str,
          write_dsn: str | None, read_dsn: str | None, replica_host: str | None) -> bool:
    return asyncio.run(_smoke(url, token, restricted, repo, write_dsn, read_dsn, replica_host))


async def _load(url: str, token: str, concurrency: int, duration: float, queries: list[str], rate: float) -> None:
    latencies: list[float] = []
    errors = 0
    stop = time.monotonic() + duration
    interval = concurrency / rate if rate > 0 else 0.0  # per-client pause to reach the total rate

    async def worker(n: int) -> None:
        nonlocal errors
        rnd = random.Random(n)
        async with mcp_client(url, token) as client:
            while time.monotonic() < stop:
                t0 = time.perf_counter()
                try:
                    res = await client.call_tool("search_code", {"query": rnd.choice(queries), "k": 10})
                    if res.is_error:
                        errors += 1
                except Exception:  # noqa: BLE001
                    errors += 1
                elapsed = time.perf_counter() - t0
                latencies.append(elapsed)
                if interval > elapsed:
                    await asyncio.sleep(interval - elapsed)

    async def report() -> None:
        last, t_last = 0, time.monotonic()
        while time.monotonic() < stop:
            await asyncio.sleep(10)
            now, t_now = len(latencies), time.monotonic()
            window = sorted(latencies[last:now]) or [0.0]
            p95 = window[int(0.95 * (len(window) - 1))]
            print(f"{time.strftime('%H:%M:%S')}  {(now - last) / (t_now - t_last):6.1f} searches/s  "
                  f"p50 {statistics.median(window) * 1000:6.0f} ms  p95 {p95 * 1000:6.0f} ms  errors {errors}", flush=True)
            last, t_last = now, t_now

    await asyncio.gather(report(), *(worker(i) for i in range(concurrency)))
    total = sorted(latencies) or [0.0]
    print(f"total {len(latencies)} searches in {duration:.0f}s, errors {errors}, "
          f"p95 {total[int(0.95 * (len(total) - 1))] * 1000:.0f} ms")


def load(url: str, token: str, concurrency: int, duration: float, queries: list[str] | None, rate: float) -> None:
    asyncio.run(_load(url, token, concurrency, duration, queries or DEFAULT_QUERIES, rate))

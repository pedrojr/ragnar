"""Reads the MCP server's audit lines (one JSON object per tool call, on stdout) into a usage report.

The report answers what a team asks before trusting an index: how many tokens it returns, how often a
search finds nothing or has low confidence, which questions repeat. It also yields implicit relevance
labels: a search followed, by the same client, by read_code on a file it returned says that file helped.
Those labels become questions for `ragnar eval run`, from real use instead of commit messages.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from statistics import mean, median

FOLLOW_UP_SECONDS = 600  # a read_code this soon after a search, by the same client, is read as its answer


def parse(lines: Iterable[str]) -> list[dict]:
    """Tool events; other log lines are skipped."""
    events = []
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") == "tool":
            events.append(event)
    return events


def _time(event: dict) -> datetime | None:
    try:
        return datetime.strptime(event["ts"], "%Y-%m-%dT%H:%M:%S%z")
    except (KeyError, ValueError):
        return None


def labels(events: list[dict]) -> list[dict]:
    """Questions with the file the agent went on to read: {"id", "query", "relevant", "client"}."""
    out: list[dict] = []
    open_searches: dict[str, tuple[datetime, dict]] = {}
    for event in events:
        when, client = _time(event), event.get("client", "")
        if when is None:
            continue
        if event["tool"] == "search_code" and event.get("paths"):
            open_searches[client] = (when, event)
        elif event["tool"] == "read_code" and client in open_searches:
            started, search = open_searches[client]
            path = event.get("path") or event.get("query", "")
            hit = next((p for p in search["paths"] if p == path or p.endswith(f"/{path}")), None)
            if hit and (when - started).total_seconds() <= FOLLOW_UP_SECONDS:
                out.append({"id": f"use-{len(out) + 1}", "query": search.get("query", ""), "relevant": [hit],
                            "client": client})
                del open_searches[client]
    seen: set[tuple[str, str]] = set()
    unique = []
    for label in out:
        key = (label["query"].lower(), label["relevant"][0])
        if key not in seen:
            seen.add(key)
            unique.append(label)
    return unique


def report(events: list[dict]) -> dict:
    """Usage per tool, tokens, empty and low-confidence searches, and repeated questions."""
    by_tool = Counter(e["tool"] for e in events)
    tokens = [e.get("tokens", 0) for e in events]
    searches = [e for e in events if e["tool"] == "search_code"]
    queries = Counter(e.get("query", "").strip().lower() for e in searches if e.get("query"))
    return {
        "calls": len(events),
        "by_tool": dict(by_tool.most_common()),
        "clients": len({e.get("client") for e in events}),
        "tokens": {"total": sum(tokens), "mean": round(mean(tokens)) if tokens else 0,
                   "median": round(median(tokens)) if tokens else 0},
        "searches": len(searches),
        "empty_searches": sum(1 for e in searches if not e.get("chunk_ids")),
        "low_confidence": sum(1 for e in searches if e.get("confidence") == "low"),
        "repeated_questions": [{"query": q, "times": n} for q, n in queries.most_common(10) if n > 1],
        "latency_ms_median": round(median(e.get("ms", 0) for e in events)) if events else 0,
        "labels": len(labels(events)),
    }

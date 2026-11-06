"""Usage report and implicit labels from the MCP audit lines."""

from __future__ import annotations

import json

from ragnar.stats import labels, parse, report


def line(ts: str, tool: str, **info) -> str:
    return json.dumps({"ts": f"2026-11-05T10:{ts}-0300", "event": "tool", "tool": tool, "client": "dev",
                       "ms": 10, "tokens": 100, **info})


LOG = [
    "INFO server started",
    line("00:00", "search_code", query="onde grava o pedido", chunk_ids=[1, 2], paths=["src/Order.pas", "src/Util.pas"],
         confidence="high"),
    line("01:00", "read_code", query="Order.pas", path="src/Order.pas"),
    line("02:00", "search_code", query="onde grava o pedido", chunk_ids=[1], paths=["src/Order.pas"], confidence="low"),
    line("03:00", "search_code", query="nada aqui", chunk_ids=[], paths=[]),
    line("30:00", "read_code", query="Util.pas", path="src/Util.pas"),  # too late: not an answer
]


def test_report_counts_tools_tokens_and_repeats():
    r = report(parse(LOG))
    assert r["calls"] == 5 and r["by_tool"] == {"search_code": 3, "read_code": 2}
    assert (r["empty_searches"], r["low_confidence"], r["tokens"]["total"]) == (1, 1, 500)
    assert r["repeated_questions"] == [{"query": "onde grava o pedido", "times": 2}]


def test_a_read_soon_after_a_search_labels_the_file():
    assert labels(parse(LOG)) == [{"id": "use-1", "query": "onde grava o pedido", "relevant": ["src/Order.pas"],
                                   "client": "dev"}]

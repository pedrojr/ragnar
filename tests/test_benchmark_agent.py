"""Scoring of real agent answers in benchmark/agent.py: abstention and invented citations."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import agent  # noqa: E402

FILES = {"gin.go": 788, "tree.go": 900, "internal/json/json.go": 20}
ENTRY = {"name": "gin", "snapshot": "abc"}


@pytest.fixture(autouse=True)
def snapshot(monkeypatch):
    monkeypatch.setattr(agent, "snapshot_files", lambda repo, snap: {f.lower(): f for f in FILES})
    monkeypatch.setattr(agent, "file_lines", lambda repo, snap, path: [""] * FILES[path])


def test_abstained_reads_the_marker_at_the_start_only():
    assert agent.abstained("NOT FOUND. Gin has no WebSocket code.")
    assert agent.abstained("**Not found**: nothing upgrades the connection.")
    assert agent.abstained("NOT FOUND")
    assert not agent.abstained("The handler is in gin.go:661. It was NOT FOUND in tree.go.")
    assert not agent.abstained("NOT FOUND is not the answer here: jsoup picks the client at runtime.")


def test_correct_on_a_negative_question_requires_abstaining():
    q = {"relevant": []}
    assert agent.correct("NOT FOUND. Closest is gin.go:661.", q)
    assert not agent.correct("Upgrades happen in gin.go:661.", q)


def test_correct_rejects_an_abstention_that_names_the_file():
    q = {"relevant": ["tree.go"]}
    assert agent.correct("Routes are looked up in tree.go:428.", q)
    assert not agent.correct("NOT FOUND, though tree.go looks close.", q)


def test_citations_parse_lines_ranges_and_link_anchors():
    found = agent.citations("See `gin.go:637-643`, [tree](D:\\x\\tree.go#L10-L20) and internal/json/json.go.", {"go"})
    assert found == [("gin.go", 637, 643), ("D:/x/tree.go", 10, 20), ("internal/json/json.go", None, None)]


def test_citations_skip_symbols_mentions_and_the_cut_of_a_truncated_answer():
    assert agent.citations("Engine.handleHTTPRequest, e.g. AGENTS.md", {"go", "md"}) == []
    assert agent.citations("first gin.go:12 then tree.go:4", {"go"}, truncated=True) == [("gin.go", 12, 12)]


def test_invented_citations_flag_missing_files_and_lines_past_the_end():
    cited, invented = agent.invented_citations("gin.go:661, gin.go:1975, router.go:3, tree.go:890-905", ENTRY)
    assert cited == 4
    assert invented == ["gin.go:1975", "router.go:3", "tree.go:890"]


def test_paths_through_the_clone_resolve_but_wrong_folders_do_not():
    answer = "D:/dev/ragnar/benchmark/repos/gin/tree.go:4, benchmark/repos/gin/gin.go:5, json.go:3, other/json.go:3"
    assert agent.invented_citations(answer, ENTRY) == (4, ["other/json.go:3"])

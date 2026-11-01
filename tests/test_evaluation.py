"""Evaluation metrics and report."""

from __future__ import annotations

from ragnar.evaluation import build_golden, format_report, grep_baseline, mrr, paired_ci, recall_at

from .test_unit import _git, make_repo


def test_metrics():
    assert recall_at(["a", "b"], ["b", "c"], 10) == 0.5
    assert recall_at(["a", "b"], ["b"], 1) == 0.0
    assert mrr(["a", "b"], ["b"]) == 0.5 and mrr(["a"], ["z"]) == 0.0
    gain, low, high = paired_ci([0.0, 0.0, 0.0, 0.0], [1.0, 1.0, 1.0, 1.0], n=100)
    assert (gain, low, high) == (1.0, 1.0, 1.0)


def test_report_warns_on_small_samples():
    scores = {"recall_at_10": 0.5, "mrr": 0.4, "avg_context_tokens": 900}
    gain = {"recall_gain": 0.1, "ci95_low": 0.0, "ci95_high": 0.2}
    text = format_report({"questions": 3, "indicative_only": True, "grep": scores,
                          "systems": {"configured": {**scores, "vs_grep": gain}}})
    assert "indicative only" in text and "configured vs_grep: recall gain +0.100" in text


def test_golden_comes_from_commits_after_the_snapshot(tmp_path):
    repo = make_repo(tmp_path)
    snapshot = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "widget" / "button.go").write_text("package widget\n\nfunc NewButton() {}\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "make the button constructor take no label")
    (repo / "new.go").write_text("package widget\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "add a file that the snapshot does not have")
    (repo / "widget" / "button.go").write_text("package widget\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "chore: skipped because of its type, even if long")
    golden = build_golden(str(repo), snapshot)
    assert [q["relevant"] for q in golden] == [["widget/button.go"]]
    assert golden[0]["query"] == "make the button constructor take no label"


def test_grep_baseline_ranks_files_with_more_terms(tmp_path):
    repo = make_repo(tmp_path)
    snapshot = _git(repo, "rev-parse", "HEAD").strip()
    assert grep_baseline(str(repo), snapshot, "redraws the button") == ["widget/button.go"]

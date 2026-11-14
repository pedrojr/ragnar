"""Evaluation metrics and report."""

from __future__ import annotations

from ragnar.evaluation import STOPWORDS, build_golden, format_report, grep_baseline, grep_terms, mrr, paired_ci, recall_at

from .test_unit import _git, make_repo


def test_metrics():
    assert recall_at(["a", "b"], ["b", "c"], 10) == 0.5
    assert recall_at(["a", "b"], ["b"], 1) == 0.0
    assert mrr(["a", "b"], ["b"]) == 0.5 and mrr(["a"], ["z"]) == 0.0
    gain, low, high = paired_ci([0.0, 0.0, 0.0, 0.0], [1.0, 1.0, 1.0, 1.0], n=100)
    assert (gain, low, high) == (1.0, 1.0, 1.0)


def test_hand_written_golden_lines_are_checked(tmp_path):
    import pytest

    from ragnar.config import RagnarError
    from ragnar.evaluation import read_golden

    path = tmp_path / "golden.jsonl"
    path.write_text('{"query": "q", "relevant": ["src\\\\a.py"]}\n\n', encoding="utf-8")
    assert read_golden(str(path)) == [{"query": "q", "relevant": ["src/a.py"]}]
    path.write_text('{"query": "q", "relevant": ["a"]}\n{"query": "q", "relevant": []}\n', encoding="utf-8")
    with pytest.raises(RagnarError, match=":2: needs"):
        read_golden(str(path))


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


def test_accented_file_names_are_not_quoted(tmp_path):
    # git quotes non-ASCII paths by default: "configura\303\247\303\243o.go" would match no file
    repo = make_repo(tmp_path)
    name = "widget/configuração.go"
    (repo / name).write_text("package widget\n\nfunc Ajusta() { println(\"tributação\") }\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "add the settings unit")
    snapshot = _git(repo, "rev-parse", "HEAD").strip()
    (repo / name).write_text("package widget\n\nfunc Ajusta() { println(\"tributação nova\") }\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "change how the settings screen computes the tax")
    assert [q["relevant"] for q in build_golden(str(repo), snapshot)] == [[name]]
    assert grep_baseline(str(repo), snapshot, "tributação") == [name]


def test_grep_terms_keep_accented_words_whole_and_add_the_plain_form():
    assert grep_terms("validação do CNPJ para emissão", STOPWORDS) == [
        ("validação", "validacao"), ("CNPJ",), ("emissão", "emissao")]
    assert grep_terms("the getUserById helper", STOPWORDS) == [("getUserById",), ("helper",)]


def test_informative_messages_say_what_changed():
    from ragnar.evaluation import informative

    assert informative("Corrige cálculo da multa da mensalidade atrasada")
    assert not informative("Ajustes gerais")
    assert not informative("fix minor changes in code")


def test_files_changed_in_passing_leave_the_right_answers(tmp_path):
    repo = make_repo(tmp_path)
    for i in range(6):  # the files must exist at the snapshot to count as answers
        (repo / f"unit{i}.go").write_text("package widget\n")
    (repo / "project.go").write_text("package widget\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "units")
    snapshot = _git(repo, "rev-parse", "HEAD").strip()
    for i in range(6):
        (repo / f"unit{i}.go").write_text(f"package widget\n\nfunc Unit{i}() {{}}\n")
        (repo / "project.go").write_text(f"package widget\n\nconst Units = {i}\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", f"cadastro de cliente numero {i} com validacao de documento")
    golden = build_golden(str(repo), snapshot)
    assert len(golden) == 6 and all(q["relevant"] == [f"unit{i}.go"] for i, q in enumerate(reversed(golden)))

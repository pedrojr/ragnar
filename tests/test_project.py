"""Ragnarizing a project: detection, .gitignore block, config file and health checks."""

from __future__ import annotations

from ragnar.config import load_settings
from ragnar.project import BEGIN, END, IGNORED, detect_languages, doctor, ensure_gitignore, init_project

from .test_unit import _git, make_repo


def test_gitignore_block_is_added_once(tmp_path):
    (tmp_path / ".gitignore").write_text("node_modules/", encoding="utf-8")
    assert ensure_gitignore(tmp_path) is True
    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert text.startswith("node_modules/\n\n" + BEGIN) and all(entry in text for entry in IGNORED)
    assert ensure_gitignore(tmp_path) is False
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == text


def test_gitignore_is_created_when_missing(tmp_path):
    assert ensure_gitignore(tmp_path) is True
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").startswith(BEGIN)


def test_gitignore_block_updates_in_place(tmp_path):
    (tmp_path / ".gitignore").write_text(f"a\n{BEGIN}\nold\n{END}\nb\n", encoding="utf-8")
    assert ensure_gitignore(tmp_path) is True
    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "old" not in text and text.startswith("a\n") and text.endswith(f"{END}\nb\n")


def test_detect_languages_counts_tracked_files(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "Orders.pas").write_text("unit Orders;\n", encoding="utf-8")
    (repo / "dist").mkdir()
    (repo / "dist" / "bundle.js").write_text("x\n", encoding="utf-8")
    _git(repo, "add", ".")
    assert detect_languages(repo) == {"go": 1, "delphi": 1}


def test_init_writes_a_valid_config_and_keeps_it(tmp_path):
    repo = make_repo(tmp_path)
    report = init_project(repo, preset="economy", agents_md=True)
    assert report.config_written and report.gitignore_changed and report.agents_changed
    settings = load_settings(repo, environ={})
    assert settings.project.name == "repo" and settings.project.branch == "main"
    assert settings.project.languages == ("go",) and settings.project.preset == "economy"

    (repo / "ragnar.toml").write_text('[project]\nname = "mine"\n', encoding="utf-8")
    again = init_project(repo)
    assert not again.config_written and not again.gitignore_changed
    assert load_settings(repo, environ={}).project.name == "mine"
    assert (repo / "AGENTS.md").read_text(encoding="utf-8").count("Code search (Ragnar)") == 1


def levels(checks):
    return {c.name: c.level for c in checks}


def test_doctor_reports_a_fresh_project(tmp_path):
    repo = make_repo(tmp_path)
    assert levels(doctor(repo))["ragnar.toml"] == "fail"
    init_project(repo)
    assert levels(doctor(repo)) == {"ragnar.toml": "ok", ".gitignore": "ok", "branch": "ok", "working tree": "ok"}


def test_doctor_measures_index_staleness(tmp_path):
    repo = make_repo(tmp_path)
    init_project(repo)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "ragnarize")
    head = _git(repo, "rev-parse", "HEAD").strip()
    assert levels(doctor(repo, head))["index"] == "ok"
    (repo / "x.go").write_text("package x\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "more")
    assert "1 commit(s) behind" in next(c.detail for c in doctor(repo, head) if c.name == "index")
    assert levels(doctor(repo, "0" * 40))["index"] == "warn"

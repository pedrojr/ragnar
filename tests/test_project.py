"""Ragnarizing a project: detection, .gitignore block, config folders and health checks."""

from __future__ import annotations

import os
import tomllib

import pytest

from ragnar.cli import SERVER_COMMANDS, main
from ragnar.config import CONFIG_FILE, ENV_FILE, LOCAL_CONFIG_FILE, REPO_OWNED, Index, Settings, load_settings
from ragnar.indexer import repo_settings
from ragnar.project import (
    AGENT_BEGIN,
    AGENT_END,
    AGENT_HEADING,
    AGENT_NOTE,
    BEGIN,
    END,
    IGNORED,
    INDEX_HELP,
    LEGACY_AGENT_NOTE,
    detect_languages,
    doctor,
    ensure_agent_notes,
    ensure_gitignore,
    init_project,
)

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
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == f"{BEGIN}\n.rag/\n{END}\n"


def test_gitignore_block_updates_in_place(tmp_path):
    (tmp_path / ".gitignore").write_text(f"a\n{BEGIN}\nold\n{END}\nb\n", encoding="utf-8")
    assert ensure_gitignore(tmp_path) is True
    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "old" not in text and text.startswith("a\n") and text.endswith(f"{END}\nb\n")


def test_detect_languages_counts_tracked_files(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "Orders.pas").write_text("unit Orders;\n", encoding="utf-8")
    (repo / "Configuração.pas").write_text("unit Configuracao;\n", encoding="utf-8")  # git quotes it by default
    (repo / "dist").mkdir()
    (repo / "dist" / "bundle.js").write_text("x\n", encoding="utf-8")
    _git(repo, "add", ".")
    assert detect_languages(repo) == {"go": 1, "delphi": 2}


def test_init_writes_a_valid_config_and_keeps_it(tmp_path):
    repo = make_repo(tmp_path)
    report = init_project(repo, preset="economy")
    assert report.config_written and report.gitignore_changed and report.agent_notes == {"AGENTS.md": "written"}
    settings = load_settings(repo, environ={})
    assert settings.project.name == "repo" and settings.project.branch == "main"
    assert settings.project.languages == ("go",) and settings.project.preset == "economy"

    (repo / CONFIG_FILE).write_text('[project]\nname = "mine"\n', encoding="utf-8")
    again = init_project(repo)
    assert not again.config_written and not again.gitignore_changed and again.local_written == []
    assert load_settings(repo, environ={}).project.name == "mine"
    assert (repo / "AGENTS.md").read_text(encoding="utf-8").count("Code search (Ragnar)") == 1
    assert again.agent_notes == {"AGENTS.md": "already up to date"}


def test_agent_notes_go_to_claude_md_unless_it_imports_agents_md(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# Team rules\n", encoding="utf-8")
    assert ensure_agent_notes(tmp_path) == {"AGENTS.md": "written", "CLAUDE.md": "updated"}
    claude = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert claude.startswith("# Team rules\n\n" + AGENT_BEGIN) and claude.endswith(AGENT_END + "\n")
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    assert ensure_agent_notes(tmp_path)["CLAUDE.md"] == "already imports AGENTS.md"


def test_agent_note_block_updates_in_place_and_replaces_the_legacy_note(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text(f"intro\n{AGENT_BEGIN}\nold rules\n{AGENT_END}\noutro\n", encoding="utf-8")
    assert ensure_agent_notes(tmp_path)["AGENTS.md"] == "updated"
    assert path.read_text(encoding="utf-8") == "intro\n" + AGENT_NOTE + "outro\n"
    path.write_text("# Rules" + LEGACY_AGENT_NOTE + "\n## Other\n", encoding="utf-8")
    assert ensure_agent_notes(tmp_path)["AGENTS.md"] == "updated"
    text = path.read_text(encoding="utf-8")
    assert text.count(AGENT_HEADING) == 1 and AGENT_BEGIN in text and text.endswith("\n## Other\n")


def test_agent_note_written_by_hand_is_kept(tmp_path):
    (tmp_path / "AGENTS.md").write_text(f"{AGENT_HEADING}\nOur own words.\n", encoding="utf-8")
    assert ensure_agent_notes(tmp_path)["AGENTS.md"].startswith("kept")
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == f"{AGENT_HEADING}\nOur own words.\n"


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_managed_blocks_keep_the_line_endings_of_the_file(tmp_path, eol):
    for name in ("AGENTS.md", ".gitignore"):
        (tmp_path / name).write_bytes(f"one{eol}two{eol}".encode())
    ensure_agent_notes(tmp_path), ensure_gitignore(tmp_path)
    for name in ("AGENTS.md", ".gitignore"):
        raw = (tmp_path / name).read_bytes()
        assert raw.startswith(f"one{eol}two{eol}".encode()) and raw.count(b"\n") == raw.count(eol.encode())


def test_markers_out_of_order_are_left_to_the_owner(tmp_path):
    text = f"mentions {AGENT_END} first\nmine\n{AGENT_BEGIN}\nmore of mine\n"
    (tmp_path / "AGENTS.md").write_text(text, encoding="utf-8", newline="\n")
    assert ensure_agent_notes(tmp_path)["AGENTS.md"].startswith("kept")
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == text
    (tmp_path / ".gitignore").write_text(f"{BEGIN}\nmine\n", encoding="utf-8", newline="\n")
    assert ensure_gitignore(tmp_path) is False


def test_init_can_leave_agent_files_alone(tmp_path, capsys):
    repo = make_repo(tmp_path)
    main(["init", "--dir", str(repo), "--no-agent-notes"])
    assert not (repo / "AGENTS.md").exists() and "AGENTS.md" not in capsys.readouterr().out
    main(["init", "--dir", str(repo), "--agents-md"])  # the old flag still parses
    assert "AGENTS.md: written" in capsys.readouterr().out


def test_init_creates_both_folders(tmp_path):
    repo = make_repo(tmp_path)
    report = init_project(repo)
    assert (repo / CONFIG_FILE).is_file() and report.local_written == [ENV_FILE, LOCAL_CONFIG_FILE]
    assert not (repo / "ragnar.toml").exists() and not (repo / "ragnar.local.toml").exists()
    assert (repo / ".gitignore").read_text(encoding="utf-8") == f"{BEGIN}\n.rag/\n{END}\n"
    # The templates hold placeholders only: every line is a comment.
    for rel in (ENV_FILE, LOCAL_CONFIG_FILE):
        lines = (repo / rel).read_text(encoding="utf-8").splitlines()
        assert lines and all(line.startswith("#") for line in lines if line.strip())
    assert tomllib.loads((repo / LOCAL_CONFIG_FILE).read_text(encoding="utf-8")) == {}
    _git(repo, "add", ".")
    tracked = _git(repo, "ls-files").split()
    assert CONFIG_FILE in tracked and not any(path.startswith(".rag/") for path in tracked)


def test_init_template_lists_every_repository_owned_key(tmp_path):
    repo = make_repo(tmp_path)
    init_project(repo)
    path = repo / CONFIG_FILE
    assert set(INDEX_HELP) == REPO_OWNED["index"]
    text = path.read_text(encoding="utf-8")
    for key in INDEX_HELP:
        assert f"\n# {key} = " in text
    # Uncommenting every key keeps the defaults: the template documents them correctly.
    lines = [line[2:] if line.startswith("# ") and line[2:].split(" = ")[0] in INDEX_HELP else line
             for line in text.splitlines()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert tomllib.loads(path.read_text(encoding="utf-8"))["index"].keys() == INDEX_HELP.keys()
    assert load_settings(repo, environ={}).index == Index()


def test_init_force_rewrites_only_the_config(tmp_path):
    repo = make_repo(tmp_path)
    init_project(repo)
    (repo / CONFIG_FILE).write_text('[project]\nname = "mine"\n', encoding="utf-8")
    (repo / ENV_FILE).write_text("RAGNAR_MCP_TOKEN=mine\n", encoding="utf-8")
    report = init_project(repo, force=True)
    assert report.config_written and report.local_written == []
    assert load_settings(repo, environ={}).project.name == "repo"
    assert (repo / ENV_FILE).read_text(encoding="utf-8") == "RAGNAR_MCP_TOKEN=mine\n"


def levels(checks):
    return {c.name: c.level for c in checks}


def test_doctor_reports_a_fresh_project(tmp_path):
    repo = make_repo(tmp_path)
    assert levels(doctor(repo))[CONFIG_FILE] == "fail"
    init_project(repo)
    assert levels(doctor(repo)) == {CONFIG_FILE: "ok", ".rag/": "ok", "agent notes": "ok", "branch": "ok",
                                    "working tree": "ok"}
    (repo / "AGENTS.md").unlink()
    assert levels(doctor(repo))["agent notes"] == "warn"


def test_doctor_flags_an_unignored_local_folder_and_stray_root_files(tmp_path):
    repo = make_repo(tmp_path)
    init_project(repo)
    (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    (repo / "ragnar.toml").write_text("[project]\n", encoding="utf-8")
    checks = {c.name: c for c in doctor(repo)}
    assert checks[".rag/"].level == "warn"
    assert checks["ragnar.toml"].level == "warn" and CONFIG_FILE in checks["ragnar.toml"].detail


def test_doctor_fails_on_committed_local_files(tmp_path):
    repo = make_repo(tmp_path)
    init_project(repo)
    _git(repo, "add", "-f", ENV_FILE)
    _git(repo, "commit", "-q", "-m", "oops")
    checks = [c for c in doctor(repo) if c.name == ".rag/"]
    assert [c.level for c in checks] == ["ok", "fail"] and ENV_FILE in checks[1].detail


def test_indexer_reads_the_config_committed_in_the_repository(tmp_path):
    repo = make_repo(tmp_path)
    init_project(repo)
    (repo / CONFIG_FILE).write_text('[index]\nchunk_budget = 900\nexclude = ["gen/*"]\n', encoding="utf-8")
    (repo / "ragnar.toml").write_text("[index]\nchunk_budget = 700\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "ragnarize")
    head = _git(repo, "rev-parse", "HEAD").strip()
    # Only the commit counts: an uncommitted edit in the working tree is ignored.
    (repo / CONFIG_FILE).write_text("[index]\nchunk_budget = 500\n", encoding="utf-8")
    s = repo_settings(repo, head, Settings())
    assert s.index.chunk_budget == 900 and s.index.exclude == ("gen/*",)


def test_server_commands_never_load_the_local_env_file(tmp_path, monkeypatch):
    import uvicorn

    (tmp_path / ".rag").mkdir()
    (tmp_path / ENV_FILE).write_text("RAGNAR_DSN_WRITE=postgresql://writer@primary/ragnar\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RAGNAR_DSN_WRITE", "unset")
    monkeypatch.delenv("RAGNAR_DSN_WRITE")  # restored to absent after the test
    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: seen.update(dsn=os.environ.get("RAGNAR_DSN_WRITE")))
    for cmd in sorted(SERVER_COMMANDS - {"embed-preload"}):
        main([cmd])
        assert seen.pop("dsn") is None, cmd
    main(["token", "--name", "x", "--repos", "*"])  # any other command loads the file
    assert os.environ["RAGNAR_DSN_WRITE"] == "postgresql://writer@primary/ragnar"


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


def test_an_unreachable_database_is_an_error_message(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RAGNAR_DSN_WRITE", "postgresql://u:p@127.0.0.1:1/ragnar?connect_timeout=2")
    try:
        main(["migrate"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected an exit")
    assert capsys.readouterr().err.startswith("error: database unavailable:")

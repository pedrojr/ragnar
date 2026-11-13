"""Repository rules that are easy to break by accident: translated docs, versions, plugin files."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from ragnar import __version__
from ragnar.project import BEGIN, END, IGNORED, ensure_gitignore

ROOT = Path(__file__).resolve().parent.parent
PAIRED_ROOT_DOCS = ["README", "CONTRIBUTING", "SECURITY", "CODE_OF_CONDUCT", "CHANGELOG"]
LINK = re.compile(r"\]\((?!https?://|#|mailto:)([^)#\s]+)")


def docs() -> list[Path]:
    return [*ROOT.glob("*.md"), *ROOT.glob("docs/**/*.md"), *ROOT.glob("example/*.md")]


@pytest.mark.parametrize("name", PAIRED_ROOT_DOCS)
def test_root_docs_have_a_translation(name):
    assert (ROOT / f"{name}.md").is_file() and (ROOT / f"{name}.pt-BR.md").is_file()


def test_example_readme_has_a_translation():
    assert (ROOT / "example" / "README.pt-BR.md").is_file()


def test_docs_folder_pairs_match():
    english = {p.name for p in (ROOT / "docs").glob("*.md")}
    assert english and english == {p.name for p in (ROOT / "docs" / "pt-BR").glob("*.md")}


def test_translations_keep_the_same_outline():
    pairs = [(ROOT / f"{n}.md", ROOT / f"{n}.pt-BR.md") for n in PAIRED_ROOT_DOCS]
    pairs.append((ROOT / "example" / "README.md", ROOT / "example" / "README.pt-BR.md"))
    pairs += [(p, ROOT / "docs" / "pt-BR" / p.name) for p in (ROOT / "docs").glob("*.md")]
    for en, pt in pairs:
        headings = [len(re.findall(r"^#{1,6} ", p.read_text(encoding="utf-8"), re.M)) for p in (en, pt)]
        assert headings[0] == headings[1], f"{en.name}: {headings}"


def test_internal_links_resolve():
    for doc in docs():
        for target in LINK.findall(doc.read_text(encoding="utf-8")):
            assert (doc.parent / target).exists(), f"{doc.relative_to(ROOT)}: {target}"


def test_versions_match():
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    assert package == plugin == __version__


def test_skills_have_frontmatter_matching_their_folder():
    skills = list((ROOT / "skills").glob("*/SKILL.md"))
    assert len(skills) == 7
    for skill in skills:
        head = skill.read_text(encoding="utf-8").split("---")[1]
        assert f"name: {skill.parent.name}\n" in head and "description: " in head


def test_mcp_config_uses_plugin_settings():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert plugin["userConfig"]["mcp_token"]["sensitive"] is True
    server = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["ragnar"]
    assert server["url"] == "${user_config.mcp_url}"


def test_scripts_are_executable_in_git():
    scripts = ["bin/ragnar", "hooks/pre-search.sh", "hooks/session-start.sh"]
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-s", *scripts],
                         capture_output=True, text=True, check=True).stdout
    assert [line.split()[0] for line in out.splitlines()] == ["100755"] * len(scripts)


BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash not installed")


def _hook(name: str, project: Path, stdin: str = "", tmp: Path | None = None) -> str:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project), "TMPDIR": str(tmp or project), "TEMP": str(tmp or project)}
    r = subprocess.run([BASH, str(ROOT / "hooks" / name)], input=stdin, capture_output=True, text=True, env=env,
                       timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_hooks_reference_existing_scripts():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    for groups in hooks.values():
        for group in groups:
            for hook in group["hooks"]:
                script = hook["args"][0].replace("${CLAUDE_PLUGIN_ROOT}", str(ROOT))
                assert Path(script).is_file(), script
    assert set(hooks) == {"SessionStart", "PreToolUse"}


@needs_bash
def test_session_start_hook_is_silent_outside_ragnarized_projects(tmp_path):
    assert _hook("session-start.sh", tmp_path) == ""
    assert not (tmp_path / ".gitignore").exists()


@needs_bash
def test_session_start_hook_adds_context_and_the_ignore_block(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".ragnar").mkdir()
    (tmp_path / ".ragnar" / "config.toml").write_text("", encoding="utf-8")
    for _ in range(2):  # a second run keeps one block and still adds the context
        out = json.loads(_hook("session-start.sh", tmp_path))["hookSpecificOutput"]
        assert out["hookEventName"] == "SessionStart" and "ragnar:search" in out["additionalContext"]
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").count(BEGIN) == 1


@needs_bash
@pytest.mark.parametrize(("tool", "tool_input", "hinted"), [
    ("Grep", {"pattern": "TOrder"}, True),
    ("Glob", {"pattern": "**/*.pas"}, True),
    ("Bash", {"command": "cd src && rg -n Validate"}, True),
    ("Bash", {"command": "git grep -n foo"}, True),
    ("Bash", {"command": "rtk grep -rn foo ."}, True),
    ("Bash", {"command": "git status"}, False),
    ("Bash", {"command": "uv run pytest -k grep"}, False),
])
def test_pre_search_hook_hints_once_per_session(tmp_path, tool, tool_input, hinted):
    project, tmp = tmp_path / "p", tmp_path / "tmp"
    project.mkdir(), tmp.mkdir()
    payload = json.dumps({"session_id": "s-1", "hook_event_name": "PreToolUse", "tool_name": tool,
                          "tool_input": tool_input})
    assert _hook("pre-search.sh", project, payload, tmp) == ""  # not a ragnarized project
    (project / ".ragnar").mkdir()
    (project / ".ragnar" / "config.toml").write_text("", encoding="utf-8")
    first = _hook("pre-search.sh", project, payload, tmp)
    if not hinted:
        assert first == ""
        return
    out = json.loads(first)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse" and "search_code" in out["additionalContext"]
    assert "permissionDecision" not in out  # never blocks or approves the call
    assert _hook("pre-search.sh", project, payload, tmp) == ""
    other = payload.replace("s-1", "s-2")
    assert _hook("pre-search.sh", project, other, tmp) != ""


def test_hook_writes_the_same_block_as_the_cli():
    hook = (ROOT / "hooks" / "session-start.sh").read_text(encoding="utf-8")
    block = hook.split("<<'EOF'\n")[1].split("EOF")[0]
    assert block == "\n".join((BEGIN, *IGNORED, END)) + "\n"


def test_hook_output_is_accepted_by_the_cli(tmp_path):
    hook = (ROOT / "hooks" / "session-start.sh").read_text(encoding="utf-8")
    block = hook.split("<<'EOF'\n")[1].split("EOF")[0]
    (tmp_path / ".gitignore").write_text(block, encoding="utf-8")
    assert ensure_gitignore(tmp_path) is False

"""Client-project side of Ragnar: "ragnarizing" a repository and checking its health."""

from __future__ import annotations

import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .config import CONFIG_FILE, LOCAL_CONFIG_FILE, PRESETS, RagnarError, load_settings
from .languages import BY_EXTENSION, is_denied

BEGIN, END = "# >>> ragnar >>>", "# <<< ragnar <<<"
IGNORED = (".ragnar/", LOCAL_CONFIG_FILE)
AGENT_NOTE = """
## Code search (Ragnar)

This project is indexed by Ragnar. Find code with the `find_symbol` and `search_code` MCP tools before
reading whole files or running broad greps. The index covers the tracked branch: for files changed
locally, read them directly.
"""
CONFIG_TEMPLATE = """# Ragnar settings. Full reference: docs/configuration.md
# Keep secrets out of this file: each section names the environment variable that holds its key.

[project]
name = "{name}"
branch = "{branch}"       # branch the index follows
preset = "{preset}"       # economy | balanced | precise
languages = [{languages}]

[index]
exclude = []             # globs, for example "legacy/thirdparty/*"
include = []             # extra globs indexed by line windows, for example "*.vue"
# index_tests = true
# index_docs = true
# chunk_budget = 1800
# legacy_encoding = "cp1252"
"""


def _git(root: Path, *args: str) -> str | None:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else None


def ensure_gitignore(root: Path) -> bool:
    """Adds the managed block to .gitignore. Returns True when the file changed."""
    path = root / ".gitignore"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    block = "\n".join((BEGIN, *IGNORED, END)) + "\n"
    if BEGIN in text and END in text:
        head, rest = text.split(BEGIN, 1)
        tail = rest.split(END, 1)[1].lstrip("\r\n")
        updated = head + block + tail
    else:
        updated = text + ("" if not text or text.endswith("\n") else "\n") + ("\n" if text else "") + block
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def tracked_files(root: Path) -> list[str]:
    listed = _git(root, "ls-files")
    if listed is not None:
        return listed.splitlines()
    skip = {".git", "node_modules", ".venv", "__pycache__"}
    return [p.relative_to(root).as_posix() for p in root.rglob("*")
            if p.is_file() and not skip & set(p.relative_to(root).parts)]


def detect_languages(root: Path) -> Counter:
    """Files per language, skipping what would never be indexed."""
    counts: Counter = Counter()
    for path in tracked_files(root):
        lang = BY_EXTENSION.get(Path(path).suffix.lower())
        if lang is not None and not is_denied(lang, path):
            counts[lang.name] += 1
    return counts


def default_branch(root: Path) -> str:
    remote = _git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if remote:
        return remote.removeprefix("origin/")
    return _git(root, "branch", "--show-current") or "main"


@dataclass
class InitReport:
    config_written: bool
    gitignore_changed: bool
    agents_changed: bool
    name: str
    branch: str
    languages: Counter = field(default_factory=Counter)


def init_project(root: Path, preset: str = "balanced", branch: str | None = None, name: str | None = None,
                 agents_md: bool = False, force: bool = False) -> InitReport:
    """Idempotent: an existing ragnar.toml stays unless force is set."""
    if preset not in PRESETS:
        raise RagnarError(f"unknown preset {preset!r}; use one of {', '.join(PRESETS)}")
    root = root.resolve()
    languages = detect_languages(root)
    project = name or root.name.lower()
    tracked = branch or default_branch(root)
    config = root / CONFIG_FILE
    write = force or not config.exists()
    if write:
        names = ", ".join(f'"{lang}"' for lang, _ in languages.most_common())
        config.write_text(CONFIG_TEMPLATE.format(name=project, branch=tracked, preset=preset, languages=names),
                          encoding="utf-8")
    agents_changed = False
    if agents_md:
        path = root / "AGENTS.md"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if "Code search (Ragnar)" not in text:
            path.write_text(text + AGENT_NOTE, encoding="utf-8")
            agents_changed = True
    return InitReport(write, ensure_gitignore(root), agents_changed, project, tracked, languages)


@dataclass
class Check:
    level: str  # ok | warn | fail
    name: str
    detail: str = ""


def doctor(root: Path, indexed_commit: str | None = None) -> list[Check]:
    """Local health of a ragnarized project. indexed_commit comes from the list_repos tool."""
    checks: list[Check] = []
    settings = None
    if (root / CONFIG_FILE).is_file():
        try:
            settings = load_settings(root)
            checks.append(Check("ok", CONFIG_FILE, f"preset {settings.project.preset}"))
        except RagnarError as exc:
            checks.append(Check("fail", CONFIG_FILE, str(exc)))
    else:
        checks.append(Check("fail", CONFIG_FILE, "missing; run `ragnar init`"))

    ignore = root / ".gitignore"
    managed = ignore.exists() and BEGIN in ignore.read_text(encoding="utf-8")
    checks.append(Check("ok" if managed else "warn", ".gitignore", "managed block found" if managed else "run `ragnar init`"))

    branch = settings.project.branch if settings else "main"
    head = _git(root, "rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}")
    if head is None:
        checks.append(Check("fail", "branch", f"{branch} not found in this clone"))
    else:
        checks.append(Check("ok", "branch", f"{branch} at {head[:12]}"))

    if indexed_commit and head:
        behind = _git(root, "rev-list", "--count", f"{indexed_commit}..{head}")
        if behind is None:
            checks.append(Check("warn", "index", f"indexed commit {indexed_commit[:12]} is not in this clone; fetch first"))
        elif behind == "0":
            checks.append(Check("ok", "index", f"up to date with {branch}"))
        else:
            checks.append(Check("warn", "index", f"{behind} commit(s) behind {branch}; check the index worker"))

    dirty = _git(root, "status", "--porcelain")
    if dirty:
        count = len(dirty.splitlines())
        checks.append(Check("ok", "working tree", f"{count} local change(s) are not in the index: read those files directly"))
    return checks

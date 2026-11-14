"""Client-project side of Ragnar: "ragnarizing" a repository and checking its health."""

from __future__ import annotations

import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .config import (
    CONFIG_DIR,
    CONFIG_FILE,
    ENV_FILE,
    LOCAL_CONFIG_FILE,
    LOCAL_DIR,
    PRESETS,
    Index,
    RagnarError,
    load_settings,
)
from .languages import BY_EXTENSION, is_denied

BEGIN, END = "# >>> ragnar >>>", "# <<< ragnar <<<"
IGNORED = (f"{LOCAL_DIR}/",)
LEGACY_FILES = {"ragnar.toml": CONFIG_FILE, "ragnar.local.toml": LOCAL_CONFIG_FILE}  # doctor flags them
AGENT_BEGIN, AGENT_END = "<!-- >>> ragnar >>> -->", "<!-- <<< ragnar <<< -->"
AGENT_HEADING = "## Code search (Ragnar)"
# Managed block in AGENTS.md and CLAUDE.md: `ragnar init` rewrites what sits between the markers.
# Self-contained on purpose: agents without the plugin never see the search skill.
AGENT_NOTE = f"""{AGENT_BEGIN}
{AGENT_HEADING}

This repository is indexed by Ragnar. On every prompt that needs to find or understand code, follow the
Ragnar `search` skill before you search; without the skill, apply these rules:

- Concept or behavior, such as "where do we validate tax IDs?": the `search_code` MCP tool.
- A known name: `find_symbol`. Its callers or uses: `find_references`. What it calls: `call_graph`.
- An exact string, an error message, or a file changed since the indexed commit: grep, then read the file.
- A large file: `file_outline`, then read only the lines you need.
- If the Ragnar tools fail, say so once and continue with local tools.
{AGENT_END}
"""
# The note `ragnar init --agents-md` appended before the managed block existed: replaced on the next init.
LEGACY_AGENT_NOTE = """
## Code search (Ragnar)

This project is indexed by Ragnar. For any question about where or how something works, call the
`search_code` MCP tool first: one call usually replaces several greps and file reads. Use `find_symbol`
for a known name, `find_references` for its callers, and `file_outline` before reading a large file.
Use grep for exact strings and for files changed locally: the index covers the tracked branch.
"""
AGENT_FILES = ("AGENTS.md", "CLAUDE.md")
# One line per [index] key a repository owns. A test checks that every key in REPO_OWNED has one.
INDEX_HELP = {
    "include": 'extra globs indexed by line windows, for example "*.vue"',
    "exclude": 'globs left out of the index, for example "legacy/thirdparty/*"',
    "max_file_kb": "larger files are left out",
    "chunk_budget": "non-whitespace characters per chunk",
    "window_lines": "chunk size, in lines, for files without a grammar",
    "window_overlap": "lines shared by consecutive line windows",
    "index_tests": "index test files",
    "index_docs": "index documentation files",
    "index_agent_notes": "index AGENTS.md, CLAUDE.md and similar agent notes",
    "legacy_encoding": "fallback encoding for sources that are not UTF-8",
    "summary_exclude": 'left out of file summaries, when the server turns them on, for example "generated/"',
}
CONFIG_HEADER = """# Ragnar settings for this repository. Commit this file.
# Full reference: https://github.com/pedrojr/ragnar/blob/main/docs/configuration.md
# Never put secrets here: keep them in {env}, which git ignores.
# Personal overrides go in {local}.

[project]
name = "{name}"  # repository name in the index
branch = "{branch}"  # branch the index follows
preset = "{preset}"  # economy | balanced | precise
languages = [{languages}]  # languages to index; empty means every supported language

# The index worker reads this section from the tracked branch. Uncomment a key to change it.
# A change here rebuilds the repository's index on the next run.
[index]
"""
LOCAL_CONFIG_TEMPLATE = f"""# Personal Ragnar overrides for this machine. Git ignores the {LOCAL_DIR}/ folder.
# Same sections and keys as {CONFIG_FILE}; values here win over it.
# RAGNAR__SECTION__KEY environment variables and --set win over both.
#
# [retrieval]
# default_k = 5
# max_context_tokens = 2000
"""
ENV_TEMPLATE = f"""# Environment for the ragnar CLI in this project. Git ignores the {LOCAL_DIR}/ folder: never commit it.
# Uncomment and fill in what you use. Variables already set in your shell win over these.
# The mcp and embedder server commands never read this file.
#
# RAGNAR_DSN_READ=postgresql://<user>:<password>@<host>:5432/ragnar
# RAGNAR_MCP_TOKEN=<token created with `ragnar token`>
# RAGNAR_EMBEDDING_API_KEY=<key>
# RAGNAR_RERANK_API_KEY=<key>
# RAGNAR_LLM_API_KEY=<key>
"""


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, tuple):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return str(value)


def config_template(name: str, branch: str, preset: str, languages: list[str]) -> str:
    """The .ragnar/config.toml that `ragnar init` writes: every repository-owned key, with its default."""
    head = CONFIG_HEADER.format(env=ENV_FILE, local=LOCAL_CONFIG_FILE, name=name, branch=branch, preset=preset,
                                languages=", ".join(f'"{lang}"' for lang in languages))
    defaults = Index()
    lines = [f"# {key} = {_toml_value(getattr(defaults, key))}" for key in INDEX_HELP]
    width = max(map(len, lines))
    body = "".join(f"{line.ljust(width)}  # {INDEX_HELP[key]}\n" for line, key in zip(lines, INDEX_HELP))
    return head + body


def _git(root: Path, *args: str) -> str | None:
    # core.quotePath=false: accented paths as they are, not "configura\303\247\303\243o.pas"
    r = subprocess.run(["git", "-c", "core.quotePath=false", "-C", str(root), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else None


def _read_lf(path: Path) -> tuple[str, str]:
    """The text with LF line endings, and the line ending the file uses, so a rewrite keeps it."""
    if not path.exists():
        return "", "\n"
    with path.open(encoding="utf-8", newline="") as f:  # Path.read_text takes newline only from Python 3.13
        raw = f.read()
    return raw.replace("\r\n", "\n"), "\r\n" if "\r\n" in raw else "\n"


def _write_eol(path: Path, text: str, eol: str) -> None:
    path.write_text(text.replace("\n", eol), encoding="utf-8", newline="")


def _replace_block(text: str, begin: str, end: str, block: str) -> str | None:
    """Swaps the block between the markers; None when they are missing or out of order."""
    head, found, rest = text.partition(begin)
    if not found or end not in rest:
        return None
    return head + block + rest.split(end, 1)[1].lstrip("\n")


def _append(text: str, block: str) -> str:
    return text + ("" if not text or text.endswith("\n") else "\n") + ("\n" if text else "") + block


def ensure_gitignore(root: Path) -> bool:
    """Adds the managed block to .gitignore. Returns True when the file changed."""
    path = root / ".gitignore"
    text, eol = _read_lf(path)
    block = "\n".join((BEGIN, *IGNORED, END)) + "\n"
    updated = _replace_block(text, BEGIN, END, block)
    if updated is None and BEGIN in text:
        return False  # a begin marker without its end: leave the file to its owner, as the session hook does
    if updated is None:
        updated = _append(text, block)
    if updated == text:
        return False
    _write_eol(path, updated, eol)
    return True


def _upsert_agent_note(path: Path) -> str:
    """Writes the managed block into one instruction file. Returns what happened, for the report."""
    text, eol = _read_lf(path)
    updated = _replace_block(text, AGENT_BEGIN, AGENT_END, AGENT_NOTE)
    if updated is not None:
        pass
    elif AGENT_BEGIN in text or AGENT_END in text:
        # one marker alone, or both out of order: a rewrite could swallow the text between them
        return "kept (the Ragnar markers are incomplete or out of order; fix them by hand)"
    elif LEGACY_AGENT_NOTE in text:
        updated = text.replace(LEGACY_AGENT_NOTE, "\n" + AGENT_NOTE, 1)
    elif AGENT_HEADING in text:
        return "kept (has a Ragnar section without the managed markers)"
    else:
        updated = _append(text, AGENT_NOTE)
    if updated == text:
        return "already up to date"
    _write_eol(path, updated, eol)
    return "updated" if text else "written"


def ensure_agent_notes(root: Path) -> dict[str, str]:
    """The search rules in AGENTS.md, which most agents read, and in CLAUDE.md when the project has one.

    Claude Code reads CLAUDE.md, not AGENTS.md. A CLAUDE.md that imports @AGENTS.md already gets the rules.
    """
    report = {"AGENTS.md": _upsert_agent_note(root / "AGENTS.md")}
    claude = root / "CLAUDE.md"
    if claude.is_file():
        if "@AGENTS.md" in claude.read_text(encoding="utf-8"):
            report["CLAUDE.md"] = "already imports AGENTS.md"
        else:
            report["CLAUDE.md"] = _upsert_agent_note(claude)
    return report


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def has_agent_note(root: Path) -> bool:
    return any(AGENT_HEADING in _read(root / name) for name in AGENT_FILES)


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
    name: str
    branch: str
    languages: Counter = field(default_factory=Counter)
    local_written: list[str] = field(default_factory=list)  # files created in .rag/
    agent_notes: dict[str, str] = field(default_factory=dict)  # instruction file -> what init did to it


def _write_once(path: Path, text: str) -> bool:
    if path.exists():
        return False
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


def init_project(root: Path, preset: str = "balanced", branch: str | None = None, name: str | None = None,
                 agent_notes: bool = True, force: bool = False) -> InitReport:
    """Idempotent. An existing .ragnar/config.toml stays unless force is set; files in .rag/ always stay.

    agent_notes writes the search rules into AGENTS.md and CLAUDE.md, inside a managed block.
    """
    if preset not in PRESETS:
        raise RagnarError(f"unknown preset {preset!r}; use one of {', '.join(PRESETS)}")
    root = root.resolve()
    languages = detect_languages(root)
    project = name or root.name.lower()
    tracked = branch or default_branch(root)
    (root / CONFIG_DIR).mkdir(exist_ok=True)
    config = root / CONFIG_FILE
    write = force or not config.exists()
    if write:
        text = config_template(project, tracked, preset, [lang for lang, _ in languages.most_common()])
        config.write_text(text, encoding="utf-8", newline="\n")
    (root / LOCAL_DIR).mkdir(exist_ok=True)
    local_written = [rel for rel, text in ((ENV_FILE, ENV_TEMPLATE), (LOCAL_CONFIG_FILE, LOCAL_CONFIG_TEMPLATE))
                     if _write_once(root / rel, text)]
    notes = ensure_agent_notes(root) if agent_notes else {}
    return InitReport(write, ensure_gitignore(root), project, tracked, languages, local_written, notes)


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

    for legacy, target in LEGACY_FILES.items():
        if (root / legacy).is_file():
            checks.append(Check("warn", legacy, f"Ragnar no longer reads this file; move it to {target}"))

    ignored = _git(root, "check-ignore", "-q", "--no-index", f"{LOCAL_DIR}/{Path(ENV_FILE).name}") is not None
    checks.append(Check("ok" if ignored else "warn", f"{LOCAL_DIR}/",
                        "ignored by git" if ignored else "not ignored by git; run `ragnar init`"))

    if has_agent_note(root):
        checks.append(Check("ok", "agent notes", "search rules in " + " and ".join(
            name for name in AGENT_FILES if AGENT_HEADING in _read(root / name))))
    else:
        checks.append(Check("warn", "agent notes", "no search rules in AGENTS.md or CLAUDE.md; run `ragnar init`"))

    branch = settings.project.branch if settings else "main"
    # origin first: the worker follows the remote branch, and a clone may never have checked it out
    head = next((h for ref in (f"origin/{branch}", branch)
                 if (h := _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"))), None)
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

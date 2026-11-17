"""Real agent runs: the same question to Claude Code with local tools only, and with the Ragnar MCP tools too.

    uv run python benchmark/agent.py --label final --questions 3 --model haiku
    uv run python benchmark/agent.py --rescore final      # score saved answers again, no model calls

Needs the `claude` CLI and a Ragnar MCP server on 127.0.0.1:18080. Runs spend model usage, so each
run has a budget cap and the question count is explicit. Each run works in the repository clone, at the snapshot.

Beyond `correct`, each answer is checked in two ways that need no model. On a question with no answer in the
repository, the agent must say NOT FOUND. Every cited path:line must exist at the snapshot: a missing file or a line
past the end is an invented citation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path, PurePosixPath
from statistics import mean

sys.path.insert(0, str(Path(__file__).parent))
from bench import REPOS, WORK, git, load_questions, manifest  # noqa: E402

from ragnar.project import AGENT_NOTE  # noqa: E402

NOT_FOUND = "NOT FOUND"
PROMPT = """{question}

Answer in at most three sentences and cite the file paths that hold the answer as path:line. If the repository does \
not implement it, start the answer with NOT FOUND."""
ANSWER_CHARS = 4000  # kept in agent.json; 600 cut citations off in older runs
# a file name with an extension, optionally after folders and before :line, :line-line or #L10-L20
CITATION = re.compile(r"(?<![\w/\\.-])((?:[A-Za-z]:)?[/\\]?(?:[\w.-]+[/\\])*[\w.-]+\.([A-Za-z]\w{0,7}))"
                      r"(?:(?::|#L)(\d+)(?:[-–]L?(\d+))?)?")
LOCAL_TOOLS = "Read,Grep,Glob"
# every tool the server offers: AGENT_NOTE names call_graph and trace_flow, and a denied call wastes a turn
RAGNAR_TOOLS = ",".join(f"mcp__ragnar__{t}" for t in ("search_code", "find_symbol", "find_references", "file_outline",
                                                      "read_code", "list_repos", "call_graph", "trace_flow",
                                                      "find_table_usage", "repo_map"))
SKIP_ENV = re.compile(r"^(CLAUDE|ANTHROPIC_BASE)")  # a child session must not inherit this one
CATEGORIES = ("concept", "flow", "callers", "ptbr", "config", "form", "negative")


def pick(questions: list[dict], count: int, split: str, categories: tuple[str, ...] = CATEGORIES) -> list[dict]:
    """Curated questions of one split, spread over the categories in file order."""
    pool = [q for q in questions if q["source"] == "curated" and split in ("all", q["split"]) and q["category"] in categories]
    chosen: list[dict] = []
    seen: set[str] = set()
    for q in pool:
        if len(chosen) < count and q["category"] not in seen:
            seen.add(q["category"])
            chosen.append(q)
    for q in pool:
        if len(chosen) < count and q not in chosen:
            chosen.append(q)
    return chosen


def abstained(answer: str) -> bool:
    """The answer opens with NOT FOUND as a verdict. "NOT FOUND is not the answer here" is not one."""
    return re.match(rf"{NOT_FOUND}\s*([.:;,!*_`)\-–—]|$)", answer.lstrip(" *_`#>\"'\n").upper()) is not None


def correct(answer: str, q: dict) -> bool:
    """An answer counts when it cites an expected file. With no answer in the repository, it must say NOT FOUND."""
    if not q["relevant"]:
        return abstained(answer)
    cited = answer.replace("\\", "/").lower()
    return not abstained(answer) and any(path.lower() in cited or Path(path).name.lower() in cited for path in q["relevant"])


def citations(answer: str, extensions: set[str], truncated: bool = False) -> list[tuple[str, int | None, int | None]]:
    """(path, first line, last line) of each cited file, as `path:line` or a `#L` link anchor.

    A file name with neither a line nor a folder is a mention, not a citation. Only extensions the repository has
    count, so `e.g.` and `Engine.handleHTTPRequest` do not. A citation at the cut of a truncated answer is left out."""
    out = []
    for m in CITATION.finditer(answer):
        if m.group(2).lower() not in extensions or (truncated and m.end() >= len(answer)):
            continue
        if not m.group(3) and not re.search(r"[/\\]", m.group(1)):
            continue
        first = int(m.group(3)) if m.group(3) else None
        out.append((m.group(1).replace("\\", "/"), first, int(m.group(4)) if m.group(4) else first))
    return out


@cache
def snapshot_files(repo: str, snapshot: str) -> dict[str, str]:
    """Lowercase path to real path, for every file at the snapshot."""
    names = git(repo, "ls-tree", "-r", "--name-only", snapshot).splitlines()
    return {name.lower(): name for name in names}


@cache
def file_lines(repo: str, snapshot: str, path: str) -> list[str]:
    return git(repo, "show", f"{snapshot}:{path}", check=False).splitlines()


def resolve(cited: str, files: dict[str, str], repo: str) -> list[str]:
    """Snapshot files a cited path can mean: the exact path, or every file whose path ends with it.

    A path through the clone, such as `D:/x/repos/gin/tree.go` or `benchmark/repos/gin/tree.go`, loses the folders up
    to the repository name. Otherwise every folder stays, so a right name in a wrong folder counts as invented."""
    key = cited.lower().removeprefix("./")
    parts = PurePosixPath(key).parts
    for start in range(len(parts)):
        if start and parts[start - 1] != repo.lower():
            continue
        tail = "/".join(parts[start:])
        found = [real for low, real in files.items() if low == tail or low.endswith("/" + tail)]
        if found:
            return found
    return []


def invented_citations(answer: str, entry: dict, truncated: bool = False) -> tuple[int, list[str]]:
    """How many citations the answer makes, and those that point at no file, or past the end of the file."""
    repo, snapshot = entry["name"], entry["snapshot"]
    files = snapshot_files(repo, snapshot)
    extensions = {PurePosixPath(f).suffix.lstrip(".").lower() for f in files if PurePosixPath(f).suffix}
    found = citations(answer, extensions, truncated)
    invented = []
    for path, first, last in found:
        targets = resolve(path, files, repo)
        if first is not None:
            targets = [t for t in targets if 1 <= first and max(first, last or first) <= len(file_lines(repo, snapshot, t))]
        if not targets:
            invented.append(f"{path}:{first}" if first else path)
    return len(found), invented


def score(answer: str, q: dict, entry: dict, truncated: bool = False) -> dict:
    cited, invented = invented_citations(answer, entry, truncated)
    return {"correct": correct(answer, q), "abstained": abstained(answer), "citations": cited, "invented": invented}


def summary(rows: list[dict]) -> str:
    """Per arm, on the questions every arm in the run finished."""
    done = [r for r in rows if "failed" not in r]
    ids =set.intersection(*[{r["id"] for r in done if r["arm"] == arm} for arm in {r["arm"] for r in done}] or [set()])
    out = []
    for arm in ("grep", "ragnar"):
        chosen = [r for r in done if r["arm"] == arm and r["id"] in ids]
        if not chosen:
            continue
        negative = [r for r in chosen if r["category"] == "negative"]
        answered = [r for r in chosen if r["category"] != "negative"]
        cited = sum(r["citations"] for r in chosen)
        invented = sum(len(r["invented"]) for r in chosen)
        out.append(f"{arm:7} runs {len(chosen):3}  correct {mean(r['correct'] for r in chosen):.0%}  "
                   f"NOT FOUND on negatives {sum(r['abstained'] for r in negative)}/{len(negative)}  "
                   f"false NOT FOUND {sum(r['abstained'] for r in answered)}/{len(answered)}  "
                   f"invented citations {invented}/{cited} in {sum(bool(r['invented']) for r in chosen)} answers")
    return "\n".join(out)


def rescore(label: str) -> None:
    """Scores the saved answers of a run again. Answers saved before ANSWER_CHARS were cut at 600 characters."""
    path = WORK / label / "agent.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    entries = {e["name"]: e for e in manifest()}
    questions = {q["id"]: q for e in entries.values() for q in load_questions(e)}
    for row in rows:
        if "failed" not in row:
            answer = row.get("answer", "")
            row.update(score(answer, questions[row["id"]], entries[row["repo"]], truncated=len(answer) in (600, ANSWER_CHARS)))
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=0), encoding="utf-8")
    print(summary(rows))


def run(q: dict, entry: dict, arm: str, model: str, budget: float, mcp_config: str) -> dict:
    tools = LOCAL_TOOLS if arm == "grep" else f"{LOCAL_TOOLS},{RAGNAR_TOOLS}"
    cmd = ["claude", "-p", PROMPT.format(question=q["query"]), "--model", model, "--output-format", "stream-json", "--verbose",
           "--no-session-persistence", "--setting-sources", "project", "--tools", LOCAL_TOOLS, "--allowedTools", tools,
           "--max-budget-usd", str(budget), "--strict-mcp-config", "--disable-slash-commands"]
    if arm == "ragnar":  # the search rules `ragnar init` writes into a project's AGENTS.md
        cmd += ["--mcp-config", mcp_config, "--append-system-prompt", AGENT_NOTE]
    env = {k: v for k, v in os.environ.items() if not SKIP_ENV.match(k)}
    t0 = time.perf_counter()
    r = subprocess.run(cmd, cwd=REPOS / entry["name"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=600)
    row = {"id": q["id"], "repo": q["repo"], "language": entry["language"], "category": q["category"], "arm": arm,
           "seconds": round(time.perf_counter() - t0, 1)}
    events = []
    for line in r.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    out = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if out is None:
        return {**row, "failed": (r.stderr or r.stdout).strip()[-300:]}
    tools_used = Counter(block["name"].removeprefix("mcp__ragnar__") for e in events if e.get("type") == "assistant"
                         for block in e.get("message", {}).get("content", []) if block.get("type") == "tool_use")
    usage = out.get("usage", {})
    answer = out.get("result") or ""
    context = sum(usage.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return {**row, **score(answer, q, entry), "turns": out.get("num_turns"), "cost_usd": out.get("total_cost_usd"),
            "input_tokens": context, "output_tokens": usage.get("output_tokens", 0), "subtype": out.get("subtype"),
            "tools": dict(tools_used), "answer": answer[:ANSWER_CHARS]}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--label")
    p.add_argument("--rescore", metavar="LABEL", help="score the saved answers of a run again and exit")
    p.add_argument("--questions", type=int, default=3, help="questions per repository")
    p.add_argument("--category", action="append", default=[], choices=CATEGORIES)
    p.add_argument("--model", default="haiku")
    p.add_argument("--budget", type=float, default=0.5, help="USD cap per run")
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--url", default="http://127.0.0.1:18080/mcp")
    p.add_argument("--token-env", default="RAGNAR_BENCH_TOKEN")
    p.add_argument("--repo", action="append", default=[])
    p.add_argument("--split", default="holdout", choices=("dev", "holdout", "all"), help="tune on dev, report on holdout")
    p.add_argument("--arm", action="append", default=[], choices=("grep", "ragnar"))
    args = p.parse_args()
    if args.rescore:
        rescore(args.rescore)
        return
    if not args.label:
        p.error("--label is required")

    config = {"mcpServers": {"ragnar": {"type": "http", "url": args.url,
                                        "headers": {"Authorization": f"Bearer {os.environ[args.token_env]}"}}}}
    entries = [e for e in manifest() if not args.repo or e["name"] in args.repo]
    arms = args.arm or ["grep", "ragnar"]
    categories = tuple(args.category) or CATEGORIES
    jobs = [(q, e, arm) for e in entries for q in pick(load_questions(e), args.questions, args.split, categories) for arm in arms]
    print(f"{len(jobs)} runs, at most ${len(jobs) * args.budget:.2f}", flush=True)
    out = WORK / args.label
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ragnar-bench-") as tmp:
        mcp_config = str(Path(tmp) / "mcp.json")
        Path(mcp_config).write_text(json.dumps(config), encoding="utf-8")
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            rows = []
            for row in pool.map(lambda j: run(j[0], j[1], j[2], args.model, args.budget, mcp_config), jobs):
                rows.append(row)
                print({k: row.get(k) for k in ("id", "arm", "correct", "abstained", "invented", "turns", "input_tokens",
                                               "cost_usd", "seconds", "tools", "failed")}, flush=True)
                (out / "agent.json").write_text(json.dumps(rows, ensure_ascii=False, indent=0), encoding="utf-8")
    print(summary(rows))


if __name__ == "__main__":
    main()

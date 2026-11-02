"""Real agent runs: the same question to Claude Code with local tools only, and with the Ragnar MCP tools too.

    uv run python benchmark/agent.py --label final --questions 3 --model haiku

Needs the `claude` CLI and a Ragnar MCP server on 127.0.0.1:18080. Runs spend model usage, so each
run has a budget cap and the question count is explicit. Each run works in the repository clone, at the snapshot.
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bench import REPOS, WORK, load_questions, manifest  # noqa: E402

PROMPT = """{question}

Answer in at most three sentences and cite the file paths that hold the answer as path:line."""
LOCAL_TOOLS = "Read,Grep,Glob"
RAGNAR_TOOLS = ",".join(f"mcp__ragnar__{t}" for t in ("search_code", "find_symbol", "find_references", "file_outline",
                                                      "read_code", "list_repos"))
SKIP_ENV = re.compile(r"^(CLAUDE|ANTHROPIC_BASE)")  # a child session must not inherit this one
CATEGORIES = ("concept", "flow", "callers", "ptbr", "config", "form")


def pick(questions: list[dict], count: int) -> list[dict]:
    """Holdout questions only, spread over the categories in file order."""
    pool = [q for q in questions if q["source"] == "curated" and q["split"] == "holdout" and q["category"] in CATEGORIES]
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


def correct(answer: str, q: dict) -> bool:
    """An answer counts when it cites an expected file."""
    cited = answer.replace("\\", "/").lower()
    return any(path.lower() in cited or Path(path).name.lower() in cited for path in q["relevant"])


def run(q: dict, entry: dict, arm: str, model: str, budget: float, mcp_config: str) -> dict:
    tools = LOCAL_TOOLS if arm == "grep" else f"{LOCAL_TOOLS},{RAGNAR_TOOLS}"
    cmd = ["claude", "-p", PROMPT.format(question=q["query"]), "--model", model, "--output-format", "json",
           "--no-session-persistence", "--setting-sources", "project", "--tools", LOCAL_TOOLS, "--allowedTools", tools,
           "--max-budget-usd", str(budget), "--strict-mcp-config", "--disable-slash-commands"]
    if arm == "ragnar":
        cmd += ["--mcp-config", mcp_config]
    env = {k: v for k, v in os.environ.items() if not SKIP_ENV.match(k)}
    t0 = time.perf_counter()
    r = subprocess.run(cmd, cwd=REPOS / entry["name"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=600)
    row = {"id": q["id"], "repo": q["repo"], "language": entry["language"], "category": q["category"], "arm": arm,
           "seconds": round(time.perf_counter() - t0, 1)}
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {**row, "failed": (r.stderr or r.stdout).strip()[-300:]}
    usage = out.get("usage", {})
    answer = out.get("result") or ""
    context = sum(usage.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return {**row, "correct": correct(answer, q), "turns": out.get("num_turns"), "cost_usd": out.get("total_cost_usd"),
            "input_tokens": context, "output_tokens": usage.get("output_tokens", 0), "subtype": out.get("subtype"),
            "answer": answer[:600]}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--label", required=True)
    p.add_argument("--questions", type=int, default=3, help="questions per repository")
    p.add_argument("--model", default="haiku")
    p.add_argument("--budget", type=float, default=0.5, help="USD cap per run")
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--url", default="http://127.0.0.1:18080/mcp")
    p.add_argument("--token-env", default="RAGNAR_BENCH_TOKEN")
    p.add_argument("--repo", action="append", default=[])
    args = p.parse_args()

    config = {"mcpServers": {"ragnar": {"type": "http", "url": args.url,
                                        "headers": {"Authorization": f"Bearer {os.environ[args.token_env]}"}}}}
    entries = [e for e in manifest() if not args.repo or e["name"] in args.repo]
    jobs = [(q, e, arm) for e in entries for q in pick(load_questions(e), args.questions) for arm in ("grep", "ragnar")]
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
                print({k: row.get(k) for k in ("id", "arm", "correct", "turns", "input_tokens", "cost_usd", "seconds", "failed")},
                      flush=True)
                (out / "agent.json").write_text(json.dumps(rows, ensure_ascii=False, indent=0), encoding="utf-8")


if __name__ == "__main__":
    main()

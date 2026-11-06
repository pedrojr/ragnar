"""Aggregates the results of a private suite into a file that names nothing in it.

    RAGNAR_BENCH_SUITE=/path/to/suite uv run python benchmark/summarize_suite.py \
        --description "Monorepo of a large Delphi system" --run v0=mono-v0/eval --run final=mono-final/eval \
        --index mono-final --out benchmark/monorepo.json

Only counts and means leave the suite: no path, query, symbol or repository name. Sizes are
rounded to two significant digits, so they describe the repository without fingerprinting it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).parent))
from bench import REPOS, WORK, manifest, split_of  # noqa: E402

from ragnar.languages import BY_EXTENSION, FORM_EXTENSIONS  # noqa: E402

ARMS = ("grep", "ragnar")


def stats(rows: list[dict], arm: str) -> dict:
    scored = [r[arm] for r in rows if r[arm]["rr"] is not None]
    if not scored:
        return {"n": 0}
    return {"n": len(scored), "mrr": round(mean(a["rr"] for a in scored), 3),
            "recall10": round(mean(a["recall10"] for a in scored), 3),
            "solved": round(mean(a["solved"] for a in scored), 3),
            "tokens": round(median(a["tokens"] for a in (r[arm] for r in rows)))}


def groups(rows: list[dict]) -> dict:
    out = {"all": rows, "holdout": [r for r in rows if r["split"] == "holdout"], "dev": [r for r in rows if r["split"] == "dev"]}
    for r in rows:
        out.setdefault(f"source:{r['source']}", []).append(r)
        if r["source"] == "curated":
            out.setdefault(f"category:{r['category']}", []).append(r)
    return {name: {arm: stats(g, arm) for arm in ARMS} for name, g in out.items()}


def paired(a: list[dict], b: list[dict]) -> dict:
    """MRR of b minus a on the same holdout questions."""
    by_id = {r["id"]: r for r in b}
    pairs = [(r, by_id[r["id"]]) for r in a if r["id"] in by_id and r["split"] == "holdout" and r["ragnar"]["rr"] is not None]
    if not pairs:
        return {"n": 0}
    return {"n": len(pairs), "mrr_a": round(mean(x["ragnar"]["rr"] for x, _ in pairs), 3),
            "mrr_b": round(mean(y["ragnar"]["rr"] for _, y in pairs), 3),
            "solved_a": round(mean(x["ragnar"]["solved"] for x, _ in pairs), 3),
            "solved_b": round(mean(y["ragnar"]["solved"] for _, y in pairs), 3)}


def agent_summary(rows: list[dict]) -> dict:
    """Paired runs only: a question counts when both modes finished."""
    done = [r for r in rows if "failed" not in r]
    ids = {r["id"] for r in done if r["arm"] == "grep"} & {r["id"] for r in done if r["arm"] == "ragnar"}
    out = {}
    for arm in ("grep", "ragnar"):
        chosen = [r for r in done if r["arm"] == arm and r["id"] in ids]
        if chosen:
            out[arm] = {"runs": len(chosen), "correct": round(mean(r["correct"] for r in chosen), 3),
                        "turns": round(mean(r["turns"] or 0 for r in chosen), 1),
                        "context": round(median(r["input_tokens"] for r in chosen)),
                        "cost": round(sum(r["cost_usd"] or 0 for r in chosen), 2)}
    return out


def rough(value: float | None) -> int | None:
    """Two significant digits: 1,234,567 lines become 1,200,000."""
    return None if value is None else int(float(f"{value:.2g}"))


def line_counts(entry: dict) -> dict:
    """Lines of the whole repository at the snapshot, by kind of file."""
    repo = REPOS / entry["name"]
    names = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", entry["snapshot"]], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", check=True).stdout.splitlines()
    counts = {"source": 0, "forms": 0, "files": len(names)}
    for name in names:
        suffix = Path(name).suffix.lower()
        kind = "forms" if suffix in FORM_EXTENSIONS else "source" if suffix in BY_EXTENSION else None
        if kind:
            path = repo / name
            counts[kind] += path.read_bytes().count(b"\n") if path.exists() else 0
    return {k: rough(v) for k, v in counts.items()}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--description", required=True)
    p.add_argument("--run", action="append", default=[], help="name=label/file, in order: the first is the baseline")
    p.add_argument("--index", required=True, help="label whose index.json describes the final index")
    p.add_argument("--agent", help="label whose agent.json holds the agent runs")
    p.add_argument("--out", required=True)
    args = p.parse_args()
    entry = manifest()[0]
    runs = {}
    for spec in args.run:
        name, path = spec.split("=", 1)
        runs[name] = json.loads((WORK / f"{path}.json").read_text(encoding="utf-8"))
    names = list(runs)
    index = json.loads((WORK / args.index / "index.json").read_text(encoding="utf-8"))
    repo = index["repos"][0]
    summary = {
        "description": args.description,
        "language": entry["language"],
        "lines": line_counts(entry),
        "index": {k: rough(repo.get(k)) for k in ("files", "chunks", "symbols", "seconds")}
                 | {"skipped": repo.get("skipped"), "database_bytes": rough(index.get("database_bytes"))},
        "questions": {"curated": sum(r["source"] == "curated" for r in runs[names[-1]]),
                      "history": sum(r["source"] == "history" for r in runs[names[-1]])},
        "runs": {name: groups(rows) for name, rows in runs.items()},
        "evolution": {f"{names[0]}->{name}": paired(runs[names[0]], runs[name]) for name in names[1:]},
    }
    if args.agent and (WORK / args.agent / "agent.json").exists():
        summary["agent"] = agent_summary(json.loads((WORK / args.agent / "agent.json").read_text(encoding="utf-8")))
    out = Path(args.out)
    if out.exists():  # the prose written by hand stays
        summary = {**{k: v for k, v in json.loads(out.read_text(encoding="utf-8")).items() if k in ("text", "embedding_hours")},
                   **summary}
    assert all(split_of(r["id"]) == r["split"] for r in runs[names[-1]])
    out.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary["runs"][names[-1]]["holdout"]), file=sys.stderr)


if __name__ == "__main__":
    main()

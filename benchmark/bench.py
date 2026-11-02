"""Benchmark harness: before and after Ragnar, on pinned open source repositories.

    uv run python benchmark/bench.py prepare                 # clone, check out snapshots, history questions
    uv run python benchmark/bench.py index --label final     # empty database, index every repository
    uv run python benchmark/bench.py evaluate --label final  # retrieval scores, with and without Ragnar

PYTHONPATH selects the Ragnar under test, so a frozen source tree runs through this same script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from fnmatch import fnmatch
from pathlib import Path
from statistics import mean, median

import psycopg

ROOT = Path(__file__).resolve().parent
REPOS = ROOT / "repos"
WORK = ROOT / "work"
QUESTIONS = ROOT / "questions"
ADMIN_DSN = "postgresql://postgres:benchmark@127.0.0.1:15433/{db}"
EMBEDDER = os.environ.get("RAGNAR_BENCH_EMBEDDER", "http://127.0.0.1:8001/v1")
CHARS_PER_TOKEN = 4
GREP_MAX_LINES = 250  # an agent's grep output is capped; Claude Code's default head_limit is 250
GREP_READS = 5  # files opened before giving up
READ_MAX_LINES = 2000  # the default page of a file read
READ_MAX_LINE_CHARS = 2000  # longer lines are cut by file reads
GREP_MAX_LINE_CHARS = 500  # and a grep line is a hint, not a page of minified data
GREP_ARM_VERSION = 2
STOPWORDS = set("""a an and are as at be but by can do does for from has have how i if in into is it its of on or that the
their them there these this to under use used using was were what when where which who why will with would you your
""".split())


def manifest() -> list[dict]:
    return tomllib.loads((ROOT / "repos.toml").read_text(encoding="utf-8"))["repo"]


def git(repo: str, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(REPOS / repo), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed in {repo}: {r.stderr.strip()[-300:]}")
    return r.stdout


def split_of(question_id: str) -> str:
    """Stable half: failures of `dev` guide the work, `holdout` is read only in the final report."""
    return "dev" if int(hashlib.sha256(question_id.encode()).hexdigest(), 16) % 2 == 0 else "holdout"


def excluded(entry: dict, path: str) -> bool:
    return any(fnmatch(path, pattern) for pattern in entry["exclude"])


def tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN + 1


def cmd_prepare(_: argparse.Namespace) -> None:
    from ragnar.evaluation import build_golden

    (QUESTIONS / "history").mkdir(parents=True, exist_ok=True)
    REPOS.mkdir(exist_ok=True)
    for entry in manifest():
        name = entry["name"]
        if not (REPOS / name).exists():
            subprocess.run(["git", "clone", "--quiet", "--filter=blob:none", entry["url"], str(REPOS / name)], check=True)
        git(name, "checkout", "--quiet", "--detach", entry["snapshot"])
        branch = f"origin/{entry['branch']}"
        golden = [q for q in build_golden(str(REPOS / name), entry["snapshot"], branch)]
        kept = []
        for q in golden:
            q["relevant"] = [p for p in q["relevant"] if not excluded(entry, p)]
            q["query"] = re.sub(r"\s*\(#\d+\)\s*$", "", q["query"].splitlines()[0]).strip()  # subject only, as a person would ask
            if q["relevant"] and len(q["query"]) >= 20:
                kept.append(q)
        path = QUESTIONS / "history" / f"{name}.jsonl"
        path.write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in kept), encoding="utf-8")
        print(f"{name}: snapshot {entry['snapshot']}, {len(kept)} history questions", flush=True)


def _settings(entry: dict, extra: list[str]):
    from ragnar.config import load_settings

    excludes = json.dumps(entry["exclude"])
    return load_settings(ROOT, overrides=[f"index.exclude={excludes}", *extra],
                         environ={k: v for k, v in os.environ.items() if not k.startswith("RAGNAR__")})


def cmd_index(args: argparse.Namespace) -> None:
    from ragnar.embedder import from_settings
    from ragnar.indexer import index
    from ragnar.migrate import migrate

    db = f"ragnar_{args.label}"
    dsn = ADMIN_DSN.format(db=db)
    if not args.keep:
        with psycopg.connect(ADMIN_DSN.format(db="postgres"), autocommit=True) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {db}")
            conn.execute(f"CREATE DATABASE {db}")
        os.environ["RAGNAR__EMBEDDING__DIM"] = os.environ.get("RAGNAR__EMBEDDING__DIM", "768")
        migrate(dsn)
    out = WORK / args.label
    out.mkdir(parents=True, exist_ok=True)
    results = []
    entries = [e for e in manifest() if not args.repo or e["name"] in args.repo]
    for entry in entries:
        settings = _settings(entry, [f"embedding.url={json.dumps(EMBEDDER)}", *args.set])
        r = index(str(REPOS / entry["name"]), entry["name"], dsn, from_settings(settings), settings,
                  ref=entry["snapshot"], full=args.full, scan=False)
        with psycopg.connect(dsn) as conn:
            symbols = conn.execute("SELECT count(*) FROM code_symbol WHERE repo = %s", (entry["name"],)).fetchone()[0]
        results.append({"repo": entry["name"], "mode": r.mode, "files": r.files, "chunks": r.chunks, "symbols": symbols,
                        "seconds": r.seconds, "skipped": getattr(r, "skipped", {}), "reused": getattr(r, "reused_vectors", 0)})
        print(json.dumps(results[-1]), flush=True)
    previous = {}
    target = out / "index.json"
    if args.repo and target.exists():
        previous = {r["repo"]: r for r in json.loads(target.read_text(encoding="utf-8"))["repos"]}
    merged = {**previous, **{r["repo"]: r for r in results}}
    with psycopg.connect(dsn) as conn:
        size = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
    target.write_text(json.dumps({"label": args.label, "database_bytes": size, "repos": list(merged.values())}, indent=1),
                      encoding="utf-8")


def load_questions(entry: dict) -> list[dict]:
    name = entry["name"]
    out = []
    for source in ("curated", "history"):
        path = QUESTIONS / source / f"{name}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                q = json.loads(line)
                q.update(repo=name, source=source, id=f"{name}/{q['id']}", category=q.get("category", "history"))
                q["split"] = split_of(q["id"])
                out.append(q)
    return out


def validate(entry: dict, questions: list[dict]) -> list[str]:
    """Curated answers are checked against the snapshot: a wrong path or evidence fails loudly."""
    problems = []
    for q in questions:
        if q["source"] != "curated":
            continue
        for path in q.get("relevant", []):
            text = git(entry["name"], "show", f"{entry['snapshot']}:{path}", check=False)
            if not text:
                problems.append(f"{q['id']}: {path} is not in the snapshot")
            elif excluded(entry, path):
                problems.append(f"{q['id']}: {path} is excluded from the index")
            elif q.get("evidence") and not any(
                    re.search(q["evidence"], git(entry["name"], "show", f"{entry['snapshot']}:{p}", check=False))
                    for p in q["relevant"]):
                problems.append(f"{q['id']}: evidence not found: {q['evidence']}")
                break
    return problems


def grep_terms(query: str) -> list[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query)
    return [w for w in dict.fromkeys(words) if w.lower() not in STOPWORDS][:10]


def grep_arm(entry: dict, q: dict) -> dict:
    """Without Ragnar: one `git grep -n` per term, files ranked by distinct terms, then the top files read in turn.

    The cost stops at the first read that shows the answer. That is the best case for grep:
    a real agent cannot always tell it found the answer and keeps reading.
    """
    t0 = time.perf_counter()
    name, snapshot = entry["name"], entry["snapshot"]
    score: dict[str, int] = {}
    output_tokens = 0
    for term in grep_terms(q.get("grep_query", q["query"])):
        out = git(name, "grep", "-n", "-I", "-i", "-w", "-e", term, snapshot, check=False)
        lines = [ln.split(":", 1)[1] for ln in out.splitlines() if ":" in ln]
        lines = [ln[:GREP_MAX_LINE_CHARS] for ln in lines if not excluded(entry, ln.split(":", 1)[0])]
        output_tokens += tokens("\n".join(lines[:GREP_MAX_LINES]))
        for path in {ln.split(":", 1)[0] for ln in lines}:
            score[path] = score.get(path, 0) + 1
    ranked = sorted(score, key=lambda p: (-score[p], p))
    relevant = set(q.get("relevant", []))
    cost, solved = output_tokens, False
    for path in ranked[:GREP_READS]:
        lines = git(name, "show", f"{snapshot}:{path}", check=False).splitlines()[:READ_MAX_LINES]
        text = "\n".join(line[:READ_MAX_LINE_CHARS] for line in lines)
        cost += tokens(text)
        if path in relevant and (not q.get("evidence") or re.search(q["evidence"], text)):
            solved = True
            break
    return {"ranked": ranked[:20], "solved": solved, "tokens": cost, "ms": (time.perf_counter() - t0) * 1000}


def ragnar_arm(pipeline, conn, entry: dict, q: dict) -> dict:
    from ragnar import search as ragnar_search
    from ragnar.mcp_server import render_hits
    from ragnar.search import find_symbol

    t0 = time.perf_counter()
    name = entry["name"]
    relevant = set(q.get("relevant", []))
    find_references = getattr(ragnar_search, "find_references", None)  # older trees run through here too
    if q.get("reference") and find_references:
        rows = find_references(conn, [name], q["reference"], 30)
        conn.rollback()
        text = "\n".join(f'{r["path"]}:{r["line"]} {r["symbol"]} {r["text"]}' for r in rows)
        ranked = list(dict.fromkeys(r["path"] for r in rows))
        return {"ranked": ranked, "solved": bool(relevant & set(ranked)), "tokens": tokens(text),
                "ms": (time.perf_counter() - t0) * 1000, "chunks": len(rows)}
    hits, _ = pipeline.search(conn, [name], q["query"])
    conn.rollback()
    text = render_hits(hits)
    cost = tokens(text)
    ranked = list(dict.fromkeys(h["path"] for h in hits))
    bodies = [h["content"] for h in hits if h["path"] in relevant and not h.get("compact")]
    solved = bool(bodies) and (not q.get("evidence") or any(re.search(q["evidence"], b) for b in bodies))
    if q.get("symbol"):
        rows = find_symbol(conn, [name], q["symbol"], 20)
        conn.rollback()
        cost = tokens("\n".join(f'{r["path"]}:{r["start_line"]} {r["symbol"]} {r["signature"]}' for r in rows))
        ranked = list(dict.fromkeys(r["path"] for r in rows))
        solved = any(r["path"] in relevant for r in rows)
    return {"ranked": ranked, "solved": solved, "tokens": cost, "ms": (time.perf_counter() - t0) * 1000, "chunks": len(hits)}


def scored(arm: dict, q: dict) -> dict:
    relevant = q.get("relevant", [])
    if not relevant:
        return {**arm, "rr": None, "recall5": None, "recall10": None}
    rank = next((i for i, p in enumerate(arm["ranked"], 1) if p in relevant), None)
    return {**arm, "rr": 1 / rank if rank else 0.0, "recall5": len(set(arm["ranked"][:5]) & set(relevant)) / len(relevant),
            "recall10": len(set(arm["ranked"][:10]) & set(relevant)) / len(relevant)}


def cmd_evaluate(args: argparse.Namespace) -> None:
    from ragnar.embedder import from_settings
    from ragnar.search import Pipeline

    dsn = ADMIN_DSN.format(db=f"ragnar_{args.db or args.label}")
    rows = []
    problems = []
    entries = [e for e in manifest() if not args.repo or e["name"] in args.repo]
    grep_cache_path = WORK / "grep_arm.json"
    grep_cache = json.loads(grep_cache_path.read_text(encoding="utf-8")) if grep_cache_path.exists() else {}
    with psycopg.connect(dsn) as conn:
        for entry in entries:
            questions = load_questions(entry)
            problems += validate(entry, questions)
            if args.split:
                questions = [q for q in questions if q["split"] == args.split]
            settings = _settings(entry, [f"embedding.url={json.dumps(EMBEDDER)}", *args.set])
            pipeline = Pipeline(settings, from_settings(settings))
            for q in questions:
                key = hashlib.sha256(json.dumps([GREP_ARM_VERSION, q["id"], q.get("grep_query", q["query"]),
                                                 q.get("evidence")]).encode()).hexdigest()
                if key not in grep_cache:
                    grep_cache[key] = grep_arm(entry, q)
                rows.append({k: q.get(k) for k in ("id", "repo", "source", "category", "split", "query", "relevant", "evidence")}
                            | {"language": entry["language"], "grep": scored(grep_cache[key], q),
                               "ragnar": scored(ragnar_arm(pipeline, conn, entry, q), q)})
            print(f"{entry['name']}: {len(questions)} questions", file=sys.stderr, flush=True)
    grep_cache_path.write_text(json.dumps(grep_cache), encoding="utf-8")
    out = WORK / args.label
    out.mkdir(parents=True, exist_ok=True)
    name = args.out or "eval"
    (out / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=0), encoding="utf-8")
    for p in problems:
        print("INVALID", p, file=sys.stderr)
    print_summary(rows)


def summarize(rows: list[dict], arm: str) -> dict:
    answerable = [r[arm] for r in rows if r[arm]["rr"] is not None]
    all_arm = [r[arm] for r in rows]
    if not answerable:
        return {"n": 0}
    return {"n": len(answerable), "mrr": round(mean(a["rr"] for a in answerable), 3),
            "recall5": round(mean(a["recall5"] for a in answerable), 3),
            "recall10": round(mean(a["recall10"] for a in answerable), 3),
            "solved": round(mean(a["solved"] for a in answerable), 3),
            "tokens": round(mean(a["tokens"] for a in all_arm)),
            "tokens_median": round(median(a["tokens"] for a in all_arm)),
            "ms": round(median(a["ms"] for a in all_arm))}


def print_summary(rows: list[dict]) -> None:
    groups: dict[str, list[dict]] = {"ALL": rows}
    for r in rows:
        groups.setdefault(f"source={r['source']}", []).append(r)
        groups.setdefault(f"lang={r['language']}", []).append(r)
        groups.setdefault(f"repo={r['repo']}", []).append(r)
        if r["source"] == "curated":
            groups.setdefault(f"cat={r['category']}", []).append(r)
    for name, group in sorted(groups.items()):
        for arm in ("grep", "ragnar"):
            print(f"{name:24} {arm:7} {summarize(group, arm)}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare").set_defaults(fn=cmd_prepare)
    s = sub.add_parser("index")
    s.add_argument("--label", required=True)
    s.add_argument("--repo", action="append", default=[])
    s.add_argument("--keep", action="store_true", help="keeps the database instead of starting empty")
    s.add_argument("--full", action="store_true")
    s.add_argument("--set", action="append", default=[])
    s.set_defaults(fn=cmd_index)
    s = sub.add_parser("evaluate")
    s.add_argument("--label", required=True)
    s.add_argument("--db", help="database label, when it differs from --label")
    s.add_argument("--repo", action="append", default=[])
    s.add_argument("--split", choices=("dev", "holdout"))
    s.add_argument("--out", help="result file name")
    s.add_argument("--set", action="append", default=[])
    s.set_defaults(fn=cmd_evaluate)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

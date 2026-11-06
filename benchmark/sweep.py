"""Compares retrieval settings on the `dev` questions only. `holdout` never enters here.

    uv run python benchmark/sweep.py --db lex --base 'retrieval.mode="lexical"' --base 'embedding.provider="fake"' \
        --variant 'no-file=retrieval.file_weight=0' --variant 'rank2=retrieval.rank_normalization=2'

A variant is a name and comma-separated changes: section.key=value settings, or NAME:value for a ragnar.search constant.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from statistics import mean

import psycopg

sys.path.insert(0, str(Path(__file__).parent))
from bench import ADMIN_DSN, EMBEDDER, _settings, load_questions, manifest, ragnar_arm, scored  # noqa: E402

from ragnar import search  # noqa: E402
from ragnar.embedder import from_settings  # noqa: E402


def score(entries: list[dict], dsn: str, overrides: list[str]) -> dict:
    groups: dict[str, list[dict]] = {}
    with psycopg.connect(dsn) as conn:
        for entry in entries:
            settings = _settings(entry, [f"embedding.url={json.dumps(EMBEDDER)}", *overrides])
            pipeline = search.Pipeline(settings, from_settings(settings))
            for q in load_questions(entry):
                if q["split"] != "dev" or not q.get("relevant"):
                    continue
                row = scored(ragnar_arm(pipeline, conn, entry, q), q)
                for group in ("all", q["source"], entry["language"], *([q["category"]] if q["source"] == "curated" else [])):
                    groups.setdefault(group, []).append(row)
    return {g: (round(mean(r["rr"] for r in rows), 3), round(mean(r["recall10"] for r in rows), 3),
                round(mean(r["solved"] for r in rows), 3), round(mean(r["tokens"] for r in rows)),
                round(mean(r["hit10"] for r in rows), 3))
            for g, rows in sorted(groups.items())}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True)
    p.add_argument("--repo", action="append", default=[])
    p.add_argument("--base", action="append", default=[])
    p.add_argument("--variant", action="append", default=[])
    args = p.parse_args()
    dsn = ADMIN_DSN.format(db=f"ragnar_{args.db}")
    entries = [e for e in manifest() if not args.repo or e["name"] in args.repo]
    print("name: group (mrr, recall10, solved, tokens, hit10)")
    for item in ["base=", *args.variant]:
        name, _, changes = item.partition("=")
        overrides, constants = [], {}
        for change in filter(None, re.split(r",(?=[A-Za-z0-9_.]+[:=])", changes)):
            if re.match(r"[A-Z][A-Z0-9_]*:", change):
                key, _, value = change.partition(":")
                constants[key] = json.loads(value)
            else:
                overrides.append(change)
        saved = {k: getattr(search, k) for k in constants}
        for key, value in constants.items():
            setattr(search, key, value)
        print(name, score(entries, dsn, [*args.base, *overrides]), flush=True)
        for key, value in saved.items():
            setattr(search, key, value)


if __name__ == "__main__":
    main()

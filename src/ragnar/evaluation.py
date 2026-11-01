"""Measures whether the index helps: retrieval with and without Ragnar, on real questions.

Each commit after a snapshot becomes a question: its message is the query, and the files it
changed are the expected answer. The snapshot predates those commits, so the answer cannot
leak into the index. Comparisons are paired and report a 95% bootstrap confidence interval.
"""

from __future__ import annotations

import json
import random
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

import psycopg

from .config import Settings, load_settings
from .embedder import from_settings
from .languages import BY_EXTENSION
from .llm import chat_from, reranker_from
from .search import Pipeline, estimate_tokens

SKIP_COMMIT = re.compile(r"^(merge|bump|chore|release|revert|format|wip)\b", re.I)
STOPWORDS = {"the", "and", "for", "with", "fix", "add", "use", "feat", "para", "com", "que", "não", "uma", "dos", "das"}
MIN_QUESTIONS = 30
GREP_FILES_READ = 5  # files an agent would open from a grep result, for the cost estimate


def _git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True).stdout


def build_golden(repo: str, snapshot: str, branch: str = "main") -> list[dict]:
    """Questions from the commits between the snapshot and the branch."""
    existing = set(_git(repo, "ls-tree", "-r", "--name-only", snapshot).splitlines())
    golden = []
    for sha in _git(repo, "rev-list", "--no-merges", f"{snapshot}..{branch}").split():
        message = _git(repo, "show", "-s", "--format=%s%n%n%b", sha).strip()
        files = [f for f in _git(repo, "show", "--name-only", "--format=", sha).splitlines()
                 if Path(f).suffix.lower() in BY_EXTENSION and f in existing]
        if not SKIP_COMMIT.match(message) and len(message) >= 25 and 1 <= len(files) <= 5:
            golden.append({"id": sha[:10], "query": message, "relevant": files})
    return golden


def recall_at(ranked: list[str], relevant: list[str], k: int) -> float:
    return len(set(ranked[:k]) & set(relevant)) / len(relevant)


def mrr(ranked: list[str], relevant: list[str]) -> float:
    return next((1 / i for i, p in enumerate(ranked, 1) if p in relevant), 0.0)


def paired_ci(a: list[float], b: list[float], n: int = 5000, seed: int = 7) -> tuple[float, float, float]:
    """Mean of b - a and its 95% bootstrap interval."""
    rnd, d = random.Random(seed), [y - x for x, y in zip(a, b)]
    means = sorted(mean(rnd.choices(d, k=len(d))) for _ in range(n))
    return mean(d), means[int(0.025 * n)], means[int(0.975 * n)]


def grep_baseline(repo: str, snapshot: str, query: str, k: int = 20) -> list[str]:
    """Without Ragnar: the snapshot files that contain the most distinct terms of the question."""
    terms = [t for t in dict.fromkeys(re.findall(r"[A-Za-z_]\w{2,}", query)) if t.lower() not in STOPWORDS][:10]
    globs = [f"*{ext}" for ext in BY_EXTENSION]
    score: Counter = Counter()
    for term in terms:
        r = subprocess.run(["git", "-C", repo, "grep", "-c", "-i", "-w", term, snapshot, "--", *globs],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        for line in r.stdout.splitlines():
            score[line.split(":", 2)[1]] += 1  # format: <sha>:<path>:<n>
    return [p for p, _ in score.most_common(k)]


def _grep_tokens(repo: str, snapshot: str, files: list[str]) -> int:
    """Estimated tokens to read the top files in full: the cost grep leaves to the agent."""
    sizes = []
    for path in files[:GREP_FILES_READ]:
        r = subprocess.run(["git", "-C", repo, "cat-file", "-s", f"{snapshot}:{path}"], capture_output=True, text=True)
        sizes.append(int(r.stdout.strip() or 0) if r.returncode == 0 else 0)
    return sum(sizes) // 4


@dataclass
class Scores:
    recall: list[float]
    mrr: list[float]
    tokens: list[int]

    def summary(self) -> dict[str, float]:
        return {"recall_at_10": round(mean(self.recall), 3), "mrr": round(mean(self.mrr), 3),
                "avg_context_tokens": round(mean(self.tokens))}


def _score_pipeline(conn: psycopg.Connection, settings: Settings, name: str, golden: list[dict]) -> Scores:
    pipeline = Pipeline(settings, from_settings(settings), chat_from(settings), reranker_from(settings))
    scores = Scores([], [], [])
    for q in golden:
        hits, _ = pipeline.search(conn, [name], q["query"], k=10)
        conn.rollback()
        ranked = list(dict.fromkeys(h["path"] for h in hits))
        scores.recall.append(recall_at(ranked, q["relevant"], 10))
        scores.mrr.append(mrr(ranked, q["relevant"]))
        scores.tokens.append(sum(estimate_tokens(h["content"]) for h in hits))
    return scores


def _score_grep(repo: str, snapshot: str, golden: list[dict]) -> Scores:
    scores = Scores([], [], [])
    for q in golden:
        ranked = grep_baseline(repo, snapshot, q["query"])
        scores.recall.append(recall_at(ranked, q["relevant"], 10))
        scores.mrr.append(mrr(ranked, q["relevant"]))
        scores.tokens.append(_grep_tokens(repo, snapshot, ranked))
    return scores


def _gain(base: Scores, other: Scores) -> dict[str, float]:
    d, lo, hi = paired_ci(base.recall, other.recall)
    return {"recall_gain": round(d, 3), "ci95_low": round(lo, 3), "ci95_high": round(hi, 3)}


def compare(dsn: str, repo: str, snapshot: str, index_name: str, golden: list[dict],
            variants: dict[str, list[str]] | None = None, overrides: list[str] | None = None) -> dict:
    """Scores grep, the configured pipeline and each variant on the same questions."""
    settings = load_settings(overrides=overrides)
    grep = _score_grep(repo, snapshot, golden)
    systems: dict[str, Scores] = {}
    with psycopg.connect(dsn) as conn:
        systems["configured"] = _score_pipeline(conn, settings, index_name, golden)
        for name, changes in (variants or {}).items():
            variant = load_settings(overrides=[*(overrides or []), *changes])
            systems[name] = _score_pipeline(conn, variant, index_name, golden)
    return {
        "questions": len(golden),
        "indicative_only": len(golden) < MIN_QUESTIONS,
        "grep": grep.summary(),
        "systems": {n: {**s.summary(), "vs_grep": _gain(grep, s),
                        **({"vs_configured": _gain(systems["configured"], s)} if n != "configured" else {})}
                    for n, s in systems.items()},
    }


def format_report(report: dict) -> str:
    lines = [f'{report["questions"]} questions',
             f'{"system":16} {"Recall@10":>9} {"MRR":>6} {"tokens":>8}',
             "{:16} {recall_at_10:9.3f} {mrr:6.3f} {avg_context_tokens:8}".format("grep", **report["grep"])]
    for name, s in report["systems"].items():
        lines.append(f'{name:16} {s["recall_at_10"]:9.3f} {s["mrr"]:6.3f} {s["avg_context_tokens"]:8}')
    for name, s in report["systems"].items():
        for base in ("vs_grep", "vs_configured"):
            if base in s:
                g = s[base]
                lines.append(f'{name} {base}: recall gain {g["recall_gain"]:+.3f}, '
                             f'95% CI [{g["ci95_low"]:+.3f}, {g["ci95_high"]:+.3f}]')
    lines.append("grep tokens estimate reading its top files in full; the others are the context actually returned.")
    if report["indicative_only"]:
        lines.append(f"Warning: fewer than {MIN_QUESTIONS} questions; results are indicative only.")
    return "\n".join(lines)


def read_golden(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]

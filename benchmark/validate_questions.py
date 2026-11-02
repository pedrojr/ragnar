"""Checks every curated answer against the pinned snapshots, with no database.

Run: uv run python benchmark/validate_questions.py
"""

from __future__ import annotations

import sys
from collections import Counter

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from bench import load_questions, manifest, validate  # noqa: E402


def main() -> None:
    problems, categories, splits, history = [], Counter(), Counter(), 0
    for entry in manifest():
        questions = load_questions(entry)
        problems += validate(entry, questions)
        for q in questions:
            splits[(q["source"], q["split"])] += 1
            history += q["source"] == "history"
            if q["source"] == "curated":
                categories[q["category"]] += 1
    print(f"curated by category: {dict(categories)}")
    print(f"history questions: {history}")
    print(f"splits: { {f'{a}/{b}': n for (a, b), n in sorted(splits.items())} }")
    print("\n".join(problems) or "every curated answer matches its snapshot")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()

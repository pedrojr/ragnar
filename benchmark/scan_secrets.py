"""Runs the indexer's secret scan on each snapshot, to see how often it would stop a run. Needs gitleaks.

Run: uv run python benchmark/scan_secrets.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bench import REPOS, ROOT, WORK, _settings, manifest  # noqa: E402

from ragnar.chunker import Chunker, normalize_source  # noqa: E402
from ragnar.indexer import list_files, read_blobs, secret_scan  # noqa: E402


def main() -> None:
    report = {}
    for entry in manifest():
        settings = _settings(entry, [])
        chunker = Chunker.from_settings(settings)
        repo = REPOS / entry["name"]
        paths = [p for p in list_files(repo, entry["snapshot"]) if chunker.eligible(p)]
        blobs = read_blobs(repo, entry["snapshot"], paths)
        normalized = {p: normalize_source(b) for p, b in blobs.items()}
        found = secret_scan({p: b for p, b in normalized.items() if b is not None})
        report[entry["name"]] = {"files": len(blobs), "with_findings": len(found), "findings": found}
        print(f"{entry['name']}: {len(blobs)} files, {len(found)} with findings {sorted(found)[:6]}", flush=True)
    WORK.mkdir(exist_ok=True)
    (WORK / "secrets.json").write_text(json.dumps(report, indent=1), encoding="utf-8")


if __name__ == "__main__":
    print(f"root: {ROOT}")
    main()

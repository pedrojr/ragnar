"""Starts a Ragnar MCP server on a benchmark database, for agent runs.

Run: RAGNAR_BENCH_TOKEN=<any string> uv run python benchmark/serve.py --db final
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).parent))
from bench import ADMIN_DSN, EMBEDDER, WORK  # noqa: E402

from ragnar.acl import hash_token  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True)
    p.add_argument("--port", type=int, default=18080)
    args = p.parse_args()
    acl = WORK / "acl.json"
    acl.write_text(json.dumps({"clients": [{"name": "benchmark", "token_sha256": hash_token(os.environ["RAGNAR_BENCH_TOKEN"]),
                                            "repos": ["*"]}]}), encoding="utf-8")
    os.environ.update(RAGNAR_DSN_READ=ADMIN_DSN.format(db=f"ragnar_{args.db}"), RAGNAR_ACL_FILE=str(acl),
                      RAGNAR__EMBEDDING__URL=EMBEDDER)
    uvicorn.run("ragnar.mcp_server:build_app", factory=True, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

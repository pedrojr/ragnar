"""Gera perguntas de recuperação a partir dos commits depois do snapshot (seção 8.1 do guia).

Uso: uv run python eval/build_golden.py <repo> <sha-do-snapshot> > eval/golden.jsonl
Pergunta = mensagem do commit. Resposta esperada = arquivos de código que ele alterou e já existiam no snapshot.
"""

import json
import re
import subprocess
import sys
from pathlib import PurePosixPath

from raglib.chunker import LANGS

SKIP = re.compile(r"^(merge|bump|chore|release|revert|format|wip)\b", re.I)


def git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout


repo, snap = sys.argv[1], sys.argv[2]
existing = set(git(repo, "ls-tree", "-r", "--name-only", snap).splitlines())
count = 0
for sha in git(repo, "rev-list", "--no-merges", f"{snap}..main").split():
    msg = git(repo, "show", "-s", "--format=%s%n%n%b", sha).strip()
    files = [f for f in git(repo, "show", "--name-only", "--format=", sha).splitlines()
             if PurePosixPath(f).suffix in LANGS and f in existing]
    if SKIP.match(msg) or len(msg) < 25 or not 1 <= len(files) <= 5:
        continue
    print(json.dumps({"id": sha[:10], "query": msg, "relevant": files}, ensure_ascii=False))
    count += 1
print(f"{count} perguntas", file=sys.stderr)

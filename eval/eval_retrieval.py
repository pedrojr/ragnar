"""Compara recuperação sem RAG (git grep) e com RAG (busca híbrida): Recall@10, MRR e IC95% pareado.

Uso: uv run python eval/eval_retrieval.py <repo> <sha-do-snapshot> <nome-do-snap-no-indice> eval/golden.jsonl
Lê o banco com RAG_DSN_READ e o embedder com EMBED_URL. Adaptado da seção 8.1 do guia.
"""

import json
import random
import re
import subprocess
import sys
from collections import Counter
from statistics import mean

import psycopg

from raglib.config import read_dsn
from raglib.embedder import from_env
from raglib.search import search

REPO_DIR, SNAP, INDEX_NAME, GOLDEN = sys.argv[1:5]
STOP = {"the", "and", "for", "with", "fix", "add", "use", "para", "com", "que", "não", "uma", "dos", "das", "feat"}


def grep_baseline(query: str, k: int = 20) -> list[str]:
    """Sem RAG: arquivos do snapshot com mais termos distintos da pergunta."""
    terms = [t for t in dict.fromkeys(re.findall(r"[A-Za-z_]\w{2,}", query)) if t.lower() not in STOP][:10]
    score: Counter = Counter()
    for t in terms:
        r = subprocess.run(["git", "-C", REPO_DIR, "grep", "-c", "-i", "-w", t, SNAP, "--",
                            "*.java", "*.cs", "*.go", "*.py"], capture_output=True, text=True)
        for line in r.stdout.splitlines():
            score[line.split(":", 2)[1]] += 1  # formato: <sha>:<path>:<n>
    return [p for p, _ in score.most_common(k)]


def rag(query: str, k: int = 20) -> list[str]:
    hits, _ = search(CONN, EMB, [INDEX_NAME], query, k=50)
    CONN.rollback()
    return list(dict.fromkeys(h["path"] for h in hits))[:k]


def recall_at(ranked, relevant, k):
    return len(set(ranked[:k]) & set(relevant)) / len(relevant)


def mrr(ranked, relevant):
    return next((1 / i for i, p in enumerate(ranked, 1) if p in relevant), 0.0)


def paired_ci(a, b, n=5000, seed=7):
    rnd, d = random.Random(seed), [y - x for x, y in zip(a, b)]
    means = sorted(mean(rnd.choices(d, k=len(d))) for _ in range(n))
    return mean(d), means[int(0.025 * n)], means[int(0.975 * n)]


EMB = from_env()
CONN = psycopg.connect(read_dsn())
golden = [json.loads(line) for line in open(GOLDEN, encoding="utf-8") if line.strip()]
if not golden:
    sys.exit("golden set vazio: sem commits elegíveis depois do snapshot")
systems = {"A_grep": grep_baseline, "C_hibrido": rag}
results = {name: {"r10": [], "mrr": []} for name in systems}
for q in golden:
    for name, fn in systems.items():
        ranked = fn(q["query"])
        results[name]["r10"].append(recall_at(ranked, q["relevant"], 10))
        results[name]["mrr"].append(mrr(ranked, q["relevant"]))

print(f"{len(golden)} perguntas")
for name, r in results.items():
    print(f"{name:12} Recall@10={mean(r['r10']):.3f}  MRR={mean(r['mrr']):.3f}")
d, lo, hi = paired_ci(results["A_grep"]["r10"], results["C_hibrido"]["r10"])
print(f"Ganho Recall@10 (híbrido - grep): {d:+.3f}  IC95% [{lo:+.3f}, {hi:+.3f}]")
if len(golden) < 30:
    print("Aviso: menos de 30 perguntas; resultado indicativo.")

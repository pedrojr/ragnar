#!/usr/bin/env bash
# Verificação ponta a ponta: MCP (token, ACL, ferramentas) e barreiras de escrita no banco.
#   ./scripts/smoke.sh            # Compose
#   ./scripts/smoke.sh --target k8s
set -euo pipefail
cd "$(dirname "$0")/.."
target=compose; [ "${1:-}" = "--target" ] && target="$2"
if [ "$target" = k8s ]; then
  kubectl -n rag delete job rag-smoke --ignore-not-found >/dev/null
  kubectl apply -f k8s/jobs/smoke.yaml >/dev/null
  for _ in $(seq 1 90); do
    s=$(kubectl -n rag get job rag-smoke -o jsonpath="{.status.succeeded}{.status.failed}")
    [ -n "$s" ] && break; sleep 2
  done
  kubectl -n rag logs job/rag-smoke
  kubectl -n rag get job rag-smoke -o jsonpath='{.status.succeeded}' | grep -q 1
else
  docker compose run --rm tools smoke --url http://rag-mcp:8080/mcp --replica-host db-replica
fi

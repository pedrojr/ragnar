#!/usr/bin/env bash
# Indexes one repository from the host, without waiting for the worker.
# First run: initial build. After that: only what changed since the last indexed commit.
#
#   ./scripts/index.sh /path/to/myapp myapp                  # Compose
#   ./scripts/index.sh /path/to/myapp myapp --target k8s     # Kubernetes (opens a port-forward to the primary)
#   ./scripts/index.sh /path/to/myapp myapp --full           # reindexes everything
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "run first: uv run --project .. ragnar init-env" >&2; exit 1; }
set -a; . ./.env; set +a

target=compose args=()
while [ $# -gt 0 ]; do
  case "$1" in
    --target) target="$2"; shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done

wait_port() { for _ in $(seq 1 60); do (echo > "/dev/tcp/127.0.0.1/$1") 2>/dev/null && return 0; sleep 0.5; done; echo "port $1 did not open" >&2; exit 1; }

if [ "$target" = k8s ]; then
  # the primary (ragnar-db-rw) stays inside the cluster: the tunnel lasts for this run
  kubectl -n ragnar port-forward svc/ragnar-db-rw 15432:5432 >/dev/null &
  pf_db=$!
  kubectl -n ragnar port-forward svc/ragnar-embedder 8001:8000 >/dev/null &
  pf_emb=$!
  trap 'kill $pf_db $pf_emb 2>/dev/null' EXIT
  wait_port 15432; wait_port 8001
fi

export RAGNAR_DSN_WRITE="postgresql://ragnar_writer:${RAGNAR_WRITER_PASSWORD}@127.0.0.1:15432/ragnar"
export RAGNAR__EMBEDDING__URL="http://127.0.0.1:8001/v1"
uv run --project .. ragnar index "${args[@]}"

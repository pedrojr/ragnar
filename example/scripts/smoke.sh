#!/usr/bin/env bash
# End-to-end check: MCP (token, ACL, tools) and the write barriers on the database.
#   ./scripts/smoke.sh myapp                # Compose
#   ./scripts/smoke.sh myapp --target k8s
set -euo pipefail
cd "$(dirname "$0")/.."
repo="${1:?usage: smoke.sh <repository> [--target k8s]}"
target=compose; [ "${2:-}" = "--target" ] && target="$3"
if [ "$target" = k8s ]; then
  kubectl -n ragnar delete job ragnar-smoke --ignore-not-found >/dev/null
  sed "s/__REPO__/$repo/" k8s/jobs/smoke.yaml | kubectl apply -f - >/dev/null
  for _ in $(seq 1 90); do
    s=$(kubectl -n ragnar get job ragnar-smoke -o jsonpath="{.status.succeeded}{.status.failed}")
    [ -n "$s" ] && break; sleep 2
  done
  kubectl -n ragnar logs job/ragnar-smoke
  kubectl -n ragnar get job ragnar-smoke -o jsonpath='{.status.succeeded}' | grep -q 1
else
  docker compose run --rm tools smoke --repo "$repo" --url http://ragnar-mcp:8080/mcp --replica-host db-replica
fi

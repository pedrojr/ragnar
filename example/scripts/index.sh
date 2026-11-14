#!/usr/bin/env bash
# Indexes one repository now, without waiting for the worker, with the settings of server.toml.
# First run: initial build. After that: only what changed since the last indexed commit.
#
#   ./scripts/index.sh /path/to/myapp myapp                  # Compose: runs on this machine
#   ./scripts/index.sh /path/to/myapp myapp --target k8s     # Kubernetes: runs in a temporary pod
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
[ ${#args[@]} -ge 2 ] || { echo "usage: index.sh <path-or-url> <name> [ragnar index options] [--target k8s]" >&2; exit 2; }

if [ "$target" = k8s ]; then
  # Inside the cluster, through the Services: kubectl port-forward exits when Postgres closes a forwarded
  # connection, and a pod reaches every embedder replica. A local clone travels as a git bundle: commits only.
  pod="ragnar-index-$(date +%s)"
  sed "s/^  name: ragnar-index$/  name: $pod/" k8s/jobs/index-pod.yaml | kubectl apply -f - >/dev/null
  trap 'kubectl -n ragnar delete pod "$pod" --ignore-not-found --wait=false >/dev/null' EXIT
  kubectl -n ragnar wait --for=condition=Ready "pod/$pod" --timeout=180s >/dev/null
  if [ -d "${args[0]}" ]; then
    git -C "${args[0]}" bundle create -q - --all \
      | kubectl -n ragnar exec -i "$pod" -- sh -c 'cat > /work/repo.bundle && git -c init.defaultBranch=main clone -q --mirror /work/repo.bundle /work/repo.git'
    args[0]=/work/repo.git
  fi
  MSYS_NO_PATHCONV=1 kubectl -n ragnar exec "$pod" -- ragnar index "${args[@]}"   # Git Bash would rewrite /work paths
  exit
fi

export RAGNAR_CONFIG=server.toml   # the same server settings as the worker, so its next pass agrees with this run
export RAGNAR_DSN_WRITE="postgresql://ragnar_writer:${RAGNAR_WRITER_PASSWORD}@127.0.0.1:15432/ragnar"
# the bundled embedder through its host port, unless .env points at a hosted endpoint
export RAGNAR__EMBEDDING__URL="${RAGNAR__EMBEDDING__URL:-http://127.0.0.1:8001/v1}"
uv run --project .. ragnar index "${args[@]}"

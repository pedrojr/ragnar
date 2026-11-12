#!/usr/bin/env bash
# Brings the example up on Docker Desktop Kubernetes with one command. Idempotent: safe to run again.
# Requires Docker Desktop with Kubernetes on, kubectl and uv. No Helm.
set -euo pipefail
cd "$(dirname "$0")/.."

CNPG_VERSION=1.30.1
KEDA_VERSION=2.21.0
METRICS_SERVER_VERSION=0.9.0
CONTEXT="${K8S_CONTEXT:-docker-desktop}"

step() { printf '\n== %s\n' "$*"; }
retry() { for i in $(seq 1 30); do "$@" && return 0; sleep 5; done; return 1; }

[ "$(kubectl config current-context)" = "$CONTEXT" ] || { echo "current context is not $CONTEXT (use K8S_CONTEXT=...)" >&2; exit 1; }
if docker compose ps -q ragnar-mcp 2>/dev/null | grep -q .; then
  echo "Compose is running and holds port 8080: run 'docker compose down' first" >&2; exit 1
fi
[ -f .env ] || uv run --project .. ragnar init-env
set -a; . ./.env; set +a

step "operators: metrics-server $METRICS_SERVER_VERSION, CloudNativePG $CNPG_VERSION, KEDA $KEDA_VERSION"
kubectl apply -f "https://github.com/kubernetes-sigs/metrics-server/releases/download/v${METRICS_SERVER_VERSION}/components.yaml" >/dev/null
# the Docker Desktop kubelet uses a self-signed certificate
kubectl -n kube-system get deploy metrics-server -o jsonpath='{.spec.template.spec.containers[0].args}' | grep -q kubelet-insecure-tls \
  || kubectl -n kube-system patch deploy metrics-server --type=json \
       -p '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]' >/dev/null
kubectl apply --server-side --force-conflicts -f \
  "https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-${CNPG_VERSION%.*}/releases/cnpg-${CNPG_VERSION}.yaml" >/dev/null
kubectl apply --server-side --force-conflicts -f \
  "https://github.com/kedacore/keda/releases/download/v${KEDA_VERSION}/keda-${KEDA_VERSION}.yaml" >/dev/null
kubectl -n cnpg-system rollout status deploy/cnpg-controller-manager --timeout=300s
kubectl -n keda rollout status deploy/keda-operator --timeout=300s
kubectl -n keda rollout status deploy/keda-metrics-apiserver --timeout=300s
kubectl -n keda rollout status deploy/keda-admission --timeout=300s
kubectl -n kube-system rollout status deploy/metrics-server --timeout=300s

step "images (Docker Desktop Kubernetes sees local images)"
docker build -q --target app -t ragnar-app:0.1.0 -f docker/Dockerfile .. >/dev/null
docker build -q --target embedder -t ragnar-embedder:0.1.0 -f docker/Dockerfile .. >/dev/null

step "namespace, secrets (from .env), repos.toml and server.toml"
kubectl apply -f k8s/base/namespace.yaml >/dev/null
apply_secret() { kubectl -n ragnar create secret "$@" --dry-run=client -o yaml | kubectl apply -f - >/dev/null; }
apply_secret generic ragnar-writer --type=kubernetes.io/basic-auth \
  --from-literal=username=ragnar_writer --from-literal=password="$RAGNAR_WRITER_PASSWORD"
apply_secret generic ragnar-reader --type=kubernetes.io/basic-auth \
  --from-literal=username=ragnar_reader --from-literal=password="$RAGNAR_READER_PASSWORD"
apply_secret generic ragnar-db-init-sql --from-literal=init.sql="CREATE ROLE ragnar_reader LOGIN PASSWORD '${RAGNAR_READER_PASSWORD}';
ALTER ROLE ragnar_reader SET default_transaction_read_only = on;"
apply_secret generic ragnar-acl --from-file=acl.json=acl.json
apply_secret generic ragnar-git --from-literal=RAGNAR_GIT_TOKEN="${RAGNAR_GIT_TOKEN:-}"
model_keys=()   # hosted chat model, reranker and embedder; empty when unused
for name in RAGNAR_LLM_API_KEY RAGNAR_RERANK_API_KEY RAGNAR_EMBEDDING_API_KEY; do
  model_keys+=("--from-literal=$name=${!name:-}")
done
apply_secret generic ragnar-model-keys "${model_keys[@]}"
apply_config() { kubectl -n ragnar create configmap "$@" --dry-run=client -o yaml | kubectl apply -f - >/dev/null; }
apply_config ragnar-repos --from-file=repos.toml=repos.toml
apply_config ragnar-server --from-file=server.toml=server.toml
apply_secret generic ragnar-mcp-tokens \
  --from-literal=RAGNAR_MCP_TOKEN="$RAGNAR_MCP_TOKEN" --from-literal=RAGNAR_MCP_TOKEN_RESTRICTED="$RAGNAR_MCP_TOKEN_RESTRICTED"

step "example resources (kubectl apply -k k8s/base)"
retry kubectl apply -k k8s/base >/dev/null   # operator webhooks may take a few seconds
kubectl -n ragnar wait --for=condition=Ready cluster/ragnar-db --timeout=600s

step "migrations on the primary"
kubectl -n ragnar delete job ragnar-migrate --ignore-not-found >/dev/null
kubectl apply -f k8s/jobs/migrate.yaml >/dev/null
kubectl -n ragnar wait --for=condition=complete job/ragnar-migrate --timeout=300s >/dev/null
kubectl -n ragnar logs job/ragnar-migrate

step "waiting for the embedder and the MCP server"
kubectl -n ragnar rollout status deploy/ragnar-embedder --timeout=600s
kubectl -n ragnar rollout restart deploy/ragnar-mcp >/dev/null   # reloads the ACL and server.toml if they changed
kubectl -n ragnar rollout status deploy/ragnar-mcp --timeout=300s

cat <<MSG

Ready. MCP at http://127.0.0.1:8080/mcp (Docker Desktop LoadBalancer Service).
  Index now:    ./scripts/index.sh /path/to/myapp myapp --target k8s
  Check:        ./scripts/smoke.sh myapp --target k8s
  Load:         kubectl apply -f k8s/jobs/load.yaml
  Watch:        kubectl -n ragnar get hpa,scaledobject,cluster -w
MSG

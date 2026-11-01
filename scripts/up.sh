#!/usr/bin/env bash
# Sobe o exemplo no Kubernetes do Docker Desktop com um comando. Idempotente: pode rodar de novo.
# Pré-requisitos: Docker Desktop com Kubernetes ligado, kubectl, uv. Sem Helm.
set -euo pipefail
cd "$(dirname "$0")/.."

CNPG_VERSION=1.30.1
KEDA_VERSION=2.21.0
METRICS_SERVER_VERSION=0.9.0
CONTEXT="${K8S_CONTEXT:-docker-desktop}"

step() { printf '\n== %s\n' "$*"; }
retry() { for i in $(seq 1 30); do "$@" && return 0; sleep 5; done; return 1; }

[ "$(kubectl config current-context)" = "$CONTEXT" ] || { echo "contexto atual não é $CONTEXT (use K8S_CONTEXT=...)" >&2; exit 1; }
if docker compose ps -q rag-mcp 2>/dev/null | grep -q .; then
  echo "o Compose está no ar e ocupa a porta 8080: rode 'docker compose down' antes" >&2; exit 1
fi
[ -f .env ] || uv run rag init-env
set -a; . ./.env; set +a

step "operadores: metrics-server $METRICS_SERVER_VERSION, CloudNativePG $CNPG_VERSION, KEDA $KEDA_VERSION"
kubectl apply -f "https://github.com/kubernetes-sigs/metrics-server/releases/download/v${METRICS_SERVER_VERSION}/components.yaml" >/dev/null
# kubelet do Docker Desktop usa certificado autoassinado
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

step "imagens (o Kubernetes do Docker Desktop enxerga as imagens locais)"
docker build -q --target app -t rag-app:0.1.0 -f docker/Dockerfile . >/dev/null
docker build -q --target embedder -t rag-embedder:0.1.0 -f docker/Dockerfile . >/dev/null

step "namespace e segredos (a partir do .env)"
kubectl apply -f k8s/base/namespace.yaml >/dev/null
apply_secret() { kubectl -n rag create secret "$@" --dry-run=client -o yaml | kubectl apply -f - >/dev/null; }
apply_secret generic rag-writer --type=kubernetes.io/basic-auth \
  --from-literal=username=rag_writer --from-literal=password="$RAG_WRITER_PASSWORD"
apply_secret generic rag-reader --type=kubernetes.io/basic-auth \
  --from-literal=username=rag_reader --from-literal=password="$RAG_READER_PASSWORD"
apply_secret generic rag-db-init-sql --from-literal=init.sql="CREATE ROLE rag_reader LOGIN PASSWORD '${RAG_READER_PASSWORD}';
ALTER ROLE rag_reader SET default_transaction_read_only = on;"
apply_secret generic rag-acl --from-file=acl.json=acl.json
apply_secret generic rag-mcp-tokens \
  --from-literal=RAG_MCP_TOKEN="$RAG_MCP_TOKEN" --from-literal=RAG_MCP_TOKEN_RESTRICTED="$RAG_MCP_TOKEN_RESTRICTED"

step "recursos do exemplo (kubectl apply -k k8s/base)"
retry kubectl apply -k k8s/base >/dev/null   # webhooks dos operadores podem levar alguns segundos
kubectl -n rag wait --for=condition=Ready cluster/rag-db --timeout=600s

step "migrações no primário"
kubectl -n rag delete job rag-migrate --ignore-not-found >/dev/null
kubectl apply -f k8s/jobs/migrate.yaml >/dev/null
kubectl -n rag wait --for=condition=complete job/rag-migrate --timeout=300s >/dev/null
kubectl -n rag logs job/rag-migrate

step "aguardando embedder e MCP"
kubectl -n rag rollout status deploy/rag-embedder --timeout=600s
kubectl -n rag rollout restart deploy/rag-mcp >/dev/null   # relê a ACL se ela mudou
kubectl -n rag rollout status deploy/rag-mcp --timeout=300s

cat <<MSG

Pronto. MCP em http://127.0.0.1:8080/mcp (Service LoadBalancer do Docker Desktop).
  Indexar:      ./scripts/index.sh /path/to/repo myapp --target k8s
  Verificar:    ./scripts/smoke.sh --target k8s
  Carga:        kubectl apply -f k8s/jobs/load.yaml
  Acompanhar:   kubectl -n rag get hpa,scaledobject,cluster -w
MSG

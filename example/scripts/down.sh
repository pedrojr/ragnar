#!/usr/bin/env bash
# Removes the example from Kubernetes. With --all, also removes the operators (CNPG, KEDA, metrics-server).
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/versions.sh
kubectl delete -k k8s/base --ignore-not-found --wait=false
kubectl delete namespace ragnar monitoring --ignore-not-found
if [ "${1:-}" = "--all" ]; then
  kubectl delete -f "https://github.com/kedacore/keda/releases/download/v${KEDA_VERSION}/keda-${KEDA_VERSION}.yaml" --ignore-not-found
  kubectl delete -f \
    "https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-${CNPG_VERSION%.*}/releases/cnpg-${CNPG_VERSION}.yaml" \
    --ignore-not-found
  kubectl delete -f \
    "https://github.com/kubernetes-sigs/metrics-server/releases/download/v${METRICS_SERVER_VERSION}/components.yaml" \
    --ignore-not-found
fi

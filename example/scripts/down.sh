#!/usr/bin/env bash
# Removes the example from Kubernetes. With --all, also removes the operators (CNPG, KEDA, metrics-server).
set -euo pipefail
cd "$(dirname "$0")/.."
kubectl delete -k k8s/base --ignore-not-found --wait=false
kubectl delete namespace ragnar monitoring --ignore-not-found
if [ "${1:-}" = "--all" ]; then
  kubectl delete -f "https://github.com/kedacore/keda/releases/download/v2.21.0/keda-2.21.0.yaml" --ignore-not-found
  kubectl delete -f "https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.30/releases/cnpg-1.30.1.yaml" --ignore-not-found
  kubectl delete -f "https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.9.0/components.yaml" --ignore-not-found
fi

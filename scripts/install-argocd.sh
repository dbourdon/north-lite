#!/usr/bin/env bash
# Install Argo CD into the current kube context and wait until it's up.
#
#   scripts/install-argocd.sh [version]
#
# Server-side apply: Argo CD's CRDs are too big for the client-side
# last-applied-configuration annotation.
set -euo pipefail

version="${1:-v3.6.2}"
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl apply -n argocd --server-side --force-conflicts \
  -f "https://raw.githubusercontent.com/argoproj/argo-cd/${version}/manifests/install.yaml" >/dev/null
for d in argocd-server argocd-repo-server argocd-applicationset-controller argocd-redis; do
  kubectl -n argocd rollout status "deploy/$d" --timeout=300s
done
kubectl -n argocd rollout status statefulset/argocd-application-controller --timeout=300s
echo "argo cd ${version} ready"

#!/usr/bin/env bash
# Install Calico into the current kube context and wait until the node is Ready.
# For kind clusters created with deploy/kind/calico.yaml (no default CNI).
#
#   scripts/install-calico.sh [version]
set -euo pipefail

version="${1:-v3.33.1}"
kubectl apply -f "https://raw.githubusercontent.com/projectcalico/calico/${version}/manifests/calico.yaml" >/dev/null
kubectl -n kube-system rollout status daemonset/calico-node --timeout=240s
kubectl -n kube-system rollout status deployment/calico-kube-controllers --timeout=240s
kubectl wait node --all --for=condition=Ready --timeout=120s
echo "calico ${version} ready"

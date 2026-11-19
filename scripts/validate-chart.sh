#!/usr/bin/env bash
# Render the chart once per flag profile and validate every manifest against the
# Kubernetes API schemas (kubeconform). Catches typos in apiVersion/kind/fields
# that `helm lint` accepts. Same script locally and in CI.
#
#   scripts/validate-chart.sh [kubernetes-version]
set -euo pipefail

k8s_version="${1:-1.33.1}"
chart="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/charts/north-lite"
rc=0
for profile in "$chart"/values-flags-*.yaml; do
  name=$(basename "$profile" .yaml)
  printf '%-28s ' "${name#values-flags-}"
  if ! helm template nl "$chart" -f "$chart/values-ci.yaml" -f "$profile" \
      | kubeconform -strict -summary -kubernetes-version "$k8s_version"; then
    rc=1
  fi
done
exit "$rc"

#!/usr/bin/env bash
# Snapshot everything useful about a release after a failure, for CI artifacts.
#
#   scripts/collect-diagnostics.sh <namespace> <output-dir> [kube-context]
#
# Never fails: a diagnostics step that errors would hide the original failure.
set -uo pipefail

ns="$1"
out="$2"
ctx_args=()
[[ -n "${3:-}" ]] && ctx_args=(--context "$3")
k() { kubectl "${ctx_args[@]}" -n "$ns" "$@"; }

mkdir -p "$out"
{
  echo "# kubectl get all,networkpolicy,configmap"
  k get all,networkpolicy,configmap -o wide
  echo
  echo "# events (oldest first)"
  k get events --sort-by=.lastTimestamp
} >"$out/overview.txt" 2>&1

helm status "$ns" -n "$ns" ${3:+--kube-context "$3"} >"$out/helm-status.txt" 2>&1
# Values can hold an inline apiKey.value (dev only): redact it, since diagnostics
# are uploaded as CI artifacts.
helm get values "$ns" -n "$ns" --all -o json ${3:+--kube-context "$3"} 2>&1 \
  | python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    v = json.loads(raw)
except ValueError:
    print(raw, end="")  # helm error text, not values
    sys.exit()
if (v.get("apiKey") or {}).get("value"):
    v["apiKey"]["value"] = "<redacted>"
print(json.dumps(v, indent=2))
' >"$out/helm-values.json" 2>&1
k describe pods >"$out/describe-pods.txt" 2>&1

for pod in $(k get pods -o name 2>/dev/null); do
  name="${pod#pod/}"
  k logs "$name" --all-containers --timestamps >"$out/logs-$name.txt" 2>&1
  # Logs of the crashed container, if it restarted (CrashLoopBackOff).
  k logs "$name" --all-containers --previous >"$out/logs-$name.previous.txt" 2>/dev/null \
    || rm -f "$out/logs-$name.previous.txt"
done

echo "diagnostics for namespace $ns written to $out:"
ls -1 "$out"
exit 0

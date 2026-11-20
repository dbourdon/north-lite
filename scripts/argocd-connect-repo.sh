#!/usr/bin/env bash
# Give the local Argo CD read-only access to this (private) GitHub repo.
#
#   scripts/argocd-connect-repo.sh
#
# Creates an SSH key pair, registers the public key as a READ-ONLY deploy key
# on dbourdon/north-lite (scoped to this one repo), and stores the private key
# in an Argo CD repository Secret. Nothing is printed. Undo with:
#   gh repo deploy-key list --repo dbourdon/north-lite
#   gh repo deploy-key delete <id> --repo dbourdon/north-lite
#   kubectl -n argocd delete secret repo-north-lite
set -euo pipefail

repo=dbourdon/north-lite
key="$HOME/.ssh/north-lite-argocd"

[[ -f "$key" ]] || ssh-keygen -q -t ed25519 -N "" -C "argocd-kind-north-lite" -f "$key"
gh repo deploy-key add "$key.pub" --repo "$repo" --title "argocd (local kind, read-only)" \
  || echo "(deploy key may already exist; continuing)"

kubectl -n argocd create secret generic repo-north-lite \
  --from-literal=type=git \
  --from-literal=url="git@github.com:$repo.git" \
  --from-file=sshPrivateKey="$key" \
  --dry-run=client -o yaml \
  | kubectl label --local -f - argocd.argoproj.io/secret-type=repository -o yaml \
  | kubectl apply -f -

echo "Argo CD can now read $repo (read-only deploy key)."

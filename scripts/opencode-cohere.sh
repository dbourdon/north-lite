#!/usr/bin/env bash
# Run OpenCode with Cohere's North Mini Code, taking CO_API_KEY from the shell or
# from the repo's .env (the key is exported into OpenCode's process only).
#
#   scripts/opencode-cohere.sh                 # interactive
#   scripts/opencode-cohere.sh run "prompt"    # one-shot
#
# Every agent step is one API call against the trial key's 1,000 calls/month.
# See docs/cohere-usage.md before long sessions.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${CO_API_KEY:-}" && -f "$root/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$root/.env"
  set +a
fi
: "${CO_API_KEY:?CO_API_KEY is not set (export it or put it in .env)}"

command -v opencode >/dev/null || {
  echo "opencode not found: npm install -g @opencode/cli" >&2
  exit 1
}
cd "$root"
exec opencode "$@"

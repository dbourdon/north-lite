# AGENTS.md

Instructions for coding agents (OpenCode / North Mini Code, Claude Code, GitHub
Copilot, Codex, Cursor). Humans: start with `README.md`.

north-lite is a small RAG service on Cohere's API plus the platform around it: a
Helm chart, a CI matrix with one kind cluster per feature-flag profile, Argo CD
environments, a simulated-user eval and a k6 benchmark gate.

## Layout

| Path | What |
|---|---|
| `src/north_lite/` | FastAPI service (`app.py`), `LLMClient` Protocol + Cohere client (`llm.py`), fake backend (`fake.py`), RAG, retries, eval harness, CLI |
| `tests/` | pytest; `-m live` tests call the real API |
| `charts/north-lite/` | Helm chart; `values-flags-*.yaml` = one environment per file; `tests/` = helm-unittest |
| `.github/workflows/` | `ci.yaml` (every PR), `release.yaml` (tags `vX.Y.Z`) |
| `.github/actions/bench-gate/` | TypeScript action; GitHub runs the committed `dist/index.js` |
| `deploy/` | Argo CD ApplicationSet; `release.yaml` = the promoted release |
| `loadtest/` | k6 script and per-profile p95 baselines |
| `evals/`, `data/` | eval questions and the handbook corpus |

## Checks: run the ones for what you changed, and report the real output

```bash
# Python (always)
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict src tests
uv run pytest -n auto -m "not live"

# Chart (anything under charts/ or deploy/)
helm lint --strict charts/north-lite -f charts/north-lite/values-ci.yaml
helm unittest charts/north-lite
scripts/validate-chart.sh          # kubeconform, every profile

# bench-gate action (anything under .github/actions/bench-gate/)
cd .github/actions/bench-gate && npm ci && npm run typecheck && npm test && npm run build
```

Tools may live in `~/.local/bin` (uv, helm v3, kind, kubectl, k6, kubeconform, ct).
Use Helm **v3**: v4 rejects the helm-unittest plugin.

## Rules

- **Never use the real API in tests or CI.** Tests use the fake backend
  (`NL_BACKEND=fake`); anything that calls Cohere is marked `@pytest.mark.live`.
  Never print, log or commit `CO_API_KEY` or `.env`.
- **Trial key budget:** 20 API calls per minute *in total* (not per endpoint), no
  `Retry-After`, 1,000 calls/month. Don't run `pytest -m live` or
  `north-lite eval --simulator llm` unless asked; pace live evals with `--pace-s 16`.
- **Code depends on `LLMClient` and the pydantic models in `llm.py`,** never on
  Cohere SDK types. New backend features go through the Protocol and the fake.
- **bench-gate:** after any change under `.github/actions/bench-gate/src/`, run `npm run build` and commit
  `dist/`. CI fails on a stale bundle. Test the built bundle, not just the source.
- **Chart:** any change under `charts/north-lite/` needs a `version` bump in
  `Chart.yaml` (`ct lint` enforces it). `appVersion` must equal `pyproject.toml`'s
  `version`. Keep `values.schema.json` in sync with `values.yaml`.
- **Flag profiles:** adding `values-flags-<name>.yaml` adds a CI e2e leg and an
  Argo CD environment automatically. Add `loadtest/baselines/<name>.json` too
  (without it the bench gate has nothing to compare), and ask the maintainer to add
  the new e2e check to branch protection.
- **Releases are immutable.** Never re-push or delete a tag or image. Promotion and
  rollback are PRs to `deploy/release.yaml`. See `docs/RELEASING.md`.
- **Git:** never push to `main` (it's protected); work on a branch and open a PR.
  Ask before pushing. In a git worktree, symlink `.env` from the main checkout if
  you need the key.
- **Local kind on old WSL kernels doesn't enforce NetworkPolicy,** so the airgapped
  profile's egress test fails locally. It's enforced in CI (Calico). Don't "fix" the
  test.
- **Writing:** Canadian spelling in docs, comments, commit messages and PRs
  (behaviour, colour, centre, licence (noun), cancelled, but -ize: organize).
  Never change spelling in identifiers, API fields or quoted output.
  Comments explain *why*, not what.

## Done means

The relevant checks above pass locally, new behaviour has a test, docs that
describe it are updated, and your summary states what you ran and the result.

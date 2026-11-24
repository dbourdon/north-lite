# Releasing north-lite

## Versions

| What | Where | Rule |
|---|---|---|
| App version | `pyproject.toml` `version` | SemVer; the single source of truth |
| Chart `appVersion` | `charts/north-lite/Chart.yaml` | Must equal the app version (`tests/test_chart.py`) |
| Chart `version` | `charts/north-lite/Chart.yaml` | Bump on **any** chart change (`ct lint` enforces on PRs) |
| Release tag | git tag `vX.Y.Z` on `main` | Must equal the app version (release workflow gate) |
| Image | `ghcr.io/dbourdon/north-lite:X.Y.Z` | **Immutable**: never re-pushed. `sha-<commit>` and `latest` are CI builds of `main`, not releases |
| Chart package | `oci://ghcr.io/dbourdon/charts/north-lite` | Published per release, `appVersion` = X.Y.Z |

`GET /version` on a running pod returns `{"version", "commit"}`: the check that a
promotion or rollback actually landed.

## Release (release manager checklist)

1. **Release PR**: bump `version` in `pyproject.toml` and `appVersion` in
   `Chart.yaml` (plus the chart `version` if the chart changed). Merge when CI is
   green: lint, types, tests, chart tests, the e2e matrix on every flag profile.
2. **Pre-release gates** (manual, on `main`):
   - Live eval: `north-lite eval --simulator llm --concurrency 1 --pace-s 16`
     against a real-backend deployment; hit rate must stay **>= 0.85**.
   - Benchmark gate (Day 9): p95 latency must not regress more than 15% vs baseline.
3. **Tag**: `git tag vX.Y.Z <main-commit> && git push origin vX.Y.Z`.
4. **Release workflow** (`.github/workflows/release.yaml`) runs:
   - Gates: tag = `pyproject.toml` = `appVersion`; commit is on `main`; CI passed
     on that exact commit; the image tag doesn't exist yet.
   - Publish: image `:X.Y.Z` (with the commit baked in), chart to GHCR (OCI),
     GitHub Release with generated notes and the chart attached.
5. **Promote**: PR setting `image.tag: "X.Y.Z"` in `deploy/release.yaml`. CI's
   `release-pin` job fails the PR if that image doesn't exist. Merge → Argo CD
   syncs every environment → PostSync checks run in each → verify `GET /version`.

Flags are not released: they're per-environment configuration, changed by PR to
the profile files and rolled out (and back) the same way.

## Rollback

Pick the smallest rollback that undoes the problem.

| Situation | Do this | Why it works |
|---|---|---|
| Bad release promoted to the Argo environments | **Revert the promotion PR** (or a PR setting the previous `image.tag`) | Argo syncs the previous immutable image; PostSync checks re-verify |
| Bad config or flag change | Revert that commit | Same path: the repo is the desired state |
| Argo-managed, and git is slow to fix | `argocd app set <app> --sync-policy none` → `argocd app rollback <app> <id>` → fix git → re-enable auto-sync | Auto-sync would otherwise undo a manual rollback |
| Helm-managed install (no Argo) | `helm rollback <release> <revision>` | Helm keeps release history; `helm history` lists revisions |

After any rollback: `GET /version` shows the expected version and commit, PostSync
or `helm test` checks pass, and a follow-up issue records the cause.

What makes rollback real: **immutable image tags**. A tag like `dev` or `latest`
can't be rolled back to, because its old contents are gone. Never re-push or
delete a release tag; fix forward with a new patch version instead.

## Where things are

- Release workflow: `.github/workflows/release.yaml`
- Promotion point: `deploy/release.yaml` (read by `deploy/argocd/appset.yaml`)
- Promotion gate: `release-pin` job in `.github/workflows/ci.yaml`
- Registry: the GHCR image and chart are public. A private registry would need an
  image pull secret (`imagePullSecrets` in the chart values).

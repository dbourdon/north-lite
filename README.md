# north-lite

A small North-style LLM service (FastAPI on the Cohere API) used to practise
developer-experience platform work: Helm, feature-flag environments, CI, benchmarks.

[![ci](https://github.com/dbourdon/north-lite/actions/workflows/ci.yaml/badge.svg)](https://github.com/dbourdon/north-lite/actions/workflows/ci.yaml)

## CI

`.github/workflows/ci.yaml` runs on every PR and push to `main`:

| Job | What it checks |
|---|---|
| `python` | ruff, `mypy --strict`, `pytest -n auto -m "not live"` (fake backend: no key, no quota) |
| `chart` | `ct lint` (yamllint, schema, Chart.yaml version bump on PRs), `helm unittest`, kubeconform on every flag profile |
| `image` | Docker build with the GitHub Actions layer cache; pushed to GHCR only from `main` |
| `e2e (<profile>)` | one leg per `values-flags-*.yaml`: kind cluster, `helm install`, `helm test` (flag checks; egress check for air-gapped). Diagnostics uploaded on failure |

`python`, `chart` and `image` run in parallel; `e2e` waits for `chart`. The kind
setup is a reusable composite action, `.github/actions/setup-kind-env`.

Run the same checks locally: `uv run pytest -n auto`, `ct lint --config ct.yaml --all`,
`helm unittest charts/north-lite`, `scripts/validate-chart.sh`.

## Evals: a simulated user

`north-lite eval` plays a user against a running service: it asks each question in
`evals/handbook.jsonl` (20, worded differently from the docs), reads the answer, and
follows up if unsatisfied. It reports retrieval hit rate, citation coverage and
precision, answer accuracy and user satisfaction, and fails below a hit-rate gate
(0.85, from the handbook's own "LLM evaluations" rule).

```bash
NL_BACKEND=fake uv run north-lite serve &               # or port-forward a kind release
uv run north-lite eval                                   # scripted user: free, deterministic
uv run north-lite eval --simulator llm --concurrency 1 --pace-s 16 --out report.json
```

With `--simulator llm`, Command A+ plays a new engineer. It only knows its goal, not
the expected answer, so its "satisfied" verdict is independent of the keyword checks.
A live run costs ~100–140 calls; see `docs/cohere-usage.md`.

## Run locally

```bash
uv sync
uv run pytest -n auto                      # offline tests, no API key needed
NL_BACKEND=fake uv run north-lite serve    # http://127.0.0.1:8000/docs
uv run pytest -m live                      # real API (needs CO_API_KEY in env or .env)
```

## Container and local cluster

```bash
docker build -t north-lite:dev .           # multi-stage, non-root (UID 10001), ~200 MB
docker run --rm -p 8000:8000 -e NL_BACKEND=fake north-lite:dev

kind create cluster --name nl --image kindest/node:v1.33.1
kind load docker-image north-lite:dev --name nl
```

The node image is pinned to Kubernetes 1.33 because Docker Desktop on this WSL
kernel uses cgroup v1, which newer kubelets refuse to run on. With cgroup v2
(see `docker info | grep -i cgroup`) the default kind image works.

## Deploy with Helm

```bash
# Fake backend: no key needed
helm upgrade --install nl charts/north-lite -n nl --create-namespace \
  --set config.backend=fake --wait

# Flip a flag: the ConfigMap changes, its checksum annotation rolls the pods
helm upgrade --install nl charts/north-lite -n nl --reuse-values --set features.rerank=false --wait
helm history nl -n nl
helm rollback nl 1 -n nl --wait

# Real backend with a pre-created Secret (preferred over apiKey.value)
kubectl -n nl create secret generic cohere-api-key --from-literal=CO_API_KEY="$CO_API_KEY"
helm upgrade --install nl charts/north-lite -n nl \
  --set config.backend=cohere --set apiKey.existingSecret=cohere-api-key --wait
```

Rendering fails early if `config.backend=cohere` has no key source. Pods roll on
ConfigMap changes and on chart-created Secret changes; a change inside an
`existingSecret` needs `kubectl rollout restart` (the chart can't hash a Secret it
doesn't render). If you build the Secret from `.env` with `--from-env-file`, strip
quotes first: kubectl keeps them, python-dotenv doesn't.

## Flag profiles and chart tests

Each environment is `values-ci.yaml` (fake backend, local image) plus one profile:

| Profile | rerank | streaming | cache | Network |
|---|---|---|---|---|
| `minimal` | off | off | off | open |
| `full` | on | on | on | open |
| `no-rerank` | off | on | on | open |
| `airgapped` | on | on | off | all egress denied (NetworkPolicy) |

```bash
helm lint charts/north-lite --strict -f charts/north-lite/values-ci.yaml
helm unittest charts/north-lite              # render-level assertions (23)
uv run pytest tests/test_chart.py            # checksum changes, schema, packaging

p=full
helm upgrade --install nl-$p charts/north-lite -n nl-$p --create-namespace \
  -f charts/north-lite/values-ci.yaml -f charts/north-lite/values-flags-$p.yaml --wait
helm test nl-$p -n nl-$p --logs              # checks the live service matches its flags
```

- `values.schema.json` rejects bad values before anything reaches the cluster
  (`--set replicaCount=abc`, a typo like `features.rerak`, an unknown backend).
- `helm test` runs a pod that checks streaming, rerank and cache behaviour against
  the profile's flags. With a NetworkPolicy, a second test pod under the same
  policy must fail to reach the internet.
- The egress test caught a real gap: kind's default CNI (kindnet) accepted the
  NetworkPolicy but didn't enforce it, both on this WSL host and on GitHub
  runners. CI therefore runs any profile with `networkPolicy.enabled: true` on a
  kind cluster with **Calico** (`deploy/kind/calico.yaml`,
  `scripts/install-calico.sh`). Calico doesn't start on this host's WSL 5.15
  kernel either, so the air-gapped egress check is verified in CI only.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `CO_API_KEY` | – | Cohere API key (never commit it) |
| `NL_BACKEND` | `cohere` | `cohere` or `fake` (deterministic, offline) |
| `NL_FEATURE_RERANK` | `true` | rerank search results |
| `NL_FEATURE_STREAMING` | `true` | allow `POST /chat?stream=true` (SSE) |
| `NL_FEATURE_CACHE` | `false` | in-process LRU cache for `/search` and `/chat` |
| `NL_REQUIRE_APPROVAL` | `true` | `POST /tools/send-email` waits for human approval |
| `NL_MAX_ATTEMPTS` | `4` | tries per API call (backoff with full jitter) |

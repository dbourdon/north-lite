# north-lite

A small North-style LLM service (FastAPI on the Cohere API) used to practise
developer-experience platform work: Helm, feature-flag environments, CI, benchmarks.

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

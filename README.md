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

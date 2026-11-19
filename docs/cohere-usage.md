# Cohere API usage and call budget

The project runs on a Cohere **trial key**: free, but capped at **1,000 API calls
per month** across all endpoints (checked 2026-11-19, docs.cohere.com/docs/rate-limits).

## Per-minute limits (trial)

The docs list per-endpoint limits (chat 20 req/min, rerank 10 req/min, embed
2,000 inputs/min), but in practice the trial key is limited to **20 API calls per
minute in total**. The API's own 429 says so ("limited to 20 API calls / minute"),
and it sends no `Retry-After` header.

So: `north_lite.retry` (backoff with jitter, `NL_MAX_ATTEMPTS`) smooths short
bursts, but it gives up within seconds and can't wait out a one-minute window.
Anything that makes many calls must **pace** itself: `north-lite eval --pace-s 16`
spaces cases so ~5 calls per case stay under 20/min.

## What costs calls

| Activity | Calls | Notes |
|---|---|---|
| `pytest` (default) | 0 | fake backend, no network |
| CI (GitHub Actions) | 0 | always `NL_BACKEND=fake`; the key is never in CI |
| `pytest -m live` | ~5 | stream + RAG + app RAG |
| Service start with `NL_BACKEND=cohere` | 1 | embeds the 20-doc handbook once |
| One `/chat` with `rag=true` | 3 | embed query + rerank + chat |
| Live eval, `--simulator llm` (20 questions) | ~100–140 | per case: simulator opening + verdict (2) + service embed/rerank/chat (3); follow-ups add ~4 |
| OpenCode + North Mini Code | 1 per agent step | a small feature is ~50–200 |

## Budget for the remaining sprint

| Use | Calls |
|---|---|
| Live tests (~20 runs) | 100 |
| Day 7 test agent and evals (~3 full runs + tuning) | 300 |
| North Mini Code via OpenCode (Day 9 TypeScript bench-gate) | 250 |
| Demos and manual checks | 150 |
| Reserve | 200 |
| **Total** | **1,000** |

Usage is shown at dashboard.cohere.com. If the cap is hit, everything except
live tests, evals and OpenCode keeps working on the fake backend.

## Coding with North Mini Code

`north-mini-code-1.0` is Cohere's open-weight agentic coding model (30B total /
3B active MoE, 256K context; Apache 2.0). It is available on the trial key via
Cohere's OpenAI-compatible endpoint, `https://api.cohere.ai/compatibility/v1`.

```bash
npm install -g @opencode/cli          # once
scripts/opencode-cohere.sh            # uses opencode.jsonc in the repo root
```

Plan: build the Day 9 TypeScript bench-gate action with it, and keep notes on
what it did well and badly, for the interview.

## Cohere inside the product

The service itself runs on Cohere: Command A+ for chat (with citations),
Embed v5 and Rerank v5 for search. Day 7's user-simulator test agent and the
LLM judge for the eval set also run on Command A+ with tool use.

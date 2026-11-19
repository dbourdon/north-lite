import itertools
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from north_lite.app import create_app, get_settings
from north_lite.fake import FakeLLMClient
from north_lite.settings import Settings

QUESTION = "How long are CI build logs kept?"


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    app = create_app(Settings(backend="fake", _env_file=None))  # type: ignore[call-arg]
    # httpx's ASGITransport doesn't run lifespan events, so run them here.
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def set_flags(app: FastAPI, **flags: bool) -> None:
    """Change flags for this test only: swap the settings dependency."""
    base: Settings = app.state.settings
    app.dependency_overrides[get_settings] = lambda: base.model_copy(update=flags)


def fake_llm(app: FastAPI) -> FakeLLMClient:
    llm = app.state.services.llm
    assert isinstance(llm, FakeLLMClient)
    return llm


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields.get("event", "message"), json.loads(fields["data"])))
    return events


FLAG_COMBOS = [
    pytest.param(r, s, c, id=f"rerank={r}-streaming={s}-cache={c}")
    for r, s, c in itertools.product([True, False], repeat=3)
]


@pytest.mark.parametrize(("rerank", "streaming", "cache"), FLAG_COMBOS)
async def test_every_flag_combination(
    app: FastAPI, client: httpx.AsyncClient, rerank: bool, streaming: bool, cache: bool
) -> None:
    set_flags(app, feature_rerank=rerank, feature_streaming=streaming, feature_cache=cache)
    llm = fake_llm(app)

    # /search: rerank flag decides whether the reranker runs.
    body = {"query": QUESTION, "top_n": 3}
    first = await client.post("/search", json=body)
    assert first.status_code == 200
    assert first.json()["reranked"] is rerank
    assert "build-logs" in [r["id"] for r in first.json()["results"]]
    assert (llm.rerank_calls > 0) is rerank

    # Same request again: cache flag decides hit vs recompute.
    second = await client.post("/search", json=body)
    assert first.headers["x-cache"] == ("miss" if cache else "bypass")
    assert second.headers["x-cache"] == ("hit" if cache else "bypass")
    assert second.json() == first.json()

    # /chat?stream=true: streaming flag decides SSE vs 400.
    msgs = {"messages": [{"role": "user", "content": "hi"}]}
    streamed = await client.post("/chat", params={"stream": "true"}, json=msgs)
    if streaming:
        assert streamed.status_code == 200
        assert streamed.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(streamed.text)
        assert events[-1][0] == "done"
        text = "".join(d["text"] for name, d in events if name == "message")
        assert text.strip() == "fake answer to: hi"
    else:
        assert streamed.status_code == 400

    # /chat with rag works under every combination and cites what it retrieved.
    answer = await client.post(
        "/chat", json={"messages": [{"role": "user", "content": QUESTION}], "rag": True}
    )
    assert answer.status_code == 200
    data = answer.json()
    assert data["citations"]
    assert all(set(c["doc_ids"]) <= set(data["doc_ids"]) for c in data["citations"])


async def test_health_and_ready(client: httpx.AsyncClient) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    assert (await client.get("/readyz")).status_code == 200


async def test_not_ready_before_startup() -> None:
    app = create_app(Settings(backend="fake", _env_file=None))  # type: ignore[call-arg]
    transport = httpx.ASGITransport(app=app)  # lifespan not run: no index yet
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/healthz")).status_code == 200
        assert (await c.get("/readyz")).status_code == 503


async def test_cache_key_includes_flags(app: FastAPI, client: httpx.AsyncClient) -> None:
    body = {"query": QUESTION}
    set_flags(app, feature_cache=True, feature_rerank=True)
    assert (await client.post("/search", json=body)).headers["x-cache"] == "miss"
    set_flags(app, feature_cache=True, feature_rerank=False)
    resp = await client.post("/search", json=body)
    assert resp.headers["x-cache"] == "miss"  # not a stale reranked result
    assert resp.json()["reranked"] is False


@pytest.mark.parametrize(
    "body",
    [{"messages": []}, {"messages": [{"role": "robot", "content": "x"}]}, {}],
    ids=["empty", "bad-role", "missing"],
)
async def test_chat_rejects_bad_input(client: httpx.AsyncClient, body: dict[str, Any]) -> None:
    assert (await client.post("/chat", json=body)).status_code == 422


async def test_stream_with_rag_is_rejected(client: httpx.AsyncClient) -> None:
    body = {"messages": [{"role": "user", "content": "x"}], "rag": True}
    assert (await client.post("/chat", params={"stream": "true"}, json=body)).status_code == 400


EMAIL = {"to": "ops@example.com", "subject": "Deploy done", "body": "v1.2 is live"}


async def test_email_waits_for_approval(app: FastAPI, client: httpx.AsyncClient) -> None:
    set_flags(app, require_approval=True)
    svc = app.state.services

    resp = await client.post("/tools/send-email", json=EMAIL)
    assert resp.status_code == 202
    assert resp.json()["status"] == "pending_approval"
    assert svc.outbox == []  # nothing sent yet

    email_id = resp.json()["id"]
    approved = await client.post(f"/approvals/{email_id}/approve")
    assert approved.json() == {"id": email_id, "status": "sent"}
    assert len(svc.outbox) == 1
    # Approving twice must not send twice.
    assert (await client.post(f"/approvals/{email_id}/approve")).status_code == 404


async def test_rejected_email_is_never_sent(app: FastAPI, client: httpx.AsyncClient) -> None:
    set_flags(app, require_approval=True)
    email_id = (await client.post("/tools/send-email", json=EMAIL)).json()["id"]
    rejected = await client.post(f"/approvals/{email_id}/reject")
    assert rejected.json()["status"] == "rejected"
    assert app.state.services.outbox == []


async def test_email_sent_directly_without_approval(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    set_flags(app, require_approval=False)
    resp = await client.post("/tools/send-email", json=EMAIL)
    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"
    assert len(app.state.services.outbox) == 1


async def test_unknown_decision_is_rejected(client: httpx.AsyncClient) -> None:
    assert (await client.post("/approvals/abc/maybe")).status_code == 422

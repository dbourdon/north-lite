"""HTTP service: chat (optionally SSE-streamed), search, health probes, and an approval-gated tool.

Run locally:
    NL_BACKEND=fake uv run uvicorn --factory north_lite.app:create_app --reload
"""

import asyncio
import hashlib
import importlib.metadata
import json
import os
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated, Literal

from cohere.core.api_error import ApiError
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from north_lite.llm import Citation, LLMClient, Message, make_client
from north_lite.rag import MiniRAG, load_docs
from north_lite.settings import Settings

# ---------------------------------------------------------------- API models


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1)
    rag: bool = False  # ground the answer in the handbook and return citations


class ChatOut(BaseModel):
    text: str
    citations: list[Citation] = Field(default_factory=list)
    doc_ids: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_n: int = Field(default=3, ge=1, le=20)


class SearchResult(BaseModel):
    id: str
    title: str
    text: str
    score: float


class SearchOut(BaseModel):
    results: list[SearchResult]
    reranked: bool


class EmailRequest(BaseModel):
    to: str = Field(min_length=3)
    subject: str
    body: str


class EmailOut(BaseModel):
    id: str
    status: Literal["sent", "pending_approval", "rejected"]


# ------------------------------------------------------------------- services


class LRUCache[T]:
    """Tiny in-process LRU. Per pod, so replicas don't share it (fine for a demo)."""

    def __init__(self, maxsize: int = 256) -> None:
        self._data: OrderedDict[str, T] = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: str) -> T | None:
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        return None

    def put(self, key: str, value: T) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        if len(self._data) > self._maxsize:
            self._data.popitem(last=False)


@dataclass
class Services:
    llm: LLMClient
    rag: MiniRAG
    search_cache: LRUCache[SearchOut] = field(default_factory=LRUCache)
    chat_cache: LRUCache[ChatOut] = field(default_factory=LRUCache)
    pending: dict[str, EmailRequest] = field(default_factory=dict)
    outbox: list[EmailRequest] = field(default_factory=list)  # stands in for an SMTP client


def cache_key(kind: str, payload: BaseModel, **flags: bool) -> str:
    # Flags that change the result are part of the key, so flipping one at runtime
    # never serves an answer computed under the old setting.
    raw = json.dumps({"kind": kind, "req": payload.model_dump(), "flags": flags}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


# --------------------------------------------------------------- dependencies
# Routes get settings and services through Depends(), so tests can swap either
# with app.dependency_overrides without touching the app's code.


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


SettingsDep = Annotated[Settings, Depends(get_settings)]
ServicesDep = Annotated[Services, Depends(get_services)]

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    # Liveness: the process is up. No dependencies checked, so a slow LLM API
    # never gets healthy pods killed.
    return {"status": "ok"}


@router.get("/version")
async def version() -> dict[str, str]:
    # What is actually running: confirms a promotion or a rollback landed.
    # The version comes from the installed package (pyproject.toml, the single
    # source of truth); the commit is baked into the image at build time.
    try:
        pkg_version = importlib.metadata.version("north-lite")
    except importlib.metadata.PackageNotFoundError:
        pkg_version = "unknown"
    return {"version": pkg_version, "commit": os.environ.get("NL_GIT_SHA", "unknown")}


@router.get("/readyz")
async def readyz(request: Request) -> dict[str, str]:
    # Readiness: only take traffic once the search index is built.
    services: Services | None = getattr(request.app.state, "services", None)
    if services is None or not services.rag.ready:
        raise HTTPException(status_code=503, detail="not ready")
    return {"status": "ready"}


@router.post("/search")
async def search(
    req: SearchRequest, response: Response, settings: SettingsDep, svc: ServicesDep
) -> SearchOut:
    await asyncio.sleep(0.05)  # DEMO: deliberate 50 ms regression; the bench gate must fail
    key = cache_key("search", req, rerank=settings.feature_rerank)
    if settings.feature_cache and (cached := svc.search_cache.get(key)) is not None:
        response.headers["X-Cache"] = "hit"
        return cached
    hits = await svc.rag.search(req.query, top_n=req.top_n, rerank=settings.feature_rerank)
    out = SearchOut(
        results=[
            SearchResult(
                id=h.document.id, title=h.document.title, text=h.document.text, score=h.score
            )
            for h in hits
        ],
        reranked=settings.feature_rerank,
    )
    if settings.feature_cache:
        svc.search_cache.put(key, out)
    response.headers["X-Cache"] = "miss" if settings.feature_cache else "bypass"
    return out


async def _sse(llm: LLMClient, messages: Sequence[Message]) -> AsyncIterator[str]:
    # Server-Sent Events: "data: <json>\n\n" per chunk, then a named "done" event.
    # Once streaming starts the 200 status is already sent, so errors must be
    # reported in-band as an event rather than as an HTTP error code.
    try:
        async for chunk in llm.stream_chat(messages):
            yield f"data: {json.dumps({'text': chunk})}\n\n"
    except Exception as exc:
        yield f"event: error\ndata: {json.dumps({'error': type(exc).__name__})}\n\n"
        return
    yield "event: done\ndata: {}\n\n"


@router.post("/chat", response_model=None)
async def chat(
    req: ChatRequest,
    response: Response,
    settings: SettingsDep,
    svc: ServicesDep,
    stream: Annotated[bool, Query()] = False,
) -> ChatOut | StreamingResponse:
    if stream:
        if not settings.feature_streaming:
            raise HTTPException(400, "streaming is disabled (NL_FEATURE_STREAMING=false)")
        if req.rag:
            raise HTTPException(400, "rag=true is not supported with stream=true")
        return StreamingResponse(
            _sse(svc.llm, req.messages),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    key = cache_key("chat", req, rerank=settings.feature_rerank)
    if settings.feature_cache and (cached := svc.chat_cache.get(key)) is not None:
        response.headers["X-Cache"] = "hit"
        return cached
    if req.rag:
        question = next(m.content for m in reversed(req.messages) if m.role == "user")
        resp, docs = await svc.rag.answer(question, rerank=settings.feature_rerank)
        out = ChatOut(text=resp.text, citations=resp.citations, doc_ids=[d.id for d in docs])
    else:
        resp = await svc.llm.chat(req.messages)
        out = ChatOut(text=resp.text, citations=resp.citations)
    if settings.feature_cache:
        svc.chat_cache.put(key, out)
    response.headers["X-Cache"] = "miss" if settings.feature_cache else "bypass"
    return out


@router.post("/tools/send-email")
async def send_email(
    req: EmailRequest, response: Response, settings: SettingsDep, svc: ServicesDep
) -> EmailOut:
    # A side-effecting "agent tool". With approval required it is parked until a
    # human approves it: the 202 says "accepted, not done yet".
    email_id = uuid.uuid4().hex
    if settings.require_approval:
        svc.pending[email_id] = req
        response.status_code = 202
        return EmailOut(id=email_id, status="pending_approval")
    svc.outbox.append(req)
    return EmailOut(id=email_id, status="sent")


@router.post("/approvals/{email_id}/{decision}")
async def decide(
    email_id: str, decision: Literal["approve", "reject"], svc: ServicesDep
) -> EmailOut:
    req = svc.pending.pop(email_id, None)
    if req is None:
        raise HTTPException(404, "no pending approval with that id")
    if decision == "reject":
        return EmailOut(id=email_id, status="rejected")
    svc.outbox.append(req)
    return EmailOut(id=email_id, status="sent")


# ------------------------------------------------------------------------ app


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Runs once per process: build the client and the search index before the
    # first request, instead of lazily on whichever request comes first.
    settings: Settings = app.state.settings
    llm = make_client(settings)
    rag = MiniRAG(llm, load_docs(settings.docs_path))
    await rag.index()
    app.state.services = Services(llm=llm, rag=rag)
    yield


async def upstream_error(request: Request, exc: Exception) -> JSONResponse:
    """The LLM provider failed after our retries: say so, instead of a bare 500.

    502 = "the server we depend on gave a bad answer" (not our bug); 503 + Retry-After
    when we were rate-limited, so clients and load balancers back off instead of
    hammering. The provider's status and error type are passed on for debugging.
    """
    assert isinstance(exc, ApiError)
    body = exc.body if isinstance(exc.body, dict) else {}
    detail = {
        "detail": "upstream LLM error",
        "upstream_status": exc.status_code,
        "upstream_error": body.get("error_type") or body.get("message"),
    }
    if exc.status_code == 429:
        return JSONResponse(detail, status_code=503, headers={"Retry-After": "60"})
    return JSONResponse(detail, status_code=502)


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="north-lite", lifespan=lifespan)
    app.state.settings = settings or Settings()
    app.include_router(router)
    app.add_exception_handler(ApiError, upstream_error)
    return app

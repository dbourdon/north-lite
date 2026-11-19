"""Typed async LLM client: a Protocol, a Cohere implementation and a batching helper.

Callers depend on `LLMClient` and on the pydantic models below, never on SDK types,
so the backend can be swapped (Cohere, fake, another vendor) without touching them.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Literal, Protocol

import cohere
from pydantic import BaseModel, Field

from north_lite.settings import Settings

Role = Literal["system", "user", "assistant"]
InputType = Literal["search_document", "search_query", "classification", "clustering"]

EMBED_BATCH_SIZE = 96  # Cohere's max texts per embed request.


class Message(BaseModel):
    role: Role
    content: str


class Document(BaseModel):
    id: str
    title: str = ""
    text: str


class Citation(BaseModel):
    start: int
    end: int
    text: str
    doc_ids: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    text: str
    citations: list[Citation] = Field(default_factory=list)


class RerankHit(BaseModel):
    index: int  # position in the documents list passed to rerank()
    score: float


class LLMClient(Protocol):
    async def chat(
        self, messages: Sequence[Message], documents: Sequence[Document] = ()
    ) -> ChatResponse: ...

    # Declared without `async`: implementations are async generators, whose type
    # is "a function returning AsyncIterator", which is what this signature says.
    def stream_chat(self, messages: Sequence[Message]) -> AsyncIterator[str]: ...

    async def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]: ...

    async def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[RerankHit]: ...


def _to_sdk_message(m: Message) -> cohere.ChatMessageV2:
    match m.role:
        case "system":
            return cohere.SystemChatMessageV2(content=m.content)
        case "user":
            return cohere.UserChatMessageV2(content=m.content)
        case "assistant":
            return cohere.AssistantChatMessageV2(content=m.content)


class CohereClient:
    """`LLMClient` on cohere.AsyncClientV2 (v2 API)."""

    def __init__(self, settings: Settings, sdk: cohere.AsyncClientV2 | None = None) -> None:
        if sdk is None:
            if settings.co_api_key is None:
                raise ValueError("CO_API_KEY is not set (or use NL_BACKEND=fake)")
            sdk = cohere.AsyncClientV2(
                api_key=settings.co_api_key.get_secret_value(), timeout=settings.timeout_s
            )
        self._sdk = sdk
        self._s = settings

    async def chat(
        self, messages: Sequence[Message], documents: Sequence[Document] = ()
    ) -> ChatResponse:
        # Passing our ids means citations point back at our Document.id values.
        sdk_docs: list[cohere.V2ChatRequestDocumentsItem] = [
            cohere.Document(id=d.id, data={"title": d.title, "snippet": d.text}) for d in documents
        ]
        resp = await self._sdk.chat(
            model=self._s.chat_model,
            messages=[_to_sdk_message(m) for m in messages],
            documents=sdk_docs or None,
        )
        text = "".join(
            c.text
            for c in resp.message.content or []
            if isinstance(c, cohere.TextAssistantMessageResponseContentItem)
        )
        citations = [
            Citation(
                start=c.start or 0,
                end=c.end or 0,
                text=c.text or "",
                doc_ids=[
                    s.id for s in c.sources or [] if isinstance(s, cohere.DocumentSource) and s.id
                ],
            )
            for c in resp.message.citations or []
        ]
        return ChatResponse(text=text, citations=citations)

    async def stream_chat(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        stream = self._sdk.chat_stream(
            model=self._s.chat_model, messages=[_to_sdk_message(m) for m in messages]
        )
        async for event in stream:
            # Only content-delta events carry answer text; the rest are lifecycle events.
            if isinstance(event, cohere.ContentDeltaV2ChatStreamResponse):
                content = event.delta and event.delta.message and event.delta.message.content
                if content and content.text:
                    yield content.text

    async def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        if len(texts) > EMBED_BATCH_SIZE:
            raise ValueError(f"embed() takes at most {EMBED_BATCH_SIZE} texts; use embed_many()")
        resp = await self._sdk.embed(
            model=self._s.embed_model,
            texts=list(texts),
            input_type=input_type,
            embedding_types=["float"],
        )
        vectors = resp.embeddings.float_
        if vectors is None or len(vectors) != len(texts):
            raise RuntimeError("embed response is missing float embeddings")
        return vectors

    async def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[RerankHit]:
        resp = await self._sdk.rerank(
            model=self._s.rerank_model, query=query, documents=list(documents), top_n=top_n
        )
        return [RerankHit(index=r.index, score=r.relevance_score) for r in resp.results]


async def embed_many(
    client: LLMClient,
    texts: Sequence[str],
    input_type: InputType,
    *,
    batch_size: int = EMBED_BATCH_SIZE,
    max_concurrency: int = 4,
) -> list[list[float]]:
    """Embed any number of texts: split into batches, run them concurrently, keep order.

    The semaphore caps in-flight requests so a big corpus doesn't trip rate limits;
    gather() returns results in submission order, so output[i] matches texts[i].
    """
    sem = asyncio.Semaphore(max_concurrency)

    async def one(batch: Sequence[str]) -> list[list[float]]:
        async with sem:
            return await client.embed(batch, input_type)

    batches = [texts[i : i + batch_size] for i in range(0, len(texts), batch_size)]
    results = await asyncio.gather(*(one(b) for b in batches))
    return [vec for batch in results for vec in batch]


def make_client(settings: Settings) -> LLMClient:
    if settings.backend == "fake":
        from north_lite.fake import FakeLLMClient

        return FakeLLMClient()
    return CohereClient(settings)

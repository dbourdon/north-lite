"""Deterministic in-process LLM for tests and CI: no key, no network, no rate limits."""

import asyncio
import hashlib
import math
import re
from collections.abc import AsyncIterator, Sequence

from north_lite.llm import ChatResponse, Citation, Document, InputType, Message, RerankHit

DIM = 256


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _bag_of_words(text: str) -> list[float]:
    # Hashed bag of words, L2-normalised. Similar wording gives similar vectors,
    # so retrieval tests behave like the real thing. hashlib, not hash(), because
    # hash() is randomised per process and would make tests non-deterministic.
    vec = [0.0] * DIM
    for tok in _tokens(text):
        vec[int(hashlib.md5(tok.encode()).hexdigest(), 16) % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class FakeLLMClient:
    def __init__(self, latency_s: float = 0.0) -> None:
        self.latency_s = latency_s
        self.embed_calls: list[int] = []  # batch size of each embed() call
        self.in_flight = 0
        self.max_in_flight = 0

    async def chat(
        self, messages: Sequence[Message], documents: Sequence[Document] = ()
    ) -> ChatResponse:
        question = messages[-1].content
        if not documents:
            return ChatResponse(text=f"fake answer to: {question}")
        top = documents[0]
        text = f"According to {top.title or top.id}: {top.text}"
        return ChatResponse(
            text=text, citations=[Citation(start=0, end=len(text), text=text, doc_ids=[top.id])]
        )

    async def stream_chat(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        for word in f"fake answer to: {messages[-1].content}".split(" "):
            yield word + " "

    async def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        self.embed_calls.append(len(texts))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.latency_s)
            return [_bag_of_words(t) for t in texts]
        finally:
            self.in_flight -= 1

    async def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[RerankHit]:
        q = set(_tokens(query))
        scored = [
            RerankHit(index=i, score=len(q & set(_tokens(d))) / (len(q) or 1))
            for i, d in enumerate(documents)
        ]
        return sorted(scored, key=lambda h: h.score, reverse=True)[:top_n]

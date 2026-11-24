"""Minimal RAG: embed a corpus, retrieve by cosine similarity, rerank, answer with citations."""

import json
import math
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from north_lite.llm import ChatResponse, Document, LLMClient, Message, embed_many


class SearchHit(BaseModel):
    document: Document
    score: float  # rerank relevance (0..1) when reranked, else cosine similarity


def load_docs(path: Path) -> list[Document]:
    return TypeAdapter(list[Document]).validate_python(json.loads(path.read_text()))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class MiniRAG:
    def __init__(
        self, client: LLMClient, docs: Sequence[Document], *, embed_concurrency: int = 4
    ) -> None:
        self._client = client
        self._docs = list(docs)
        self._embed_concurrency = embed_concurrency
        self._vectors: list[list[float]] = []

    async def index(self) -> None:
        texts = [f"{d.title}\n{d.text}" for d in self._docs]
        self._vectors = await embed_many(
            self._client, texts, "search_document", max_concurrency=self._embed_concurrency
        )

    @property
    def ready(self) -> bool:
        return bool(self._vectors)

    async def search(
        self, query: str, k: int = 8, top_n: int = 3, rerank: bool = True
    ) -> list[SearchHit]:
        if not self._vectors:
            await self.index()
        # Different input_type for queries: the embed model is trained asymmetrically.
        [qvec] = await self._client.embed([query], "search_query")
        scored = sorted(
            ((cosine(qvec, v), d) for v, d in zip(self._vectors, self._docs, strict=True)),
            key=lambda pair: pair[0],
            reverse=True,
        )[:k]
        if not rerank:
            return [SearchHit(document=d, score=s) for s, d in scored[:top_n]]
        # Embeddings give cheap recall; the cross-encoder reranker gives precision.
        candidates = [d for _, d in scored]
        hits = await self._client.rerank(query, [f"{d.title}\n{d.text}" for d in candidates], top_n)
        return [SearchHit(document=candidates[h.index], score=h.score) for h in hits]

    async def retrieve(
        self, query: str, k: int = 8, top_n: int = 3, rerank: bool = True
    ) -> list[Document]:
        return [h.document for h in await self.search(query, k, top_n, rerank)]

    async def answer(
        self, question: str, rerank: bool = True
    ) -> tuple[ChatResponse, list[Document]]:
        docs = await self.retrieve(question, rerank=rerank)
        resp = await self._client.chat([Message(role="user", content=question)], documents=docs)
        return resp, docs

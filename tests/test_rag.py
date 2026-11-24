import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest

from north_lite.fake import FakeLLMClient
from north_lite.llm import Document, InputType
from north_lite.rag import MiniRAG, load_docs

DOCS = Path(__file__).resolve().parents[1] / "data" / "handbook.json"


@pytest.fixture
def rag() -> MiniRAG:
    return MiniRAG(FakeLLMClient(), load_docs(DOCS))


def test_handbook_has_20_unique_docs() -> None:
    docs = load_docs(DOCS)
    assert len(docs) == 20
    assert len({d.id for d in docs}) == 20


@pytest.mark.parametrize(
    ("question", "expected_id"),
    [
        ("How long are CI build logs kept?", "build-logs"),
        ("When does the on-call shift hand over?", "oncall-rotation"),
        ("What p95 latency regression blocks a release?", "benchmarks"),
    ],
)
@pytest.mark.parametrize("rerank", [True, False])
async def test_retrieves_the_right_doc(
    rag: MiniRAG, question: str, expected_id: str, rerank: bool
) -> None:
    docs = await rag.retrieve(question, rerank=rerank)
    assert expected_id in [d.id for d in docs]


async def test_answer_cites_retrieved_docs(rag: MiniRAG) -> None:
    resp, docs = await rag.answer("How long are CI build logs kept?")
    assert resp.citations
    retrieved = {d.id for d in docs}
    assert all(set(c.doc_ids) <= retrieved for c in resp.citations)


async def test_index_respects_embed_concurrency() -> None:
    in_flight = peak = 0

    class Counting(FakeLLMClient):
        async def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return await super().embed(texts, input_type)

    # 200 docs = 3 embed batches of <=96; with concurrency 1 they must run one at a time.
    docs = [Document(id=str(i), text=f"doc {i}") for i in range(200)]
    rag = MiniRAG(Counting(), docs, embed_concurrency=1)
    await rag.index()
    assert rag.ready
    assert peak == 1

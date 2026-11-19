"""Real-API smoke tests. Excluded by default; run with: uv run pytest -m live"""

from pathlib import Path

import pytest

from north_lite.llm import CohereClient, Message
from north_lite.rag import MiniRAG, load_docs
from north_lite.settings import Settings

DOCS = Path(__file__).resolve().parents[1] / "data" / "handbook.json"

pytestmark = [
    pytest.mark.live,
    # Settings() also reads .env, so a key there counts, not just an exported one.
    pytest.mark.skipif(Settings().co_api_key is None, reason="CO_API_KEY not set (env or .env)"),
]


async def test_stream_chat_real() -> None:
    client = CohereClient(Settings())
    chunks = [c async for c in client.stream_chat([Message(role="user", content="Say OK.")])]
    assert "".join(chunks).strip()


async def test_rag_real_returns_citations() -> None:
    rag = MiniRAG(CohereClient(Settings()), load_docs(DOCS))
    resp, retrieved = await rag.answer("How long are CI build logs kept?")
    assert "90" in resp.text
    assert any("build-logs" in c.doc_ids for c in resp.citations)
    assert "build-logs" in [d.id for d in retrieved]

"""CohereClient maps SDK responses to our models correctly, with no network.

The SDK client is replaced by a stub returning real SDK response objects, so these
tests pin down our mapping logic without spending trial-key quota.
"""

from typing import Any, cast

import cohere
import pytest
from pydantic import SecretStr

from north_lite.llm import CohereClient, Document, Message
from north_lite.settings import Settings


class StubSDK:
    def __init__(self, response: cohere.V2ChatResponse) -> None:
        self.response = response
        self.kwargs: dict[str, Any] = {}

    async def chat(self, **kwargs: Any) -> cohere.V2ChatResponse:
        self.kwargs = kwargs
        return self.response


def make_response(citations: list[cohere.Citation]) -> cohere.V2ChatResponse:
    return cohere.V2ChatResponse(
        id="r1",
        finish_reason="COMPLETE",
        message=cohere.AssistantMessageResponse(
            content=[
                cohere.ThinkingAssistantMessageResponseContentItem(thinking="let me think"),
                cohere.TextAssistantMessageResponseContentItem(text="Logs are kept 90 days."),
            ],
            citations=citations,
        ),
    )


def cite(
    text: str,
    sources: list[cohere.ToolSource | cohere.DocumentSource],
    type_: cohere.CitationType = "TEXT_CONTENT",
) -> cohere.Citation:
    return cohere.Citation(start=0, end=len(text), text=text, sources=sources, type=type_)


@pytest.fixture
def settings() -> Settings:
    return Settings(backend="cohere", co_api_key=SecretStr("unused"), _env_file=None)  # type: ignore[call-arg]


async def test_maps_text_and_document_citations(settings: Settings) -> None:
    stub = StubSDK(
        make_response(
            [
                cite("90 days", [cohere.DocumentSource(id="build-logs")]),
                cite("think", [cohere.DocumentSource(id="x")], type_="THINKING_CONTENT"),
                cite("no source", []),
            ]
        )
    )
    client = CohereClient(settings, sdk=cast(cohere.AsyncClientV2, stub))

    resp = await client.chat(
        [Message(role="user", content="How long are logs kept?")],
        documents=[Document(id="build-logs", title="Build logs", text="90 days")],
    )

    assert resp.text == "Logs are kept 90 days."  # thinking block dropped
    assert [c.doc_ids for c in resp.citations] == [["build-logs"]]  # only the valid one
    assert stub.kwargs["documents"][0].id == "build-logs"


async def test_no_documents_sends_none(settings: Settings) -> None:
    stub = StubSDK(make_response([]))
    client = CohereClient(settings, sdk=cast(cohere.AsyncClientV2, stub))

    resp = await client.chat([Message(role="user", content="hi")])

    assert resp.citations == []
    assert stub.kwargs["documents"] is None

import pytest

from north_lite.fake import FakeLLMClient
from north_lite.llm import CohereClient, Message, embed_many, make_client
from north_lite.settings import Settings


async def test_embed_many_batches_by_96_and_keeps_order() -> None:
    fake = FakeLLMClient()
    texts = [f"text number {i}" for i in range(250)]

    vectors = await embed_many(fake, texts, "search_document")

    assert fake.embed_calls == [96, 96, 58]
    # Order preserved: each output vector equals embedding its own input alone.
    for i in (0, 95, 96, 249):
        [single] = await fake.embed([texts[i]], "search_document")
        assert vectors[i] == single


async def test_embed_many_caps_concurrency() -> None:
    fake = FakeLLMClient(latency_s=0.01)

    await embed_many(fake, ["x"] * 1000, "search_document", batch_size=10, max_concurrency=3)

    assert len(fake.embed_calls) == 100
    assert fake.max_in_flight == 3


async def test_fake_stream_yields_chunks() -> None:
    chunks = [c async for c in FakeLLMClient().stream_chat([Message(role="user", content="hi")])]
    assert len(chunks) > 1
    assert "".join(chunks).strip() == "fake answer to: hi"


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CO_API_KEY", "test-key")
    monkeypatch.setenv("NL_CHAT_MODEL", "some-model")
    monkeypatch.setenv("NL_TIMEOUT_S", "5")

    s = Settings(_env_file=None)  # type: ignore[call-arg]

    assert s.co_api_key is not None and s.co_api_key.get_secret_value() == "test-key"
    assert "test-key" not in repr(s)  # SecretStr keeps the key out of logs
    assert s.chat_model == "some-model"
    assert s.timeout_s == 5.0


def test_cohere_backend_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CO_API_KEY", raising=False)
    with pytest.raises(ValueError, match="CO_API_KEY"):
        CohereClient(Settings(_env_file=None))  # type: ignore[call-arg]


def test_make_client_fake_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CO_API_KEY", raising=False)
    assert isinstance(make_client(Settings(backend="fake", _env_file=None)), FakeLLMClient)  # type: ignore[call-arg]

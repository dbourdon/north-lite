"""gather vs TaskGroup, cancellation and timeouts, shown on embed_many.

Interview question 8: "asyncio.gather vs TaskGroup: what happens to the other
tasks when one fails?" These tests are the answer, executable.
"""

import asyncio
from collections.abc import Sequence

import pytest

from north_lite.fake import FakeLLMClient
from north_lite.llm import InputType, embed_many


class FailingBatchClient(FakeLLMClient):
    """Batch 0 fails fast; every other batch is slow and records how it ended."""

    def __init__(self) -> None:
        super().__init__()
        self.finished = 0
        self.cancelled = 0

    async def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        if texts[0] == "boom":
            await asyncio.sleep(0.01)
            raise RuntimeError("batch failed")
        try:
            await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise  # never swallow cancellation
        self.finished += 1
        return await super().embed(texts, input_type)


TEXTS = ["boom", "a", "b", "c"]  # batch_size=1 gives 4 batches


async def test_taskgroup_cancels_siblings_when_one_fails() -> None:
    client = FailingBatchClient()

    with pytest.raises(ExceptionGroup) as info:
        await embed_many(client, TEXTS, "search_document", batch_size=1, max_concurrency=4)

    assert info.group_contains(RuntimeError, match="batch failed")
    assert client.cancelled == 3  # the three slow batches were stopped...
    assert client.finished == 0  # ...and none ran to completion


async def test_gather_leaves_siblings_running() -> None:
    # The contrast: gather raises the first error but does NOT cancel the rest.
    client = FailingBatchClient()

    with pytest.raises(RuntimeError):
        await asyncio.gather(*(client.embed([t], "search_document") for t in TEXTS))

    assert client.cancelled == 0
    await asyncio.sleep(0.3)  # the orphaned tasks keep going after we've given up
    assert client.finished == 3


async def test_except_star_handles_the_group() -> None:
    caught: list[str] = []
    try:
        await embed_many(FailingBatchClient(), TEXTS, "search_document", batch_size=1)
    except* RuntimeError as group:
        caught = [str(e) for e in group.exceptions]
    assert caught == ["batch failed"]


async def test_timeout_cancels_everything() -> None:
    slow = FakeLLMClient(latency_s=1.0)

    with pytest.raises(TimeoutError):
        await embed_many(slow, ["x"] * 10, "search_document", batch_size=1, timeout_s=0.05)

    await asyncio.sleep(0)  # let cancellations settle
    assert slow.in_flight == 0  # nothing left running after the deadline

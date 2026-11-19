import random
from collections.abc import Awaitable, Callable
from typing import Any, cast

import cohere
import httpx
import pytest
from cohere.errors import BadRequestError, ServiceUnavailableError, TooManyRequestsError
from pydantic import SecretStr

from north_lite.llm import CohereClient
from north_lite.retry import RetryPolicy, is_retryable, retry_after_s, retry_async
from north_lite.settings import Settings


class Recorder:
    """Stand-in for asyncio.sleep: records delays instead of waiting."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def failing(errors: list[Exception], result: str = "ok") -> Callable[[], Awaitable[str]]:
    """An async callable that raises each error in turn, then returns `result`."""
    calls = iter(errors)

    async def fn() -> str:
        err = next(calls, None)
        if err is not None:
            raise err
        return result

    return fn


async def test_succeeds_after_transient_429s() -> None:
    sleep = Recorder()
    errors: list[Exception] = [TooManyRequestsError(body={}), TooManyRequestsError(body={})]
    policy = RetryPolicy(attempts=4, base_s=1.0, cap_s=10.0)

    result = await retry_async(failing(errors), policy, sleep=sleep, rng=random.Random(0))

    assert result == "ok"
    assert len(sleep.delays) == 2
    # Full jitter: retry n waits somewhere in [0, base * 2^n].
    assert 0 <= sleep.delays[0] <= 1.0
    assert 0 <= sleep.delays[1] <= 2.0


async def test_backoff_is_capped() -> None:
    policy = RetryPolicy(base_s=1.0, cap_s=5.0)
    rng = random.Random(1)
    assert all(policy.backoff(10, rng) <= 5.0 for _ in range(100))


async def test_honours_retry_after_header() -> None:
    sleep = Recorder()
    err = TooManyRequestsError(body={}, headers={"Retry-After": "3"})

    await retry_async(failing([err]), RetryPolicy(cap_s=10), sleep=sleep)

    assert sleep.delays == [3.0]


async def test_gives_up_after_max_attempts() -> None:
    sleep = Recorder()
    errors: list[Exception] = [ServiceUnavailableError(body={}) for _ in range(5)]

    with pytest.raises(ServiceUnavailableError):
        await retry_async(failing(errors), RetryPolicy(attempts=3), sleep=sleep)

    assert len(sleep.delays) == 2  # 3 attempts = 2 waits


async def test_does_not_retry_client_errors() -> None:
    sleep = Recorder()

    with pytest.raises(BadRequestError):
        await retry_async(failing([BadRequestError(body={})]), sleep=sleep)

    assert sleep.delays == []  # a bad request will fail the same way every time


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (TooManyRequestsError(body={}), True),
        (ServiceUnavailableError(body={}), True),
        (BadRequestError(body={}), False),
        (httpx.ReadTimeout("slow"), True),
        (httpx.ConnectError("down"), True),
        (ValueError("bug"), False),
    ],
    ids=["429", "503", "400", "read-timeout", "connect-error", "value-error"],
)
def test_is_retryable(exc: Exception, expected: bool) -> None:
    assert is_retryable(exc) is expected


def test_retry_after_parsing() -> None:
    assert retry_after_s(TooManyRequestsError(body={}, headers={"retry-after": "2.5"})) == 2.5
    assert retry_after_s(TooManyRequestsError(body={}, headers={"retry-after": "soon"})) is None
    assert retry_after_s(TooManyRequestsError(body={})) is None


class FlakyRerankSDK:
    """SDK stub: rerank fails with 429 `failures` times, then succeeds."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls: list[dict[str, Any]] = []

    async def rerank(self, **kwargs: Any) -> cohere.V2RerankResponse:
        self.calls.append(kwargs)
        if len(self.calls) <= self.failures:
            raise TooManyRequestsError(body={}, headers={"retry-after": "0"})
        return cohere.V2RerankResponse(
            results=[cohere.V2RerankResponseResultsItem(index=0, relevance_score=0.9)]
        )


async def test_cohere_client_retries_and_disables_sdk_retries() -> None:
    sdk = FlakyRerankSDK(failures=2)
    settings = Settings(co_api_key=SecretStr("unused"), max_attempts=3, _env_file=None)  # type: ignore[call-arg]
    client = CohereClient(settings, sdk=cast(cohere.AsyncClientV2, sdk))

    hits = await client.rerank("q", ["a"], top_n=1)

    assert [h.index for h in hits] == [0]
    assert len(sdk.calls) == 3
    # Our layer owns retries, so every SDK call must have its own retries off.
    assert all(c["request_options"] == {"max_retries": 0} for c in sdk.calls)

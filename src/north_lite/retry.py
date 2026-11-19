"""Retry with exponential backoff and full jitter.

Why our own layer instead of the SDK's built-in retries (which we switch off):
one explicit policy for every backend, unit-testable without the network, and no
"SDK retries x our retries" multiplication of load during an outage.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from cohere.core.api_error import ApiError

RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 4  # total tries, including the first
    base_s: float = 0.5
    cap_s: float = 8.0

    def backoff(self, retry: int, rng: random.Random) -> float:
        # "Full jitter": uniform in [0, min(cap, base * 2^n)]. Randomising the whole
        # interval spreads clients out, so they don't all retry in lock-step and
        # hit the rate limit again together (the thundering herd).
        return rng.uniform(0, min(self.cap_s, self.base_s * 2**retry))


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, ApiError):
        return exc.status_code in RETRYABLE_STATUS
    # Timeouts and dropped connections are transient; a malformed request is not.
    return isinstance(exc, httpx.TimeoutException | httpx.TransportError)


def retry_after_s(exc: BaseException) -> float | None:
    """Seconds the server asked us to wait (Retry-After), if it said so."""
    if not isinstance(exc, ApiError) or not exc.headers:
        return None
    headers = {k.lower(): v for k, v in exc.headers.items()}
    try:
        return max(0.0, float(headers["retry-after"]))
    except (KeyError, ValueError):
        return None  # missing, or an HTTP-date we don't bother parsing


async def retry_async[T](
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy = RetryPolicy(),
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: random.Random | None = None,
) -> T:
    """Call `fn` until it succeeds, a non-retryable error occurs, or attempts run out.

    `sleep` and `rng` are injectable so tests run instantly and deterministically.
    asyncio.CancelledError is a BaseException, not Exception, so a cancelled caller
    (timeout, shutdown, TaskGroup sibling failure) stops retrying immediately.
    """
    rng = rng or random.Random()
    for retry in range(policy.attempts):
        try:
            return await fn()
        except Exception as exc:
            if retry == policy.attempts - 1 or not is_retryable(exc):
                raise
            hinted = retry_after_s(exc)
            delay = min(hinted, policy.cap_s) if hinted is not None else policy.backoff(retry, rng)
            await sleep(delay)
    raise AssertionError("unreachable")  # loop always returns or raises

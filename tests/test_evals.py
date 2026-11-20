import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from north_lite.app import create_app
from north_lite.evals import (
    CaseResult,
    EvalCase,
    LLMSimulator,
    ScriptedSimulator,
    Turn,
    _parse_verdict,
    format_report,
    load_cases,
    run_case,
    run_eval,
    summarize,
    wait_until_ready,
)
from north_lite.fake import FakeLLMClient
from north_lite.llm import ChatResponse, Document, Message
from north_lite.rag import load_docs
from north_lite.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
CASES = load_cases(ROOT / "evals" / "handbook.jsonl")


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(Settings(backend="fake", _env_file=None))  # type: ignore[call-arg]
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield c


def test_eval_set_is_well_formed() -> None:
    doc_ids = {d.id for d in load_docs(ROOT / "data" / "handbook.json")}
    assert len(CASES) == 20
    assert len({c.id for c in CASES}) == 20
    for case in CASES:
        assert set(case.expected_doc_ids) <= doc_ids, case.id
        assert case.answer_contains, case.id


async def test_scripted_eval_runs_end_to_end(client: httpx.AsyncClient) -> None:
    report = await run_eval(client, ScriptedSimulator(), CASES)

    assert report.cases == 20
    for rate in (report.retrieval_hit_rate, report.citation_coverage, report.answer_accuracy):
        assert 0.0 <= rate <= 1.0
    assert 1.0 <= report.avg_turns <= 2.0
    # The fake backend cites only docs it retrieved, so precision is never "wrong doc".
    for r in report.results:
        for t in r.turns:
            assert set(t.cited_doc_ids) <= set(t.doc_ids)


async def test_unsatisfied_user_follows_up(client: httpx.AsyncClient) -> None:
    # An expectation the fake answer can't meet forces the follow-up turn.
    case = EvalCase(
        id="x", question=CASES[0].question, expected_doc_ids=["deploy-windows"],
        answer_contains=["no such phrase anywhere"],
    )  # fmt: skip
    result = await run_case(client, ScriptedSimulator(), case, max_turns=2)
    assert len(result.turns) == 2
    assert result.satisfied is False
    assert result.turns[1].user.startswith("That didn't answer it.")


async def test_failing_case_is_recorded_not_fatal(client: httpx.AsyncClient) -> None:
    class Boom(ScriptedSimulator):
        async def opening(self, case: EvalCase) -> str:
            if case.id == "q01":
                raise RuntimeError("429 from the simulator's LLM")
            return await super().opening(case)

    report = await run_eval(client, Boom(), CASES[:3])

    assert report.cases == 3 and report.errors == 1
    [failed] = [r for r in report.results if r.error]
    assert failed.case_id == "q01" and "429" in (failed.error or "")
    assert not failed.retrieval_hit  # an error counts as a miss, never a pass


async def test_wait_until_ready_returns_once_ready(client: httpx.AsyncClient) -> None:
    assert await wait_until_ready(client, timeout_s=1, interval_s=0.01)


async def test_wait_until_ready_times_out_when_not_ready() -> None:
    app = create_app(Settings(backend="fake", _env_file=None))  # type: ignore[call-arg]
    transport = httpx.ASGITransport(app=app)  # lifespan not run: /readyz stays 503
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        assert not await wait_until_ready(c, timeout_s=0.05, interval_s=0.01)


async def test_wait_until_ready_tolerates_nothing_listening() -> None:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:9") as c:  # discard port
        assert not await wait_until_ready(c, timeout_s=0.05, interval_s=0.01)


async def test_pacer_spaces_out_case_starts(client: httpx.AsyncClient) -> None:
    import time

    start = time.monotonic()
    await run_eval(client, ScriptedSimulator(), CASES[:3], concurrency=3, pace_s=0.1)
    assert time.monotonic() - start >= 0.2  # starts at ~0, 0.1, 0.2


def make_result(hit: bool, cited: list[str], expected: str = "a") -> CaseResult:
    turn = Turn(user="q", answer="a", doc_ids=[expected] if hit else ["z"], cited_doc_ids=cited)
    exp = {expected}
    return CaseResult(
        case_id="c", turns=[turn], satisfied=hit,
        retrieval_hit=hit, cited_expected=bool(exp & set(cited)),
        citation_precision=len(exp & set(cited)) / len(cited) if cited else None,
        answer_correct=hit,
    )  # fmt: skip


def test_summary_math_and_gate() -> None:
    results = [
        make_result(True, ["a"]),  # precision 1.0
        make_result(True, ["a", "b"]),  # precision 0.5
        make_result(True, []),  # no citations: excluded from precision
        make_result(False, ["z"]),  # precision 0.0
    ]
    report = summarize(results)
    assert report.retrieval_hit_rate == 0.75
    assert report.citation_coverage == 0.5
    assert report.citation_precision == pytest.approx(0.5)
    assert report.passes(0.75) and not report.passes(0.85)
    assert format_report(report, 0.85).endswith("FAIL")


@pytest.mark.parametrize(
    ("raw", "satisfied", "follow_up"),
    [
        ('{"satisfied": true}', True, None),
        ('```json\n{"satisfied": false, "next_message": "what about weekends?"}\n```', False,
         "what about weekends?"),
        ('Sure! {"satisfied": false, "next_message": "and SEV1?"} hope that helps', False,
         "and SEV1?"),
        ("I think so", False, None),  # no JSON: stop instead of looping
        ('{"satisfied": "maybe"}', False, None),  # wrong type
    ],
    ids=["plain", "fenced", "prose-wrapped", "no-json", "bad-type"],
)  # fmt: skip
def test_parse_verdict(raw: str, satisfied: bool, follow_up: str | None) -> None:
    verdict = _parse_verdict(raw)
    assert verdict.satisfied is satisfied
    assert verdict.next_message == follow_up


class ScriptedLLM(FakeLLMClient):
    """Returns canned replies in order, and records the prompts it was sent."""

    def __init__(self, replies: list[str]) -> None:
        super().__init__()
        self.replies = replies
        self.prompts: list[str] = []

    async def chat(
        self, messages: Sequence[Message], documents: Sequence[Document] = ()
    ) -> ChatResponse:
        self.prompts.append(messages[-1].content)
        return ChatResponse(text=self.replies.pop(0))


async def test_llm_simulator_uses_persona_and_hides_expected_answer() -> None:
    reply = {"satisfied": False, "next_message": "ok but who approves it?"}
    llm = ScriptedLLM(['"hey, can I deploy fridays?"', json.dumps(reply)])
    sim = LLMSimulator(llm)
    case = CASES[0]

    opening = await sim.opening(case)
    # An answer that lacks the expected fact ("on-call lead"):
    verdict = await sim.react(case, "Friday deploys need extra approval.")

    assert opening == "hey, can I deploy fridays?"  # quotes stripped
    assert not verdict.satisfied
    assert verdict.next_message == "ok but who approves it?"
    # The simulator must never be told the expected answer, or its judgement is no
    # longer independent of the keyword check.
    assert all(fact not in p for p in llm.prompts for fact in case.answer_contains)

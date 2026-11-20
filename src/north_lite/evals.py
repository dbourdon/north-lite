"""Eval harness: a simulated user talks to the running service over HTTP.

The simulator (scripted offline, or an LLM with a persona) opens with a question,
reads the answer, and either stops satisfied or sends a follow-up. The harness
scores each conversation on retrieval, citations and answer correctness, and the
run passes or fails against a retrieval hit-rate threshold.

Talking HTTP (not calling MiniRAG directly) means the same harness can test a
local server, a kind deployment or any other environment.
"""

import asyncio
import json
import re
from collections.abc import Sequence
from pathlib import Path
from statistics import mean
from typing import Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from north_lite.llm import LLMClient, Message


class EvalCase(BaseModel):
    id: str
    question: str
    expected_doc_ids: list[str] = Field(min_length=1)
    answer_contains: list[str] = Field(default_factory=list)  # case-insensitive


class Turn(BaseModel):
    user: str
    answer: str
    doc_ids: list[str]  # what the service retrieved
    cited_doc_ids: list[str]  # what the answer actually cites


class CaseResult(BaseModel):
    case_id: str
    turns: list[Turn]
    satisfied: bool  # the simulated user's own judgement
    # Scored on the first turn: that is what the user saw first.
    retrieval_hit: bool  # an expected doc was retrieved
    cited_expected: bool  # the answer cites an expected doc
    citation_precision: float | None  # share of cited docs that are expected; None = no citations
    answer_correct: bool  # every answer_contains string is present
    error: str | None = None  # the case couldn't run (e.g. 429, 5xx); scored as a miss

    @classmethod
    def failed(cls, case_id: str, error: str) -> "CaseResult":
        return cls(
            case_id=case_id, turns=[], satisfied=False, retrieval_hit=False,
            cited_expected=False, citation_precision=None, answer_correct=False, error=error,
        )  # fmt: skip


class Report(BaseModel):
    cases: int
    errors: int
    retrieval_hit_rate: float
    citation_coverage: float
    citation_precision: float | None
    answer_accuracy: float
    satisfied_rate: float
    avg_turns: float
    results: list[CaseResult]

    def passes(self, min_hit_rate: float) -> bool:
        # Errored cases count as misses in the rate, so errors can't make a run pass.
        return self.retrieval_hit_rate >= min_hit_rate


def load_cases(path: Path) -> list[EvalCase]:
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    return [EvalCase.model_validate_json(ln) for ln in lines]


# ------------------------------------------------------------------ simulators


class Verdict(BaseModel):
    satisfied: bool
    next_message: str | None = None


class Simulator(Protocol):
    async def opening(self, case: EvalCase) -> str: ...

    async def react(self, case: EvalCase, answer: str) -> Verdict: ...


def contains_all(text: str, needles: Sequence[str]) -> bool:
    low = text.lower()
    return all(n.lower() in low for n in needles)


class ScriptedSimulator:
    """Deterministic user for CI: asks the case question verbatim, is satisfied
    when the expected facts appear, otherwise rephrases once."""

    async def opening(self, case: EvalCase) -> str:
        return case.question

    async def react(self, case: EvalCase, answer: str) -> Verdict:
        if contains_all(answer, case.answer_contains):
            return Verdict(satisfied=True)
        return Verdict(satisfied=False, next_message=f"That didn't answer it. {case.question}")


PERSONA = (
    "You are role-playing a new engineer in their first week at the company, "
    "chatting with the internal help assistant. You are busy and write short, "
    "informal messages, like a real chat message. Never mention that you are simulated."
)


def _parse_verdict(raw: str) -> Verdict:
    """Pull the first JSON object out of a model reply (tolerates prose and ``` fences)."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return Verdict.model_validate(json.loads(match.group(0)))
        except (json.JSONDecodeError, ValidationError):
            pass
    # Unparseable: stop the conversation rather than loop on garbage.
    return Verdict(satisfied=False, next_message=None)


class LLMSimulator:
    """A user played by an LLM. It knows only its goal, not the expected answer,
    so its satisfaction is an independent signal from the keyword checks."""

    def __init__(self, llm: LLMClient, persona: str = PERSONA) -> None:
        self._llm = llm
        self._persona = persona

    async def opening(self, case: EvalCase) -> str:
        prompt = (
            f"Your goal: find out the answer to: {case.question!r}\n"
            "Write the first message you would send. Use your own words, not the goal "
            "verbatim. Reply with the message only."
        )
        resp = await self._llm.chat(
            [Message(role="system", content=self._persona), Message(role="user", content=prompt)]
        )
        return resp.text.strip().strip('"') or case.question

    async def react(self, case: EvalCase, answer: str) -> Verdict:
        prompt = (
            f"Your goal was to find out: {case.question!r}\n"
            f"The assistant replied:\n---\n{answer}\n---\n"
            "Did that fully answer your goal? Reply with JSON only: "
            '{"satisfied": true} or {"satisfied": false, "next_message": "<your follow-up>"}'
        )
        resp = await self._llm.chat(
            [Message(role="system", content=self._persona), Message(role="user", content=prompt)]
        )
        return _parse_verdict(resp.text)


# --------------------------------------------------------------------- runner


async def run_case(
    client: httpx.AsyncClient, sim: Simulator, case: EvalCase, max_turns: int = 2
) -> CaseResult:
    history: list[dict[str, str]] = []
    turns: list[Turn] = []
    satisfied = False
    message = await sim.opening(case)
    for _ in range(max_turns):
        history.append({"role": "user", "content": message})
        resp = await client.post("/chat", json={"messages": history, "rag": True})
        resp.raise_for_status()
        data = resp.json()
        cited = sorted({i for c in data["citations"] for i in c["doc_ids"]})
        turns.append(
            Turn(user=message, answer=data["text"], doc_ids=data["doc_ids"], cited_doc_ids=cited)
        )
        history.append({"role": "assistant", "content": data["text"]})
        verdict = await sim.react(case, data["text"])
        satisfied = verdict.satisfied
        if satisfied or not verdict.next_message:
            break
        message = verdict.next_message

    first = turns[0]
    expected = set(case.expected_doc_ids)
    first_cited = set(first.cited_doc_ids)
    return CaseResult(
        case_id=case.id,
        turns=turns,
        satisfied=satisfied,
        retrieval_hit=bool(expected & set(first.doc_ids)),
        cited_expected=bool(expected & first_cited),
        citation_precision=(
            len(expected & first_cited) / len(first_cited) if first_cited else None
        ),
        answer_correct=contains_all(first.answer, case.answer_contains),
    )


def summarize(results: Sequence[CaseResult]) -> Report:
    n = len(results)

    def rate(flags: Sequence[bool]) -> float:
        return sum(flags) / n if n else 0.0

    precisions = [r.citation_precision for r in results if r.citation_precision is not None]
    ran = [r for r in results if r.error is None]
    return Report(
        cases=n,
        errors=n - len(ran),
        retrieval_hit_rate=rate([r.retrieval_hit for r in results]),
        citation_coverage=rate([r.cited_expected for r in results]),
        citation_precision=mean(precisions) if precisions else None,
        answer_accuracy=rate([r.answer_correct for r in results]),
        satisfied_rate=rate([r.satisfied for r in results]),
        avg_turns=mean(len(r.turns) for r in ran) if ran else 0.0,
        results=list(results),
    )


async def wait_until_ready(
    client: httpx.AsyncClient, timeout_s: float, interval_s: float = 0.5
) -> bool:
    """Poll GET /readyz until it returns 200 or `timeout_s` passes.

    Lets `serve & eval` (or a fresh port-forward) work without a hand-tuned sleep:
    the service only reports ready once its search index is built.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        try:
            if (await client.get("/readyz")).status_code == 200:
                return True
        except httpx.TransportError:
            pass  # not listening yet
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(interval_s)


class Pacer:
    """At most one start every `interval_s` seconds, across all tasks.

    A rate limit like "20 calls per minute" can't be fixed by retries alone: short
    backoffs give up before the window resets. Pacing keeps the run under the limit.
    """

    def __init__(self, interval_s: float) -> None:
        self._interval = interval_s
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if self._interval <= 0:
            return
        async with self._lock:
            loop = asyncio.get_running_loop()
            delay = self._next - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next = loop.time() + self._interval


async def run_eval(
    client: httpx.AsyncClient,
    sim: Simulator,
    cases: Sequence[EvalCase],
    *,
    max_turns: int = 2,
    concurrency: int = 2,
    pace_s: float = 0.0,
) -> Report:
    sem = asyncio.Semaphore(concurrency)
    pacer = Pacer(pace_s)

    async def one(case: EvalCase) -> CaseResult:
        async with sem:
            await pacer.wait()
            try:
                return await run_case(client, sim, case, max_turns)
            except Exception as exc:  # one bad case must not discard the whole run
                return CaseResult.failed(case.id, f"{type(exc).__name__}: {str(exc)[:200]}")

    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(one(c)) for c in cases]
    return summarize([t.result() for t in tasks])


def format_report(report: Report, min_hit_rate: float) -> str:
    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.0%}"

    rows = [
        ("cases", f"{report.cases} ({report.errors} errored)"),
        ("retrieval hit rate", f"{pct(report.retrieval_hit_rate)}  (gate >= {min_hit_rate:.0%})"),
        ("citation coverage", pct(report.citation_coverage)),
        ("citation precision", pct(report.citation_precision)),
        ("answer accuracy", pct(report.answer_accuracy)),
        ("user satisfied", pct(report.satisfied_rate)),
        ("avg turns", f"{report.avg_turns:.2f}"),
    ]
    lines = [f"{k:<20} {v}" for k, v in rows]
    misses = [r.case_id for r in report.results if not r.retrieval_hit and r.error is None]
    if misses:
        lines.append(f"{'retrieval misses':<20} {', '.join(misses)}")
    for r in report.results:
        if r.error:
            lines.append(f"{'error ' + r.case_id:<20} {r.error[:90]}")
    lines.append("PASS" if report.passes(min_hit_rate) else "FAIL")
    return "\n".join(lines)

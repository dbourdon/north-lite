"""Day 1 demo CLI.

uv run north-lite stream "Explain Helm in one sentence."
uv run north-lite rag "How long are build logs kept?"
uv run north-lite eval --base-url http://127.0.0.1:8000 [--simulator llm]
"""

import argparse
import asyncio
import sys
from pathlib import Path

from north_lite.llm import Message, make_client
from north_lite.rag import MiniRAG, load_docs
from north_lite.settings import Settings

DEFAULT_DOCS = Path(__file__).resolve().parents[2] / "data" / "handbook.json"


async def _stream(prompt: str) -> None:
    client = make_client(Settings())
    async for chunk in client.stream_chat([Message(role="user", content=prompt)]):
        sys.stdout.write(chunk)
        sys.stdout.flush()
    print()


async def _rag(question: str, docs_path: Path) -> None:
    rag = MiniRAG(make_client(Settings()), load_docs(docs_path))
    resp, docs = await rag.answer(question)
    print(resp.text, "\n")
    for c in resp.citations:
        print(f"  [{c.start}:{c.end}] {c.text!r} <- {', '.join(c.doc_ids)}")
    print("\nretrieved:", ", ".join(d.id for d in docs))


async def _eval(args: argparse.Namespace) -> int:
    import httpx

    from north_lite.evals import (
        LLMSimulator,
        ScriptedSimulator,
        Simulator,
        format_report,
        load_cases,
    )
    from north_lite.evals import run_eval as run

    cases = load_cases(args.cases)
    if args.limit:
        cases = cases[: args.limit]
    # The LLM simulator always uses the real backend: a fake user tests nothing.
    sim: Simulator = (
        LLMSimulator(make_client(Settings(backend="cohere")))
        if args.simulator == "llm"
        else ScriptedSimulator()
    )
    async with httpx.AsyncClient(base_url=args.base_url, timeout=120) as client:
        report = await run(
            client,
            sim,
            cases,
            max_turns=args.max_turns,
            concurrency=args.concurrency,
            pace_s=args.pace_s,
        )
    print(format_report(report, args.min_hit_rate))
    if args.out:
        args.out.write_text(report.model_dump_json(indent=2))
        print(f"report written to {args.out}")
    return 0 if report.passes(args.min_hit_rate) else 1


def main() -> None:
    p = argparse.ArgumentParser(prog="north-lite")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stream", help="stream a chat reply")
    s.add_argument("prompt")
    r = sub.add_parser("rag", help="answer from the handbook with citations")
    r.add_argument("question")
    r.add_argument("--docs", type=Path, default=DEFAULT_DOCS)
    v = sub.add_parser("serve", help="run the HTTP service")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8000)
    e = sub.add_parser("eval", help="simulated-user eval against a running service")
    e.add_argument("--base-url", default="http://127.0.0.1:8000")
    e.add_argument(
        "--cases", type=Path, default=DEFAULT_DOCS.parents[1] / "evals" / "handbook.jsonl"
    )
    e.add_argument("--simulator", choices=["scripted", "llm"], default="scripted")
    e.add_argument("--max-turns", type=int, default=2)
    e.add_argument("--concurrency", type=int, default=2)
    e.add_argument(
        "--pace-s",
        type=float,
        default=0.0,
        help="min seconds between case starts (~16 keeps a live run under 20 calls/min)",
    )
    e.add_argument("--limit", type=int, default=0, help="only the first N cases (0 = all)")
    e.add_argument("--min-hit-rate", type=float, default=0.85)
    e.add_argument("--out", type=Path, help="write the full JSON report here")
    args = p.parse_args()
    if args.cmd == "stream":
        asyncio.run(_stream(args.prompt))
    elif args.cmd == "rag":
        asyncio.run(_rag(args.question, args.docs))
    elif args.cmd == "eval":
        sys.exit(asyncio.run(_eval(args)))
    else:
        import uvicorn

        uvicorn.run("north_lite.app:create_app", factory=True, host=args.host, port=args.port)

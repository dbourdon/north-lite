"""Day 1 demo CLI.

uv run north-lite stream "Explain Helm in one sentence."
uv run north-lite rag "How long are build logs kept?"
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
    args = p.parse_args()
    if args.cmd == "stream":
        asyncio.run(_stream(args.prompt))
    elif args.cmd == "rag":
        asyncio.run(_rag(args.question, args.docs))
    else:
        import uvicorn

        uvicorn.run("north_lite.app:create_app", factory=True, host=args.host, port=args.port)

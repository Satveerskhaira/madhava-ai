"""madhava: ask the core agent, replay goldens, serve the core over A2A or MCP.

    madhava ask RX-1001 "Why was this rejected, and what would it cost if approved?"
    madhava ask RX-1007 "Why did this reject?" --trace
    madhava ask RX-1001 "Why was this rejected?" --workflow     # as an ADK workflow graph
    madhava serve --port 8100                                  # core agent over A2A
    madhava mcp                                                # toolbox + core over MCP (stdio)
    madhava eval | validate | cards | gaps
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import warnings
from datetime import date
from pathlib import Path

from rulegraph import Settings

GOLDENS = Path(__file__).resolve().parents[2] / "goldens" / "goldens.yaml"


def _settings(args: argparse.Namespace) -> Settings:
    s = Settings()
    if getattr(args, "today", None):
        s.today = date.fromisoformat(args.today)
    return s


async def _ask(args: argparse.Namespace) -> int:
    from .system import build
    system = build(_settings(args), model=args.model)
    if args.workflow:
        from google.adk.runners import InMemoryRunner
        from rulegraph.adapters.adk import build_workflow
        runner = InMemoryRunner(node=build_workflow(system.core), app_name="madhava")
        session = await runner.session_service.create_session(app_name="madhava", user_id="cli")
        request = {"record_id": args.claim, "question": args.question, "session_id": args.session,
                   "rules_mode": args.rules_mode, "as_of": args.as_of}
        result = None
        async for ev in runner.run_async(user_id="cli", session_id=session.id, state_delta={"request": request}):
            if isinstance(ev.output, dict) and "answer" in ev.output:
                result = ev.output
        print(json.dumps(result, indent=2, default=str) if args.json else (result or {}).get("answer", "no answer"))
        return 0
    exp = await system.core.ask(args.question, args.claim, session_id=args.session, rules_mode=args.rules_mode,
                                as_of=date.fromisoformat(args.as_of) if args.as_of else None)
    if args.json:
        print(exp.model_dump_json(indent=2))
        return 0
    print(exp.answer)
    if args.trace:
        print("\nTrace:")
        for line in exp.trace:
            print("  " + line)
    return 0


async def _eval(args: argparse.Namespace) -> int:
    from rulegraph.evaluation import run_goldens
    from .system import build
    system = build(_settings(args))
    results = await run_goldens(Path(args.goldens), planner=system.core, agents=system.agents)
    passed = sum(r.passed for r in results)
    for r in results:
        print(f"{'PASS' if r.passed else 'FAIL'}  {r.record}  {r.question}")
        for p in r.problems:
            print(f"        - {p}")
    print(f"\nMatch rate: {passed}/{len(results)} ({100 * passed / len(results):.0f}%)")
    return 0 if passed == len(results) else 1


async def _serve(args: argparse.Namespace) -> int:
    import uvicorn
    from rulegraph.adapters.adk import a2a_app
    from .system import build
    system = build(_settings(args), model=args.model)
    app = a2a_app(system.core, host=args.host, port=args.port)
    print(f"core agent: http://{args.host}:{args.port}/.well-known/agent-card.json")
    await uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level="warning")).serve()
    return 0


def main(argv: list[str] | None = None) -> int:
    warnings.filterwarnings("ignore")
    p = argparse.ArgumentParser(prog="madhava", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--today", help="pin today's date (YYYY-MM-DD) for current-rules mode")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask", help="ask the core agent about a claim")
    a.add_argument("claim", nargs="?", help="claim id; omit for a knowledge question")
    a.add_argument("question")
    a.add_argument("--rules-mode", default="as_decided", choices=["as_decided", "current", "as_of"])
    a.add_argument("--as-of")
    a.add_argument("--session")
    a.add_argument("--model", help="ADK model name, for example gemini-2.5-flash; default is deterministic")
    a.add_argument("--workflow", action="store_true", help="run the core planner as an ADK workflow graph")
    a.add_argument("--json", action="store_true")
    a.add_argument("--trace", action="store_true")
    e = sub.add_parser("eval", help="replay core goldens")
    e.add_argument("--goldens", default=str(GOLDENS))
    sub.add_parser("validate", help="CI checks for the knowledge pack and core procedures")
    sub.add_parser("cards", help="print the core agent card")
    s = sub.add_parser("serve", help="serve the core agent over A2A (needs rulegraph[adk])")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8100)
    s.add_argument("--model")
    m = sub.add_parser("mcp", help="serve the toolbox and the core over MCP (needs rulegraph[mcp])")
    m.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])
    m.add_argument("--host", default="127.0.0.1")
    m.add_argument("--port", type=int, default=8200)
    sub.add_parser("gaps", help="show logged coverage gaps (SME backlog)")
    args = p.parse_args(argv)

    if args.cmd == "ask":
        return asyncio.run(_ask(args))
    if args.cmd == "eval":
        return asyncio.run(_eval(args))
    if args.cmd == "serve":
        return asyncio.run(_serve(args))
    from .system import build
    system = build(_settings(args))
    if args.cmd == "validate":
        from rulegraph.rules.validate import validate
        issues = validate(system.knowledge)
        for i in issues:
            print("ISSUE", i)
        print(f"core + {len(system.agents)} domain agents, {len(system.knowledge.rules.rules)} rule versions: "
              f"{'OK' if not issues else f'{len(issues)} issues'}")
        return 1 if issues else 0
    if args.cmd == "cards":
        from rulegraph.agent.cards import agent_card
        print(json.dumps(agent_card(system.core), indent=2))
        return 0
    if args.cmd == "mcp":
        from rulegraph.adapters.mcp import build_mcp_server
        server = build_mcp_server(system.toolbox, system.core, name="madhava", record_noun="claim",
                                  reference_noun="benefit")
        if args.transport == "stdio":
            server.run("stdio")
        else:
            server.run("streamable-http", host=args.host, port=args.port)
        return 0
    if args.cmd == "gaps":
        d = system.settings.episodic_dir
        if not d or not (d / "gaps.jsonl").exists():
            print("No gap log. Set MADHAVA_EPISODIC_DIR to keep episodic memory on disk.")
        else:
            print((d / "gaps.jsonl").read_text())
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

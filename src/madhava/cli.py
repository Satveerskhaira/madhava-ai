"""Command line: ask questions, replay goldens, validate knowledge, serve agents and tools."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import warnings
from datetime import date

from .settings import Settings


def _settings(args: argparse.Namespace) -> Settings:
    s = Settings()
    if getattr(args, "today", None):
        s.today = date.fromisoformat(args.today)
    return s


def _a2a_urls(args: argparse.Namespace, settings: Settings) -> dict[str, str] | None:
    if not getattr(args, "a2a_base_port", None):
        return None
    from .knowledge import Knowledge
    names = list(Knowledge.load(settings).taxonomy.agents)
    return {n: f"http://{args.a2a_host}:{args.a2a_base_port + i}" for i, n in enumerate(names)}


async def _ask(args: argparse.Namespace) -> int:
    from .app import build_system
    settings = _settings(args)
    system = build_system(settings, model=args.model, a2a_urls=_a2a_urls(args, settings))
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    if args.workflow:
        from google.adk.runners import InMemoryRunner
        from .adk.workflow import build_workflow
        runner = InMemoryRunner(node=build_workflow(system.planner), app_name="madhava")
        session = await runner.session_service.create_session(app_name="madhava", user_id="cli")
        request = {"claim_id": args.claim, "question": args.question, "session_id": args.session,
                   "rules_mode": args.rules_mode, "as_of": args.as_of}
        result = None
        async for ev in runner.run_async(user_id="cli", session_id=session.id, state_delta={"request": request}):
            if isinstance(ev.output, dict) and "answer" in ev.output:
                result = ev.output
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(result["answer"] if result else "no answer")
        return 0
    exp = await system.planner.ask(args.question, args.claim, session_id=args.session,
                                   rules_mode=args.rules_mode, as_of=as_of)
    if args.json:
        print(exp.model_dump_json(indent=2))
    else:
        print(exp.answer)
        if args.trace:
            print("\nTrace:")
            for line in exp.trace:
                print("  " + line)
    return 0


async def _eval(args: argparse.Namespace) -> int:
    from .app import build_system
    from .evaluation import run_goldens
    results = await run_goldens(build_system(_settings(args)))
    passed = sum(r.passed for r in results)
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        print(f"{mark}  {r.claim}  {r.question}")
        for p in r.problems:
            print(f"        - {p}")
    print(f"\nMatch rate: {passed}/{len(results)} ({100 * passed / len(results):.0f}%)")
    return 0 if passed == len(results) else 1


def _validate(args: argparse.Namespace) -> int:
    from .knowledge import Knowledge
    from .rules.validate import validate
    kn = Knowledge.load(_settings(args))
    issues = validate(kn)
    for i in issues:
        print("ISSUE", i)
    print(f"{len(kn.rules.rules)} rule versions, {len(kn.rules.domains)} domains, "
          f"{len(kn.catalog.fields)} catalog fields, {len(kn.taxonomy.concepts)} concepts, "
          f"policy graph {len(kn.graph.nodes)} nodes / {kn.graph.edge_count} edges: "
          f"{'OK' if not issues else f'{len(issues)} issues'}")
    return 1 if issues else 0


def _cards(args: argparse.Namespace) -> int:
    from .app import build_system
    system = build_system(_settings(args))
    names = [args.agent] if args.agent else system.registry.names()
    print(json.dumps([system.registry.card(n) for n in names], indent=2))
    return 0


async def _serve_agents(args: argparse.Namespace) -> int:
    import uvicorn
    from .adk.a2a import build_a2a_app
    from .app import build_system
    system = build_system(_settings(args))
    names = [args.agent] if args.agent else system.registry.names()
    servers = []
    for i, name in enumerate(names):
        port = args.port if args.agent else args.base_port + i
        app = build_a2a_app(system.registry, name, host=args.host, port=port)
        servers.append(uvicorn.Server(uvicorn.Config(app, host=args.host, port=port, log_level="warning")))
        print(f"{name}: http://{args.host}:{port}/.well-known/agent-card.json")
    await asyncio.gather(*(s.serve() for s in servers))
    return 0


def _mcp(args: argparse.Namespace) -> int:
    from .app import build_system
    from .tools.mcp_server import build_mcp_server
    server = build_mcp_server(build_system(_settings(args)))
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=args.host, port=args.port)
    return 0


def _export(args: argparse.Namespace) -> int:
    from .knowledge import Knowledge
    kn = Knowledge.load(_settings(args))
    doc = kn.taxonomy.to_jsonld() if args.what == "taxonomy" else kn.ontology.to_jsonld()
    print(json.dumps(doc, indent=2))
    return 0


def _gaps(args: argparse.Namespace) -> int:
    settings = _settings(args)
    if not settings.episodic_dir or not (settings.episodic_dir / "gaps.jsonl").exists():
        print("No gap log. Set MADHAVA_EPISODIC_DIR to keep episodic memory on disk.")
        return 0
    print((settings.episodic_dir / "gaps.jsonl").read_text())
    return 0


def main(argv: list[str] | None = None) -> int:
    warnings.filterwarnings("ignore")
    p = argparse.ArgumentParser(prog="madhava", description=__doc__)
    p.add_argument("--today", help="pin today's date (YYYY-MM-DD) for current-rules mode")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("ask", help="ask a question about a claim")
    a.add_argument("claim", nargs="?", help="claim id; omit for a knowledge question")
    a.add_argument("question")
    a.add_argument("--rules-mode", default="as_adjudicated", choices=["as_adjudicated", "current", "as_of"])
    a.add_argument("--as-of")
    a.add_argument("--session")
    a.add_argument("--model", help="ADK model name (for example gemini-2.5-flash); default is deterministic")
    a.add_argument("--workflow", action="store_true", help="run the planner as the ADK workflow graph")
    a.add_argument("--a2a-base-port", type=int, help="call domain agents over A2A at consecutive ports")
    a.add_argument("--a2a-host", default="127.0.0.1")
    a.add_argument("--json", action="store_true")
    a.add_argument("--trace", action="store_true")

    sub.add_parser("eval", help="replay golden claims and report the match rate")
    sub.add_parser("validate", help="CI checks for rules, catalog, taxonomy and releases")
    c = sub.add_parser("cards", help="print A2A agent cards")
    c.add_argument("--agent")

    s = sub.add_parser("serve-agents", help="serve domain agents over A2A")
    s.add_argument("--agent", help="serve only this agent")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8100)
    s.add_argument("--base-port", type=int, default=8101)

    m = sub.add_parser("mcp", help="serve the toolbox over MCP")
    m.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])
    m.add_argument("--host", default="127.0.0.1")
    m.add_argument("--port", type=int, default=8200)

    e = sub.add_parser("export", help="export the taxonomy or ontology as JSON-LD")
    e.add_argument("what", choices=["taxonomy", "ontology"])
    sub.add_parser("gaps", help="show logged coverage gaps (SME backlog)")

    args = p.parse_args(argv)
    if args.cmd == "ask":
        return asyncio.run(_ask(args))
    if args.cmd == "eval":
        return asyncio.run(_eval(args))
    if args.cmd == "serve-agents":
        return asyncio.run(_serve_agents(args))
    return {"validate": _validate, "cards": _cards, "mcp": _mcp, "export": _export, "gaps": _gaps}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

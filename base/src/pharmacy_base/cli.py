"""pharmacy-domains: ask an installed pharmacy domain agent, replay its goldens, validate the composed packs.

    pharmacy-domains agents
    pharmacy-domains ask utilization_management RX-1001 "Why was this rejected?" --trace
    pharmacy-domains eval goldens/domain_goldens.yaml
    pharmacy-domains validate
    pharmacy-domains cards [--agent NAME]
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



def _settings(args: argparse.Namespace) -> Settings:
    s = Settings()
    if getattr(args, "today", None):
        s.today = date.fromisoformat(args.today)
    return s


async def _ask(args: argparse.Namespace) -> int:
    from .system import build
    system = build(_settings(args))
    agent = system.agents[args.agent]
    exp = await agent.ask(args.question, args.record, session_id=args.session, rules_mode=args.rules_mode,
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
    results = await run_goldens(Path(args.goldens), agents=system.agents)
    passed = sum(r.passed for r in results)
    for r in results:
        print(f"{'PASS' if r.passed else 'FAIL'}  {r.agent:<24} {r.record}  {r.question}")
        for p in r.problems:
            print(f"        - {p}")
    print(f"\nMatch rate: {passed}/{len(results)} ({100 * passed / len(results):.0f}%)")
    return 0 if passed == len(results) else 1


def main(argv: list[str] | None = None) -> int:
    warnings.filterwarnings("ignore")
    p = argparse.ArgumentParser(prog="pharmacy-domains", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--today", help="pin today's date (YYYY-MM-DD) for current-rules mode")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("agents", help="list domain agents and their rule domains")
    a = sub.add_parser("ask", help="ask one domain agent about a claim")
    a.add_argument("agent")
    a.add_argument("record")
    a.add_argument("question")
    a.add_argument("--rules-mode", default="as_decided", choices=["as_decided", "current", "as_of"])
    a.add_argument("--as-of")
    a.add_argument("--session")
    a.add_argument("--json", action="store_true")
    a.add_argument("--trace", action="store_true")
    e = sub.add_parser("eval", help="replay domain goldens")
    e.add_argument("goldens", help="goldens YAML file (each entry names its agent)")
    sub.add_parser("validate", help="CI checks for the knowledge pack")
    c = sub.add_parser("cards", help="print agent cards")
    c.add_argument("--agent")
    from rulegraph.commands import add_knowledge_commands, run_knowledge_command
    add_knowledge_commands(sub)
    args = p.parse_args(argv)
    if args.cmd in ("view", "export", "shacl", "publish"):
        from .system import base_source, build, discover_domains
        return run_knowledge_command(args, lambda: build(_settings(args)).knowledge,
                                     lambda: [base_source(), *[pk.source for pk in discover_domains()]])

    if args.cmd == "ask":
        return asyncio.run(_ask(args))
    if args.cmd == "eval":
        return asyncio.run(_eval(args))
    from .system import build
    system = build(_settings(args))
    if args.cmd == "agents":
        for name, agent in system.agents.items():
            print(f"{name:<24} {agent.label:<28} rule domains: {', '.join(agent.executors)}")
        return 0
    if args.cmd == "validate":
        from rulegraph.rules.validate import validate
        issues = validate(system.knowledge)
        for i in issues:
            print("ISSUE", i)
        kn = system.knowledge
        print(f"{kn.origin}: {len(kn.rules.rules)} rule versions, {len(kn.rules.domains)} rule domains, "
              f"{len(system.agents)} agents ({', '.join(system.agents)}): {'OK' if not issues else f'{len(issues)} issues'}")
        return 1 if issues else 0
    if args.cmd == "cards":
        from rulegraph.agent.cards import agent_card
        names = [args.agent] if args.agent else list(system.agents)
        print(json.dumps([agent_card(system.agents[n]) for n in names], indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

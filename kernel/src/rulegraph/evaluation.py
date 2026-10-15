"""Golden replay: the quality gate for rules, procedures and code changes.

Goldens are YAML: [{record, question, agent?, expect: {...}}]. Without `agent`
the question goes to the planner passed in; with `agent` it goes to that domain
agent directly (from `agents`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from .agent.planner import Planner
from .models import Explanation


@dataclass
class GoldenResult:
    record: str
    question: str
    agent: str | None
    passed: bool
    problems: list[str] = field(default_factory=list)
    explanation: Explanation | None = None


def check(exp: Explanation, expect: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if "primary" in expect:
        got = exp.primary.rule_id if exp.primary else None
        if got != expect["primary"]:
            problems.append(f"primary {got} != {expect['primary']}")
    if "confidence" in expect and exp.confidence != expect["confidence"]:
        problems.append(f"confidence {exp.confidence} != {expect['confidence']}")
    for phrase in expect.get("contains", []):
        if phrase.lower() not in exp.answer.lower():
            problems.append(f"answer lacks {phrase!r}")
    if "what_if" in expect and expect["what_if"] not in {f.rule_id for f in exp.what_if}:
        problems.append(f"what-if {expect['what_if']} missing")
    for rid in expect.get("overridden", []):
        if rid not in {f.rule_id for f in exp.overridden}:
            problems.append(f"{rid} should be overridden")
    if "agents_in_round_2" in expect:
        second = exp.rounds[1].agents if len(exp.rounds) > 1 else []
        names = {a.split(":")[0] for a in second}
        if not set(expect["agents_in_round_2"]) <= names:
            problems.append(f"round 2 agents {sorted(names)} lack {expect['agents_in_round_2']}")
    if "gap" in expect and expect["gap"] not in exp.gaps:
        problems.append(f"gap {expect['gap']} not reported")
    if "out_of_scope" in expect and expect["out_of_scope"] not in exp.out_of_scope:
        problems.append(f"{expect['out_of_scope']} should be reported out of scope")
    if "compare_differs" in expect:
        differs = bool(exp.compare and exp.compare.get("differs"))
        if differs != expect["compare_differs"]:
            problems.append(f"compare differs {differs} != {expect['compare_differs']}")
    if "current_primary" in expect:
        got = (exp.compare or {}).get("current", {}).get("primary")
        if got != expect["current_primary"]:
            problems.append(f"current primary {got} != {expect['current_primary']}")
    return problems


async def run_goldens(path: Path, planner: Planner | None = None,
                      agents: Mapping[str, Planner] | None = None) -> list[GoldenResult]:
    goldens = yaml.safe_load(Path(path).read_text())["goldens"]
    results = []
    for g in goldens:
        target = agents[g["agent"]] if g.get("agent") else planner
        if target is None:
            raise ValueError(f"no planner for golden {g}")
        exp = await target.ask(g["question"], g["record"])
        problems = check(exp, g["expect"])
        results.append(GoldenResult(g["record"], g["question"], g.get("agent"), not problems, problems, exp))
    return results

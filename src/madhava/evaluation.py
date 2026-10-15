"""Golden-claim replay: the quality gate for rules, procedures and code changes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .app import System
from .models import Explanation


@dataclass
class GoldenResult:
    claim: str
    question: str
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
    if "compare_differs" in expect:
        differs = bool(exp.compare and exp.compare.get("differs"))
        if differs != expect["compare_differs"]:
            problems.append(f"compare differs {differs} != {expect['compare_differs']}")
    if "current_primary" in expect:
        got = (exp.compare or {}).get("current", {}).get("primary")
        if got != expect["current_primary"]:
            problems.append(f"current primary {got} != {expect['current_primary']}")
    return problems


async def run_goldens(system: System, path: Path | None = None) -> list[GoldenResult]:
    path = path or system.settings.goldens_file
    goldens = yaml.safe_load(path.read_text())["goldens"]
    results = []
    for g in goldens:
        exp = await system.planner.ask(g["question"], g["claim"])
        problems = check(exp, g["expect"])
        results.append(GoldenResult(g["claim"], g["question"], not problems, problems, exp))
    return results

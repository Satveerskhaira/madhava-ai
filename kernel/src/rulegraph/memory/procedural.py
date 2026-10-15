"""Procedural memory: reviewed playbooks for planners and investigators.

- planner procedures (core level): which agents to ask per question type
- domain planner procedures: the same, inside one domain agent's scope,
  including how to handle a task delegated by a parent planner
- investigator playbook: the fixed steps every rule-domain investigator runs
- intent patterns: how questions are classified without a model
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class PlannerProcedure:
    name: str
    description: str
    intents: list[str]
    task_mode: str = "explain"                 # explain | what_if | none
    route_from: list[str] = field(default_factory=list)
    speculative: list[str] = field(default_factory=list)   # executor names always asked; "*" = all
    sweep_if_unrouted: bool = False            # ask every executor in scope when nothing routed
    must_explain: str = "none"                 # outcome_tokens | amount_tokens | task_tokens | none
    rules_modes: list[str] = field(default_factory=list)
    max_rounds: int = 1
    expand: list[str] = field(default_factory=list)


@dataclass
class InvestigatorPlaybook:
    steps: list[dict[str, Any]]
    modes: dict[str, dict[str, Any]]
    notes: dict[str, str]

    def mode(self, name: str) -> dict[str, Any]:
        return self.modes.get(name, {})


def _procedures(path: Path) -> dict[str, PlannerProcedure]:
    raw = yaml.safe_load(path.read_text())["procedures"]
    out = {}
    for name, spec in raw.items():
        loop = spec.get("loop", {})
        out[name] = PlannerProcedure(
            name=name,
            description=str(spec.get("description", "")).strip(),
            intents=spec["intents"],
            task_mode=spec.get("task_mode", "explain"),
            route_from=spec.get("route_from", []),
            speculative=spec.get("speculative", []),
            sweep_if_unrouted=bool(spec.get("sweep_if_unrouted", False)),
            must_explain=spec.get("must_explain", "none"),
            rules_modes=spec.get("rules_modes", []),
            max_rounds=loop.get("max_rounds", 1),
            expand=loop.get("expand", []),
        )
    return out


class ProceduralMemory:
    def __init__(self, planner: dict[str, PlannerProcedure], domain_planner: dict[str, PlannerProcedure],
                 investigator: InvestigatorPlaybook, intents: dict[str, Any]):
        self.planner = planner
        self.domain_planner = domain_planner
        self.investigator = investigator
        self.intents = intents

    @classmethod
    def load(cls, pack: Any, planner_procedures: Path | None = None) -> "ProceduralMemory":
        inv = yaml.safe_load(pack.path("investigator").read_text())
        playbook = InvestigatorPlaybook(
            steps=inv["default"]["steps"],
            modes=inv["default"].get("modes", {}),
            notes={k: (v or {}).get("notes", "") for k, v in inv.get("agents", {}).items()},
        )
        domain = _procedures(pack.path("domain_planner")) if pack.has("domain_planner") else {}
        core = _procedures(planner_procedures) if planner_procedures else {}
        intents = yaml.safe_load(pack.path("intents").read_text()) if pack.has("intents") else {}
        return cls(core, domain, playbook, intents)

    @staticmethod
    def for_intents(procedures: dict[str, PlannerProcedure], intents: list[str]) -> list[PlannerProcedure]:
        return [p for p in procedures.values() if set(p.intents) & set(intents)]

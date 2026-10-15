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
    return procedures_from(yaml.safe_load(path.read_text()))


def procedures_from(doc: dict[str, Any] | None) -> dict[str, PlannerProcedure]:
    raw = (doc or {}).get("procedures", {})
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
    def from_documents(cls, investigator: dict[str, Any], domain_planner: dict[str, Any] | None = None,
                       intents: dict[str, Any] | None = None,
                       planner_procedures: Path | dict[str, Any] | None = None) -> "ProceduralMemory":
        playbook = InvestigatorPlaybook(
            steps=investigator["default"]["steps"],
            modes=investigator["default"].get("modes", {}),
            notes={k: (v or {}).get("notes", "") for k, v in (investigator.get("agents") or {}).items()},
        )
        core_doc = yaml.safe_load(Path(planner_procedures).read_text()) \
            if isinstance(planner_procedures, (str, Path)) else planner_procedures
        return cls(procedures_from(core_doc), procedures_from(domain_planner), playbook, intents or {})

    @staticmethod
    def for_intents(procedures: dict[str, PlannerProcedure], intents: list[str]) -> list[PlannerProcedure]:
        return [p for p in procedures.values() if set(p.intents) & set(intents)]

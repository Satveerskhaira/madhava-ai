"""Procedural memory: reviewed playbooks for the planner and domain agents."""

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
    task_mode: str = "explain"
    route_from: list[str] = field(default_factory=list)
    speculative_agents: list[str] = field(default_factory=list)
    must_explain: str = "none"
    rules_modes: list[str] = field(default_factory=list)
    max_rounds: int = 1
    expand: list[str] = field(default_factory=list)


@dataclass
class DomainPlaybook:
    steps: list[dict[str, Any]]
    modes: dict[str, dict[str, Any]]
    notes: dict[str, str]

    def mode(self, name: str) -> dict[str, Any]:
        return self.modes.get(name, {})

    def step(self, name: str) -> dict[str, Any] | None:
        return next((s for s in self.steps if s["do"] == name), None)


class ProceduralMemory:
    def __init__(self, planner: dict[str, PlannerProcedure], domain: DomainPlaybook):
        self.planner = planner
        self.domain = domain

    @classmethod
    def load(cls, directory: Path) -> "ProceduralMemory":
        p = yaml.safe_load((directory / "planner.yaml").read_text())["procedures"]
        planner = {}
        for name, spec in p.items():
            loop = spec.get("loop", {})
            planner[name] = PlannerProcedure(
                name=name,
                description=spec["description"].strip(),
                intents=spec["intents"],
                task_mode=spec.get("task_mode", "explain"),
                route_from=spec.get("route_from", []),
                speculative_agents=spec.get("speculative_agents", []),
                must_explain=spec.get("must_explain", "none"),
                rules_modes=spec.get("rules_modes", []),
                max_rounds=loop.get("max_rounds", 1),
                expand=loop.get("expand", []),
            )
        d = yaml.safe_load((directory / "domain.yaml").read_text())
        playbook = DomainPlaybook(
            steps=d["default"]["steps"],
            modes=d["default"].get("modes", {}),
            notes={k: v.get("notes", "") for k, v in d.get("agents", {}).items()},
        )
        return cls(planner, playbook)

    def for_intents(self, intents: list[str]) -> list[PlannerProcedure]:
        return [p for p in self.planner.values() if set(p.intents) & set(intents)]

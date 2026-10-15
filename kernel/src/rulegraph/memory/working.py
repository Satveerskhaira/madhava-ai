"""Working memory: the blackboard passed between planner graph nodes.

A pydantic model, so it can live in any agent SDK's state store between graph
nodes and be replayed exactly.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from ..models import Ask, ContextRef, DomainReport, DomainTask, Explanation, Finding, RoundRecord


class Blackboard(BaseModel):
    agent: str = "core"
    question: str
    record_id: str | None = None
    session_id: str | None = None
    requested_rules_mode: str = "as_decided"
    as_of: date | None = None
    task: DomainTask | None = None              # set when a parent planner delegated this question

    ask: Ask | None = None
    record: dict[str, Any] = Field(default_factory=dict)       # summary, not the full record
    outcome_tokens: list[str] = Field(default_factory=list)
    amount_tokens: list[str] = Field(default_factory=list)

    procedures: list[str] = Field(default_factory=list)
    must_explain: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    max_rounds: int = 1
    expand: list[str] = Field(default_factory=list)
    rules_modes: list[str] = Field(default_factory=list)
    contexts: dict[str, ContextRef] = Field(default_factory=dict)

    pending: list[DomainTask] = Field(default_factory=list)
    reports: list[DomainReport] = Field(default_factory=list)
    asked: list[str] = Field(default_factory=list)
    round: int = 0
    rounds: list[RoundRecord] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    reused_from_session: bool = False
    trace: list[str] = Field(default_factory=list)
    explanation: Explanation | None = None

    @property
    def delegated(self) -> bool:
        return self.task is not None

    @property
    def primary_rules_mode(self) -> str:
        return self.rules_modes[0] if self.rules_modes else self.requested_rules_mode

    def findings(self, mode: str | None = None, rules_mode: str | None = None) -> list[Finding]:
        """All findings from all reports, one per rule version, mode and rules mode."""
        best: dict[tuple[str, str, str], Finding] = {}
        for r in self.reports:
            for f in r.findings:
                if mode and f.mode != mode:
                    continue
                if rules_mode and f.rules_mode != rules_mode:
                    continue
                key = (f.key, f.mode, f.rules_mode)
                cur = best.get(key)
                # prefer the finding from the investigator that retrieved the rule itself
                if cur is None or (cur.retrieval is None and f.retrieval is not None):
                    best[key] = f
        return list(best.values())

    def log(self, message: str) -> None:
        self.trace.append(message)

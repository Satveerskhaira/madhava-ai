"""Typed contracts shared by planners, investigators, tools and adapters.

These are the schemas on every edge: tasks a planner sends to its executors
(domain agents or rule-domain investigators), the reports and findings they
return, and the explanation a planner produces. Keep them stable; every agent
in every repository depends on them.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

TaskMode = Literal["explain", "what_if"]
OutcomeType = Literal["reject", "pricing", "info", "override"]
FindingStatus = Literal["fired", "not_fired", "overridden", "unverified"]
Confidence = Literal["confirmed", "likely", "supporting", "what_if", "unexplained"]


# ------------------------------------------------------------------ rules
class RuleOutcome(BaseModel):
    type: OutcomeType
    code: str | None = None          # adverse outcome code (reject, deny, decline ...)
    component: str | None = None     # amount component (copay, fee, interest ...)
    amount: str | None = None        # CEL expression for the expected amount
    actual: str | None = None        # record path holding the actual amount

    @property
    def token(self) -> str | None:
        if self.type == "reject" and self.code:
            return f"reject:{self.code}"
        if self.type == "pricing" and self.component:
            return f"price:{self.component}"
        return None


class SourceRef(BaseModel):
    doc: str
    section: str | None = None
    row: str | None = None


class RuleCard(BaseModel):
    id: str
    version: int
    status: Literal["draft", "in_review", "approved", "deprecated"]
    domain: str
    concept: str
    intent: str
    outcome: RuleOutcome
    record_inputs: list[str] = Field(default_factory=list)
    reference_inputs: list[str] = Field(default_factory=list)
    condition: str
    order: int = 100
    overridden_by: list[str] = Field(default_factory=list)
    template: str = ""
    source_refs: list[SourceRef] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"
    sme_owner: str | None = None

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def inputs(self) -> list[str]:
        return [*self.record_inputs, *self.reference_inputs]


class RuleSet(BaseModel):
    """An effective-dated release of one rule domain."""

    id: str
    domain: str
    effective_from: date
    effective_to: date | None = None
    status: Literal["candidate", "active", "archived", "withdrawn"]
    change_ref: str | None = None
    reason: str | None = None
    previous: str | None = None
    rules: list[str]

    def covers(self, day: date) -> bool:
        return self.effective_from <= day and (self.effective_to is None or day < self.effective_to)


# --------------------------------------------------------------- findings
class Evidence(BaseModel):
    source: Literal["record", "reference", "computed"]
    path: str
    value: Any = None
    present: bool = True
    pointer: str | None = None
    reference_version: str | None = None


class Retrieval(BaseModel):
    """Why the policy graph picked this rule: score and the path that reached it."""

    score: float
    path: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    rule_id: str
    version: int
    rule_set: str | None = None
    domain: str                        # rule domain
    concept: str
    agent: str                         # top-level domain agent that owns the concept
    unit: str                          # executor that evaluated it (rule-domain investigator)
    intent: str
    outcome_type: OutcomeType
    outcome_token: str | None = None
    status: FindingStatus
    confidence: Confidence | None = None
    mode: TaskMode = "explain"
    rules_mode: str = "as_decided"
    order: int = 100
    precedence: int = 100
    amount: float | None = None
    actual: float | None = None
    overridden_by: str | None = None
    overrides_checked: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    narrative: str = ""
    retrieval: Retrieval | None = None
    note: str | None = None
    error: str | None = None

    @property
    def key(self) -> str:
        return f"{self.rule_id}@{self.version}"

    @property
    def explains(self) -> bool:
        return self.status == "fired" and self.confidence in ("confirmed", "likely")


# ------------------------------------------------------------- delegation
class ContextRef(BaseModel):
    """What an executor needs to find the record itself. No record data is copied."""

    record_id: str
    reference_id: str
    service_date: date
    rules_mode: str = "as_decided"
    rules_date: date


class DomainTask(BaseModel):
    task_id: str
    agent: str                                        # executor name at the receiving level
    context: ContextRef
    mode: TaskMode = "explain"
    concepts: list[str] = Field(default_factory=list)
    outcome_tokens: list[str] = Field(default_factory=list)
    hints: list[str] = Field(default_factory=list)    # rule ids suggested by the parent's graph search
    question: str = ""
    round: int = 1


class RoundRecord(BaseModel):
    round: int
    agents: list[str]
    reason: str
    elapsed_ms: float = 0.0


class DomainReport(BaseModel):
    task_id: str
    agent: str
    mode: TaskMode = "explain"
    rules_mode: str = "as_decided"
    findings: list[Finding] = Field(default_factory=list)
    rule_sets: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    widened: bool = False
    answer: str = ""                                  # domain-level answer when the executor is a planner
    gaps: list[str] = Field(default_factory=list)
    confidence: Confidence | None = None
    rounds: list[RoundRecord] = Field(default_factory=list)
    elapsed_ms: float = 0.0
    error: str | None = None


# --------------------------------------------------------------- planner
class Ask(BaseModel):
    """The understood question."""

    question: str
    record_id: str | None = None
    intents: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)
    source: str = "deterministic"


class Explanation(BaseModel):
    agent: str                                       # planner that answered (core or a domain agent)
    record_id: str | None
    question: str
    answer: str
    confidence: Confidence
    intents: list[str] = Field(default_factory=list)
    primary: Finding | None = None
    supporting: list[Finding] = Field(default_factory=list)
    what_if: list[Finding] = Field(default_factory=list)
    overridden: list[Finding] = Field(default_factory=list)
    checked: list[Finding] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    rules_context: dict[str, Any] = Field(default_factory=dict)
    reference_context: dict[str, Any] = Field(default_factory=dict)
    compare: dict[str, Any] | None = None
    rounds: list[RoundRecord] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)

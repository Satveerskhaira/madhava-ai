"""Typed contracts shared by the planner, domain agents, tools and A2A.

These are the schemas on every edge of the graph: tasks sent to domain agents,
reports and findings they return, and the explanation the planner produces.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

RulesMode = Literal["as_adjudicated", "current", "as_of"]
TaskMode = Literal["explain", "what_if"]
OutcomeType = Literal["reject", "pricing", "info", "override"]
FindingStatus = Literal["fired", "not_fired", "overridden", "unverified"]
Confidence = Literal["confirmed", "likely", "supporting", "what_if", "unexplained"]


# ------------------------------------------------------------------ rules
class RuleOutcome(BaseModel):
    type: OutcomeType
    code: str | None = None          # reject code
    component: str | None = None     # pricing component: copay, coinsurance, deductible
    amount: str | None = None        # CEL expression for the expected amount
    actual: str | None = None        # claim path holding the actual amount

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
    claim_inputs: list[str] = Field(default_factory=list)
    plan_inputs: list[str] = Field(default_factory=list)
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
        return [*self.claim_inputs, *self.plan_inputs]


class RuleSet(BaseModel):
    """An effective-dated release of one rule domain (design doc section 7)."""

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
    source: Literal["claim", "plan", "computed"]
    path: str
    value: Any = None
    present: bool = True
    pointer: str | None = None
    plan_version: str | None = None


class Retrieval(BaseModel):
    """Why the policy graph picked this rule: score and the path that reached it."""

    score: float
    path: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    rule_id: str
    version: int
    rule_set: str | None = None
    domain: str
    concept: str
    agent: str
    intent: str
    outcome_type: OutcomeType
    outcome_token: str | None = None
    status: FindingStatus
    confidence: Confidence | None = None
    mode: TaskMode = "explain"
    rules_mode: str = "as_adjudicated"
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


# ------------------------------------------------------------------- A2A
class ContextRef(BaseModel):
    """What a domain agent needs to find the claim itself. No PHI is copied."""

    claim_id: str
    plan_id: str
    dos: date
    rules_mode: str = "as_adjudicated"
    rules_date: date


class DomainTask(BaseModel):
    task_id: str
    agent: str
    context: ContextRef
    mode: TaskMode = "explain"
    concepts: list[str] = Field(default_factory=list)
    outcome_tokens: list[str] = Field(default_factory=list)
    hints: list[str] = Field(default_factory=list)   # rule ids suggested by the planner's graph search
    question: str = ""
    round: int = 1


class DomainReport(BaseModel):
    task_id: str
    agent: str
    mode: TaskMode = "explain"
    rules_mode: str = "as_adjudicated"
    findings: list[Finding] = Field(default_factory=list)
    rule_sets: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    widened: bool = False
    elapsed_ms: float = 0.0
    error: str | None = None


# --------------------------------------------------------------- planner
class Ask(BaseModel):
    """The understood question."""

    question: str
    claim_id: str | None = None
    intents: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)
    source: str = "deterministic"


class RoundRecord(BaseModel):
    round: int
    agents: list[str]
    reason: str
    elapsed_ms: float = 0.0


class Explanation(BaseModel):
    claim_id: str | None
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
    rules_context: dict[str, Any] = Field(default_factory=dict)
    plan_context: dict[str, Any] = Field(default_factory=dict)
    compare: dict[str, Any] | None = None
    rounds: list[RoundRecord] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)

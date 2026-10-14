"""The toolbox: every capability agents use to reach data and knowledge.

Agents call these methods in process; `mcp_server.py` serves the same methods
over MCP so the contract is identical for remote agents, IDE assistants and
the production Java tool servers that will implement it.
"""

from __future__ import annotations

import threading
from datetime import date
from typing import Any

from ..knowledge import Knowledge
from ..models import Evidence, RuleCard
from ..rules.graph import RuleHit, Seed
from ..settings import Settings
from .apis import BenefitSetupApi, ClaimsApi, PlanVersion

_PRICE_FIELDS = {"copay": "copay", "coinsurance": "coinsurance", "deductible": "deductibleApplied"}


def _d(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


class Toolbox:
    def __init__(self, knowledge: Knowledge, claims: ClaimsApi, benefits: BenefitSetupApi,
                 settings: Settings | None = None):
        self.kn = knowledge
        self.claims = claims
        self.benefits = benefits
        self.settings = settings or Settings()
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._plans: dict[tuple[str, date], PlanVersion] = {}
        self._slices: dict[tuple[str, str, str], Any] = {}
        self._lock = threading.Lock()
        self.stats = {"claim_calls": 0, "plan_calls": 0, "slice_hits": 0, "slice_misses": 0}

    # ----------------------------------------------------------------- claim
    def get_claim_outcome(self, claim_id: str) -> dict[str, Any]:
        """Claim status, outcome tokens, decoded rejects and the plan version pinned by DOS."""
        with self._lock:
            if claim_id in self._snapshots:
                return self._snapshots[claim_id]
        raw = self.claims.get_claim(claim_id)
        self.stats["claim_calls"] += 1
        dos = _d(raw["dos"])
        member = raw.setdefault("member", {})
        if member.get("enrollmentDate"):
            member["daysSinceEnrollment"] = (dos - _d(member["enrollmentDate"])).days
        history = raw.setdefault("history", {})
        if history.get("lastFillDate"):
            history["daysSinceLastFill"] = (dos - _d(history["lastFillDate"])).days
        tokens = [f"reject:{c}" for c in raw.get("rejectCodes", [])]
        pricing = raw.get("pricing", {})
        price_tokens = [f"price:{comp}" for comp, fld in _PRICE_FIELDS.items() if (pricing.get(fld) or 0) > 0]
        plan = self._plan(raw["planId"], dos)
        snapshot = {
            "claim": raw,
            "claim_id": claim_id,
            "status": raw.get("status"),
            "outcome_tokens": tokens,
            "price_tokens": price_tokens,
            "rejects": [{"code": c, "meaning": self.kn.reject_codes.get(str(c), "unknown code")}
                        for c in raw.get("rejectCodes", [])],
            "dos": dos.isoformat(),
            "adjudicated_at": str(raw.get("adjudicatedAt", raw["dos"]))[:10],
            "plan": {"plan_id": plan.plan_id, "version": plan.version,
                     "effective_from": plan.effective_from.isoformat(),
                     "effective_to": plan.effective_to.isoformat() if plan.effective_to else None},
            "drug_name": raw.get("drug", {}).get("name"),
        }
        with self._lock:
            self._snapshots[claim_id] = snapshot
        return snapshot

    def rules_date(self, snapshot: dict[str, Any], rules_mode: str, as_of: date | None = None) -> date:
        """Which day's rules apply: as adjudicated (default), current, or a given date."""
        if rules_mode == "current":
            return self.settings.today
        if rules_mode == "as_of" and as_of:
            return as_of
        key = "adjudicated_at" if self.settings.rule_resolution == "adjudication_date" else "dos"
        return _d(snapshot[key])

    def claim_values(self, claim_id: str, paths: list[str]) -> dict[str, Evidence]:
        claim = self.get_claim_outcome(claim_id)["claim"]
        out: dict[str, Evidence] = {}
        for p in paths:
            value = self.kn.catalog.read_claim(claim, p)
            missing = self.kn.catalog.is_missing(value)
            out[p] = Evidence(source="claim", path=p, value=None if missing else value, present=not missing)
        return out

    # ------------------------------------------------------------------ plan
    def _plan(self, plan_id: str, dos: date) -> PlanVersion:
        key = (plan_id, dos)
        with self._lock:
            if key in self._plans:
                return self._plans[key]
        plan = self.benefits.get_plan_as_of(plan_id, dos)
        self.stats["plan_calls"] += 1
        with self._lock:
            self._plans[key] = plan
        return plan

    def get_benefit_slice(self, claim_id: str, paths: list[str]) -> dict[str, Evidence]:
        """Only the requested benefit setup fields, resolved as of the claim's date of service."""
        snap = self.get_claim_outcome(claim_id)
        claim = snap["claim"]
        plan = self._plan(claim["planId"], _d(claim["dos"]))
        out: dict[str, Evidence] = {}

        def value_of(path: str) -> Any:
            if path not in out:
                resolve(path)
            ev = out[path]
            return ev.value if ev.present else None

        def resolve(path: str) -> None:
            pointer = self.kn.catalog.resolve_plan_pointer(path, claim, value_of)
            if pointer is None:
                out[path] = Evidence(source="plan", path=path, present=False, plan_version=plan.version)
                return
            key = (plan.plan_id, plan.version, pointer)
            with self._lock:
                cached = key in self._slices
                value = self._slices.get(key)
            if cached:
                self.stats["slice_hits"] += 1
            else:
                self.stats["slice_misses"] += 1
                value = self.kn.catalog.read_pointer(plan.payload, pointer)
                with self._lock:
                    self._slices[key] = value
            missing = self.kn.catalog.is_missing(value)
            out[path] = Evidence(source="plan", path=path, value=None if missing else value,
                                 present=not missing, pointer=pointer, plan_version=plan.version)

        for p in paths:
            if p not in out:
                resolve(p)
        return {p: out[p] for p in paths}

    def inputs_for(self, claim_id: str, cards: list[RuleCard]) -> dict[str, Evidence]:
        claim_paths = sorted({p for c in cards for p in c.claim_inputs} | {c.outcome.actual for c in cards if c.outcome.actual})
        plan_paths = sorted({p for c in cards for p in c.plan_inputs})
        values = self.claim_values(claim_id, claim_paths)
        values.update(self.get_benefit_slice(claim_id, plan_paths))
        return values

    # ----------------------------------------------------------------- rules
    def get_rule(self, rule_id: str, version: int | None = None) -> RuleCard:
        if version is None:
            return self.kn.rules.latest(rule_id)
        return self.kn.rules.rules[f"{rule_id}@{version}"]

    def search_rules(self, rules_date: date, outcome_tokens: list[str] | None = None,
                     concepts: list[str] | None = None, text: str = "", hints: list[str] | None = None,
                     scope: set[str] | None = None, k: int = 10) -> list[RuleHit]:
        """Graph search for candidate rules in force on `rules_date`, optionally inside a taxonomy branch."""
        seeds: list[Seed] = []
        for t in outcome_tokens or []:
            seeds.append(Seed(f"outcome:{t}", 1.0, t))
        for c in concepts or []:
            seeds.append(Seed(f"concept:{c}", 0.8, c))
        for h in hints or []:
            seeds.append(Seed(f"rule:{h}", 1.0, f"hint {h}"))
        if text:
            hits = self.kn.semantic.search(text, kinds={"rule"}, k=5)
            top = hits[0].score if hits else 1.0
            for hit in hits:
                seeds.append(Seed(hit.doc.id, 0.5 * hit.score / top, f"text match {hit.doc.id}"))
        domains = None
        if scope is not None:
            domains = {self.kn.taxonomy.get(c).rule_domain for c in scope if self.kn.taxonomy.get(c).rule_domain}
        allowed = set(self.kn.rules.resolved(domains, rules_date))
        return self.kn.graph.search(seeds, allowed_rules=allowed, scope_concepts=scope,
                                    rule_concept=self.kn.rule_concepts(), k=k)

    def list_rule_history(self, domain: str) -> list[dict[str, Any]]:
        return [rs.model_dump(mode="json") for rs in self.kn.rules.history(domain)]

    def diff_rule_sets(self, domain: str, from_date: date, to_date: date) -> dict[str, Any]:
        return {"domain": domain, "from": from_date.isoformat(), "to": to_date.isoformat(),
                **self.kn.rules.diff(domain, from_date, to_date),
                "releases": [{"id": rs.id, "effective_from": rs.effective_from.isoformat(), "reason": rs.reason}
                             for rs in self.kn.rules.changes_after(domain, from_date, to_date)]}

    # ------------------------------------------------------------- knowledge
    def lookup_concept(self, text: str) -> list[dict[str, Any]]:
        out = []
        for cid in self.kn.taxonomy.match_text(text):
            c = self.kn.taxonomy.get(cid)
            out.append({"concept": cid, "label": c.prefLabel, "definition": c.definition,
                        "agent": self.kn.taxonomy.agent_for(cid), "outcomes": c.notations})
        return out

    def search_knowledge(self, query: str, kinds: list[str] | None = None, k: int = 5) -> list[dict[str, Any]]:
        hits = self.kn.semantic.search(query, kinds=set(kinds) if kinds else None, k=k)
        return [{"id": h.doc.id, "kind": h.doc.kind, "score": round(h.score, 3), **h.doc.meta} for h in hits]

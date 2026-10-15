"""The toolbox: every capability planners and investigators use to reach data and knowledge.

Agents call these methods in process; `adapters/mcp.py` serves the same methods
over MCP, so the contract is identical for any agent SDK, IDE assistant or
tool server written in another language.

Domain specifics live in a `RecordAdapter` supplied by the knowledge pack's
package: which record fields hold the status, outcome codes, amounts and
dates, and which fields are derived (days since last fill, age, ...).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from ..knowledge import Knowledge
from ..models import Evidence, RuleCard
from ..rules.graph import RuleHit, Seed
from ..settings import Settings
from .apis import RecordApi, ReferenceApi, ReferenceVersion


def as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


@dataclass
class RecordView:
    """What the kernel needs to know about a record, produced by the pack's RecordAdapter."""

    record: dict[str, Any]                         # raw record plus derived fields
    status: str
    adverse: bool                                  # an adverse decision (reject, deny, decline)
    outcome_tokens: list[str]                      # e.g. reject:75
    amount_tokens: list[str]                       # e.g. price:copay
    reference_id: str
    service_date: date
    decision_date: date
    summary: dict[str, Any] = field(default_factory=dict)   # safe display fields (no identifiers)


class RecordAdapter(Protocol):
    def view(self, raw: dict[str, Any]) -> RecordView: ...


class Toolbox:
    def __init__(self, knowledge: Knowledge, records: RecordApi, references: ReferenceApi,
                 adapter: RecordAdapter, settings: Settings | None = None):
        self.kn = knowledge
        self.records = records
        self.references = references
        self.adapter = adapter
        self.settings = settings or Settings()
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._refs: dict[tuple[str, date], ReferenceVersion] = {}
        self._slices: dict[tuple[str, str, str], Any] = {}
        self._lock = threading.Lock()
        self.stats = {"record_calls": 0, "reference_calls": 0, "slice_hits": 0, "slice_misses": 0}

    # ---------------------------------------------------------------- record
    def get_record_outcome(self, record_id: str) -> dict[str, Any]:
        """Status, outcome and amount tokens, decoded codes and the reference version pinned by service date."""
        with self._lock:
            if record_id in self._snapshots:
                return self._snapshots[record_id]
        raw = self.records.get_record(record_id)
        self.stats["record_calls"] += 1
        view = self.adapter.view(raw)
        ref = self._reference(view.reference_id, view.service_date)
        kind = self.kn.pack.codes_kind
        snapshot = {
            "record": view.record,
            "record_id": record_id,
            "status": view.status,
            "adverse": view.adverse,
            "outcome_tokens": list(view.outcome_tokens),
            "amount_tokens": list(view.amount_tokens),
            "codes": [{"token": t, "code": t.split(":", 1)[1],
                       "meaning": self.kn.codes.get(t.split(":", 1)[1], "unknown code")}
                      for t in view.outcome_tokens if t.startswith(kind + ":")],
            "service_date": view.service_date.isoformat(),
            "decision_date": view.decision_date.isoformat(),
            "reference": {"id": ref.reference_id, "version": ref.version,
                          "effective_from": ref.effective_from.isoformat(),
                          "effective_to": ref.effective_to.isoformat() if ref.effective_to else None},
            "summary": view.summary,
        }
        with self._lock:
            self._snapshots[record_id] = snapshot
        return snapshot

    def rules_date(self, snapshot: dict[str, Any], rules_mode: str, as_of: date | None = None) -> date:
        """Which day's rules apply: as decided (default), current, or a given date."""
        if rules_mode == "current":
            return self.settings.today
        if rules_mode == "as_of" and as_of:
            return as_of
        key = "decision_date" if self.settings.rule_resolution == "decision_date" else "service_date"
        return as_date(snapshot[key])

    def record_values(self, record_id: str, paths: list[str]) -> dict[str, Evidence]:
        record = self.get_record_outcome(record_id)["record"]
        out: dict[str, Evidence] = {}
        for p in paths:
            value = self.kn.catalog.read_record(record, p)
            missing = self.kn.catalog.is_missing(value)
            out[p] = Evidence(source="record", path=p, value=None if missing else value, present=not missing)
        return out

    # ------------------------------------------------------------- reference
    def _reference(self, reference_id: str, day: date) -> ReferenceVersion:
        key = (reference_id, day)
        with self._lock:
            if key in self._refs:
                return self._refs[key]
        ref = self.references.get_as_of(reference_id, day)
        self.stats["reference_calls"] += 1
        with self._lock:
            self._refs[key] = ref
        return ref

    def get_reference_slice(self, record_id: str, paths: list[str]) -> dict[str, Evidence]:
        """Only the requested reference fields, resolved as of the record's service date."""
        snap = self.get_record_outcome(record_id)
        record = snap["record"]
        ref = self._reference(snap["reference"]["id"], as_date(snap["service_date"]))
        out: dict[str, Evidence] = {}

        def value_of(path: str) -> Any:
            if path not in out:
                resolve(path)
            ev = out[path]
            return ev.value if ev.present else None

        def resolve(path: str) -> None:
            pointer = self.kn.catalog.resolve_pointer(path, record, value_of)
            if pointer is None:
                out[path] = Evidence(source="reference", path=path, present=False, reference_version=ref.version)
                return
            key = (ref.reference_id, ref.version, pointer)
            with self._lock:
                cached = key in self._slices
                value = self._slices.get(key)
            if cached:
                self.stats["slice_hits"] += 1
            else:
                self.stats["slice_misses"] += 1
                value = self.kn.catalog.read_pointer(ref.payload, pointer)
                with self._lock:
                    self._slices[key] = value
            missing = self.kn.catalog.is_missing(value)
            out[path] = Evidence(source="reference", path=path, value=None if missing else value,
                                 present=not missing, pointer=pointer, reference_version=ref.version)

        for p in paths:
            if p not in out:
                resolve(p)
        return {p: out[p] for p in paths}

    def inputs_for(self, record_id: str, cards: list[RuleCard]) -> dict[str, Evidence]:
        rec_paths = sorted({p for c in cards for p in c.record_inputs} | {c.outcome.actual for c in cards if c.outcome.actual})
        ref_paths = sorted({p for c in cards for p in c.reference_inputs})
        values = self.record_values(record_id, rec_paths)
        values.update(self.get_reference_slice(record_id, ref_paths))
        return values

    # ----------------------------------------------------------------- rules
    def get_rule(self, rule_id: str, version: int | None = None) -> RuleCard:
        if version is None:
            return self.kn.rules.latest(rule_id)
        return self.kn.rules.rules[f"{rule_id}@{version}"]

    def search_rules(self, rules_date: date, outcome_tokens: list[str] | None = None,
                     concepts: list[str] | None = None, text: str = "", hints: list[str] | None = None,
                     scope: set[str] | None = None, k: int = 10) -> list[RuleHit]:
        """Graph search for candidate rules in force on `rules_date`, optionally inside a set of concepts."""
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

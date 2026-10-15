"""Investigators: the leaf executors that evaluate rules for one rule domain.

Each investigator runs the playbook from procedural memory. The tool sequence
is fixed; nothing here is chosen by a model:

  recall_rules -> resolve_inputs -> evaluate -> apply_overrides
  -> widen_if_unexplained (at most once) -> phrase
"""

from __future__ import annotations

import asyncio
import time
from datetime import date

from ..knowledge import Knowledge
from ..models import DomainReport, DomainTask, Evidence, Finding, Retrieval, RuleCard, RuleSet
from ..rules.evaluator import render
from ..rules.graph import RuleHit
from ..settings import Settings
from ..tools.toolbox import Toolbox


class Investigator:
    def __init__(self, name: str, knowledge: Knowledge, toolbox: Toolbox, concepts: set[str],
                 settings: Settings | None = None):
        self.name = name
        self.kn = knowledge
        self.tools = toolbox
        self.settings = settings or toolbox.settings
        self.scope = set(concepts)
        self.rule_domains = {knowledge.taxonomy.get(c).rule_domain for c in concepts
                             if knowledge.taxonomy.get(c).rule_domain}

    async def handle(self, task: DomainTask) -> DomainReport:
        return await asyncio.to_thread(self.run, task)

    # ------------------------------------------------------------------ run
    def run(self, task: DomainTask) -> DomainReport:
        start = time.perf_counter()
        report = DomainReport(task_id=task.task_id, agent=self.name, mode=task.mode,
                              rules_mode=task.context.rules_mode)
        try:
            self._run(task, report)
        except Exception as exc:  # noqa: BLE001 - an investigator failure must not sink the answer
            report.error = f"{type(exc).__name__}: {exc}"
            report.steps.append(f"{self.name}: failed: {report.error}")
        report.elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        return report

    def _run(self, task: DomainTask, report: DomainReport) -> None:
        playbook = self.kn.procedures.investigator
        mode_cfg = playbook.mode(task.mode)
        allowed_types = set(mode_cfg.get("outcome_types", ["reject", "pricing", "info", "override"]))
        ignore_actuals = bool(mode_cfg.get("ignore_actuals"))
        day = task.context.rules_date
        snap = self.tools.get_record_outcome(task.context.record_id)
        observed = set(snap["outcome_tokens"]) | set(snap["amount_tokens"])
        resolved = self.kn.rules.resolved(self.rule_domains, day)
        report.rule_sets = sorted({rs.id for rs, _ in resolved.values()})

        candidates: list[tuple[RuleHit, RuleSet, RuleCard]] = []
        values: dict[str, Evidence] = {}
        findings: dict[str, Finding] = {}
        concepts = [c for c in task.concepts if c in self.scope] or sorted(self.scope)

        def recall(text: str, seed_concepts: list[str], hints: list[str], k: int) -> list[tuple[RuleHit, RuleSet, RuleCard]]:
            hits = self.tools.search_rules(day, outcome_tokens=task.outcome_tokens, concepts=seed_concepts,
                                           text=text, hints=hints, scope=self.scope, k=k)
            out = []
            for h in hits:
                if h.rule_id in findings or h.rule_id not in resolved:
                    continue
                rs, card = resolved[h.rule_id]
                if card.outcome.type in allowed_types:
                    out.append((h, rs, card))
            return out

        def evaluate(batch: list[tuple[RuleHit, RuleSet, RuleCard]]) -> None:
            values.update(self.tools.inputs_for(task.context.record_id, [c for _, _, c in batch]))
            for hit, rs, card in sorted(batch, key=lambda x: x[2].order):
                findings[card.id] = self._finding(card, rs, hit, values, task, ignore_actuals)

        for step in playbook.steps:
            action = step["do"]
            if action == "recall_rules":
                candidates = recall(task.question, concepts, task.hints, step.get("k", self.settings.recall_k))
                report.steps.append(f"{self.name}: recall_rules {len(candidates)} candidates "
                                    f"({', '.join(h.rule_id for h, _, _ in candidates)})")
            elif action == "resolve_inputs":
                values.update(self.tools.inputs_for(task.context.record_id, [c for _, _, c in candidates]))
                report.steps.append(f"{self.name}: resolve_inputs {len(values)} fields")
            elif action == "evaluate":
                evaluate(candidates)
                fired = [f.rule_id for f in findings.values() if f.status == "fired"]
                report.steps.append(f"{self.name}: evaluate fired {fired or 'none'}")
            elif action == "apply_overrides":
                self._apply_overrides(findings, values, task, day, ignore_actuals, report)
            elif action == "widen_if_unexplained":
                pending = [t for t in task.outcome_tokens
                           if not any(f.status == "fired" and f.outcome_token == t for f in findings.values())]
                if pending and task.mode == "explain" and step.get("max", 1) > 0:
                    text = " ".join([task.question, *(self.kn.describe_token(t) for t in pending)])
                    extra = recall(text, sorted(self.scope), [], 2 * self.settings.recall_k)
                    report.widened = True
                    report.steps.append(f"{self.name}: widen_if_unexplained {len(extra)} more for {pending}")
                    if extra:
                        evaluate(extra)
                        self._apply_overrides(findings, values, task, day, ignore_actuals, report)
            elif action == "phrase":
                for f in findings.values():
                    card = self.kn.rules.rules[f.key]
                    f.narrative = render(card.template, values, f.amount) if f.status in ("fired", "overridden") else card.intent

        for f in findings.values():
            f.confidence = self._confidence(f, observed)
        report.findings = sorted(findings.values(), key=lambda f: (f.precedence, f.order))

    # ------------------------------------------------------------- helpers
    def _finding(self, card: RuleCard, rs: RuleSet, hit: RuleHit | None, values: dict[str, Evidence],
                 task: DomainTask, ignore_actuals: bool) -> Finding:
        ev = self.kn.evaluator.evaluate(card, values)
        evidence = [values[p] for p in card.inputs if p in values]
        status = "unverified" if ev.fired is None else ("fired" if ev.fired else "not_fired")
        return Finding(
            rule_id=card.id, version=card.version, rule_set=rs.id, domain=card.domain, concept=card.concept,
            agent=self.kn.taxonomy.agent_for(card.concept) or self.name, unit=self.name,
            intent=card.intent, outcome_type=card.outcome.type, outcome_token=card.outcome.token,
            status=status, mode=task.mode, rules_mode=task.context.rules_mode, order=card.order,
            precedence=self.kn.taxonomy.precedence(card.concept),
            amount=ev.amount, actual=None if ignore_actuals else ev.actual,
            evidence=evidence, error=ev.error,
            retrieval=Retrieval(score=round(hit.score, 3), path=hit.path) if hit else None,
        )

    def _apply_overrides(self, findings: dict[str, Finding], values: dict[str, Evidence], task: DomainTask,
                         day: date, ignore_actuals: bool, report: DomainReport) -> None:
        for f in list(findings.values()):
            card = self.kn.rules.rules[f.key]
            if f.status != "fired" or not card.overridden_by or f.overrides_checked:
                continue
            for oid in card.overridden_by:
                if oid in findings:
                    of = findings[oid]
                else:
                    resolved = self.kn.rules.resolve_rule(oid, day)
                    if resolved is None:
                        f.overrides_checked.append({"rule": oid, "matched": False, "note": "not in force"})
                        continue
                    rs, ocard = resolved
                    values.update(self.tools.inputs_for(task.context.record_id, [ocard]))
                    of = self._finding(ocard, rs, None, values, task, ignore_actuals)
                    of.narrative = render(ocard.template, values, of.amount) if of.status == "fired" else ocard.intent
                    findings[oid] = of
                matched = of.status == "fired"
                f.overrides_checked.append({"rule": of.key, "matched": matched, "intent": of.intent})
                if matched:
                    f.status = "overridden"
                    f.overridden_by = of.key
            report.steps.append(f"{self.name}: apply_overrides {f.rule_id} checked "
                                f"{[o['rule'] for o in f.overrides_checked]}")

    @staticmethod
    def _confidence(f: Finding, observed: set[str]) -> str | None:
        if f.status != "fired":
            return None
        if f.mode == "what_if":
            return "what_if"
        if f.outcome_type in ("info", "override"):
            return "supporting"
        if f.outcome_token in observed:
            if f.outcome_type == "pricing" and f.amount is not None and f.actual is not None \
                    and abs(f.amount - f.actual) > 0.005:
                f.note = f"rule expects {f.amount:.2f} but the record shows {f.actual:.2f}"
                return "likely"
            return "confirmed"
        f.note = f"rule fires, but the record does not show {f.outcome_token}"
        return "likely"

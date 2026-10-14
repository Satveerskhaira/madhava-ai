"""CI validation for knowledge changes (design doc section 6.3 step 4 and 8.4).

Deterministic checks, no model: every rule input exists in the field catalog,
expressions only read declared inputs and compile in CEL, outcome codes exist,
concepts and domains line up with the taxonomy, overrides exist, releases
reference real rule versions and do not overlap, and catalog fields sit on
ontology classes and taxonomy concepts.
"""

from __future__ import annotations

import re

from ..knowledge import Knowledge
from .evaluator import referenced_paths


def validate(kn: Knowledge) -> list[str]:
    issues: list[str] = []
    tax, cat = kn.taxonomy, kn.catalog

    for f in cat.fields.values():
        if not kn.ontology.has_class(f.entity):
            issues.append(f"field {f.path}: unknown ontology class {f.entity}")
        if f.concept not in tax.concepts:
            issues.append(f"field {f.path}: unknown concept {f.concept}")
        if f.source == "plan" and not f.pointer:
            issues.append(f"field {f.path}: plan field without pointer")
        for name, src in f.params.items():
            if "{" + name + "}" not in (f.pointer or ""):
                issues.append(f"field {f.path}: param {name} not used in pointer")
            if src not in cat:
                issues.append(f"field {f.path}: param source {src} not in catalog")

    for key, card in kn.rules.rules.items():
        where = f"rule {key}"
        declared = set(card.inputs)
        for p in card.claim_inputs:
            if not p.startswith("claim."):
                issues.append(f"{where}: claim input {p} is not a claim path")
        for p in card.plan_inputs:
            if not p.startswith("plan."):
                issues.append(f"{where}: plan input {p} is not a plan path")
        for p in declared:
            if p not in cat:
                issues.append(f"{where}: input {p} is not in the field catalog")
        exprs = {"condition": card.condition}
        if card.outcome.amount:
            exprs["amount"] = card.outcome.amount
        for label, expr in exprs.items():
            for p in referenced_paths(expr):
                if p not in declared:
                    issues.append(f"{where}: {label} reads undeclared path {p}")
            try:
                kn.evaluator.compile(expr)
            except Exception as exc:  # noqa: BLE001 - surface any parse error
                issues.append(f"{where}: {label} does not compile: {exc}")
        for p in re.findall(r"\{\{\s*([\w.]+)", card.template):
            if p != "amount" and p not in declared:
                issues.append(f"{where}: template uses undeclared path {p}")
        if card.outcome.actual and card.outcome.actual not in declared:
            issues.append(f"{where}: actual path {card.outcome.actual} not declared")
        if card.outcome.type == "reject" and card.outcome.code not in kn.reject_codes:
            issues.append(f"{where}: reject code {card.outcome.code} not in code table")
        if card.concept not in tax.concepts:
            issues.append(f"{where}: unknown concept {card.concept}")
        elif tax.get(card.concept).rule_domain != card.domain:
            issues.append(f"{where}: concept {card.concept} belongs to rule domain "
                          f"{tax.get(card.concept).rule_domain}, not {card.domain}")
        for o in card.overridden_by:
            if o not in kn.rules.rule_ids():
                issues.append(f"{where}: overridden_by {o} does not exist")
        if card.domain not in kn.rules.domains:
            issues.append(f"{where}: domain {card.domain} has no domain.yaml")

    for name, info in kn.rules.domains.items():
        for c in info.concepts:
            if c not in tax.concepts or tax.get(c).rule_domain != name:
                issues.append(f"domain {name}: concept {c} does not map to this rule domain")
        releases = sorted(info.releases, key=lambda r: r.effective_from)
        for a, b in zip(releases, releases[1:]):
            if a.status != "withdrawn" and b.status != "withdrawn" and (a.effective_to is None or a.effective_to > b.effective_from):
                issues.append(f"domain {name}: releases {a.id} and {b.id} overlap")
        for rs in releases:
            for key in rs.rules:
                if key not in kn.rules.rules:
                    issues.append(f"release {rs.id}: rule version {key} does not exist")
                elif kn.rules.rules[key].domain != name:
                    issues.append(f"release {rs.id}: {key} belongs to another domain")
                elif kn.rules.rules[key].status != "approved":
                    issues.append(f"release {rs.id}: {key} is not approved")

    for rd in {c.rule_domain for c in tax.concepts.values() if c.rule_domain}:
        if rd not in kn.rules.domains:
            issues.append(f"taxonomy: rule domain {rd} has no rules yet")
    return issues

"""Rule store: immutable rule versions and effective-dated rule sets per domain.

Locally this reads the Git-first layout under <pack>/domains. In production the
same objects can come from a database (rules and rule_sets collections);
nothing above this class changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from ..models import RuleCard, RuleSet


@dataclass
class DomainInfo:
    domain: str
    concepts: list[str]
    owner: str
    releases: list[RuleSet] = field(default_factory=list)


class RuleStore:
    def __init__(self, domains: dict[str, DomainInfo], rules: dict[str, RuleCard]):
        self.domains = domains
        self.rules = rules                       # key "R-PA-001@2" -> card
        self._latest: dict[str, RuleCard] = {}
        for card in rules.values():
            cur = self._latest.get(card.id)
            if cur is None or card.version > cur.version:
                self._latest[card.id] = card

    @classmethod
    def load(cls, directory: Path) -> "RuleStore":
        domains: dict[str, DomainInfo] = {}
        rules: dict[str, RuleCard] = {}
        for ddir in sorted(p for p in directory.iterdir() if p.is_dir()):
            meta = yaml.safe_load((ddir / "domain.yaml").read_text())
            releases = [RuleSet(domain=meta["domain"], **r) for r in meta.get("releases", [])]
            releases.sort(key=lambda r: r.effective_from)
            domains[meta["domain"]] = DomainInfo(meta["domain"], meta["concepts"], meta["owner"], releases)
            for f in sorted((ddir / "rules").glob("*.yaml")):
                card = RuleCard(**yaml.safe_load(f.read_text()))
                rules[card.key] = card
        return cls(domains, rules)

    # ------------------------------------------------------------- lookups
    def rule_ids(self) -> list[str]:
        return sorted(self._latest)

    def latest(self, rule_id: str) -> RuleCard:
        return self._latest[rule_id]

    def versions(self, rule_id: str) -> list[RuleCard]:
        return sorted((c for c in self.rules.values() if c.id == rule_id), key=lambda c: c.version)

    def rule_set(self, domain: str, day: date) -> RuleSet | None:
        """The release of a domain in force on a date (withdrawn sets never count)."""
        for rs in reversed(self.domains[domain].releases):
            if rs.status != "withdrawn" and rs.covers(day):
                return rs
        return None

    def rule_set_by_id(self, rule_set_id: str) -> RuleSet | None:
        for d in self.domains.values():
            for rs in d.releases:
                if rs.id == rule_set_id:
                    return rs
        return None

    def resolved(self, domains: set[str] | None, day: date) -> dict[str, tuple[RuleSet, RuleCard]]:
        """rule id -> (rule set, rule version) in force on `day` for the given domains."""
        out: dict[str, tuple[RuleSet, RuleCard]] = {}
        for name in (domains or set(self.domains)):
            if name not in self.domains:
                continue
            rs = self.rule_set(name, day)
            if rs is None:
                continue
            for key in rs.rules:
                card = self.rules[key]
                out[card.id] = (rs, card)
        return out

    def resolve_rule(self, rule_id: str, day: date) -> tuple[RuleSet, RuleCard] | None:
        card = self._latest.get(rule_id)
        if card is None:
            return None
        return self.resolved({card.domain}, day).get(rule_id)

    def history(self, domain: str) -> list[RuleSet]:
        return list(self.domains[domain].releases)

    def changes_after(self, domain: str, day: date, until: date) -> list[RuleSet]:
        """Releases that took effect after `day` and on or before `until`."""
        return [rs for rs in self.domains[domain].releases
                if rs.status != "withdrawn" and day < rs.effective_from <= until]

    def diff(self, domain: str, a: date, b: date) -> dict[str, list[str]]:
        sa, sb = self.rule_set(domain, a), self.rule_set(domain, b)
        ka = {self.rules[k].id: self.rules[k].version for k in (sa.rules if sa else [])}
        kb = {self.rules[k].id: self.rules[k].version for k in (sb.rules if sb else [])}
        return {
            "added": sorted(f"{r}@{kb[r]}" for r in kb.keys() - ka.keys()),
            "removed": sorted(f"{r}@{ka[r]}" for r in ka.keys() - kb.keys()),
            "changed": sorted(f"{r}@{ka[r]}->{kb[r]}" for r in ka.keys() & kb.keys() if ka[r] != kb[r]),
        }

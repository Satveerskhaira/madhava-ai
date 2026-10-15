"""Knowledge pack manifest (pack.yaml).

A knowledge pack is a directory holding everything domain-specific: taxonomy,
ontology, field catalog, code table, rule domains, procedures, intent
patterns and answer phrases. The kernel reads only the manifest's paths, so a
new domain is a new pack, not new kernel code.

    name: pharmacy-claims
    version: "2026.10"
    agent_prefix: pharmacy
    roots: {record: claim, reference: plan}
    nouns: {record: claim, reference: plan setup}
    codes_kind: reject
    files:
      taxonomy: taxonomy/adjudication.taxonomy.yaml
      ontology: ontology/pharmacy.ontology.yaml
      catalog: catalog/fields.yaml
      codes: catalog/reject_codes.yaml
      domains: domains
      investigator: procedures/investigator.yaml
      domain_planner: procedures/domain_planner.yaml
      intents: procedures/intents.yaml
    rules_modes: {as_decided: as adjudicated, current: current, as_of: as of}
    token_labels: {reject: "reject {value} ({meaning})", price: "{value} amount"}
    phrases: {amount_paid: "The member paid {actual} on this fill. Reason: {narrative} {cite}"}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PHRASES = {
    "primary_outcome": "Primary reason for {token}: {narrative} {cite}",
    "amount_paid": "The amount was {actual}. Reason: {narrative} {cite}",
    "supporting": "Also relevant: {narrative} {cite}",
    "overridden": "Did not apply: {intent} [{rule}].{reason}",
    "what_if": "If approved: {narrative} [{rule}, estimate from submitted amounts]",
    "conflict": "Check: {intent} fires under these rules, but the record does not show {token} [{rule}].",
    "unverified": "Could not verify {rule}: {error}.",
    "checked": "Checked with no issue{when}: {names}.",
    "gap": "Not explained: {token}. No approved rule explains this outcome, so it was logged for expert review rather than guessed.",
    "gap_scoped": "Not explained by this agent: {token}. No rule in this domain explains it; the core agent checks the other domains.",
    "out_of_scope": "Outside this agent's scope: {tokens}.",
    "reference_line": "Reference data: {id} version {version} (effective {effective_from}), as of {service_date}.",
    "agent_header": "{label} agent:",
}

DEFAULT_TOKEN_LABELS = {
    "reject": "reject {value} ({meaning})",
    "price": "{value} amount",
    "*": "{kind} {value}",
}


@dataclass
class PackManifest:
    root: Path
    name: str
    version: str
    agent_prefix: str
    record_root: str
    reference_root: str
    record_noun: str
    reference_noun: str
    codes_kind: str
    files: dict[str, str]
    rules_modes: dict[str, str]
    token_labels: dict[str, str]
    phrases: dict[str, str]
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path) -> "PackManifest":
        data = yaml.safe_load((root / "pack.yaml").read_text())
        roots = data.get("roots", {})
        nouns = data.get("nouns", {})
        return cls(
            root=root,
            name=data["name"],
            version=str(data.get("version", "0")),
            agent_prefix=data.get("agent_prefix", data["name"]),
            record_root=roots.get("record", "record"),
            reference_root=roots.get("reference", "reference"),
            record_noun=nouns.get("record", "record"),
            reference_noun=nouns.get("reference", "reference data"),
            codes_kind=data.get("codes_kind", "reject"),
            files=data["files"],
            rules_modes={"as_decided": "as decided", "current": "current", "as_of": "as of",
                         **data.get("rules_modes", {})},
            token_labels={**DEFAULT_TOKEN_LABELS, **data.get("token_labels", {})},
            phrases={**DEFAULT_PHRASES, **data.get("phrases", {})},
            extra={k: v for k, v in data.items() if k not in {
                "name", "version", "agent_prefix", "roots", "nouns", "codes_kind", "files", "rules_modes",
                "token_labels", "phrases"}},
        )

    def path(self, key: str) -> Path:
        return self.root / self.files[key]

    def has(self, key: str) -> bool:
        return key in self.files and (self.root / self.files[key]).exists()

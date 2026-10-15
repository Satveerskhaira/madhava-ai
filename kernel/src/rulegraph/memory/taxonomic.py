"""Taxonomic memory: the outcome taxonomy as a SKOS-style concept scheme.

Used to classify outcome tokens, recognise concepts in questions, decide which
agent owns a concept, order findings by adjudication precedence and expand to
related concepts in the gap round.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Concept:
    id: str
    prefLabel: str
    definition: str = ""
    broader: str | None = None
    narrower: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    altLabels: list[str] = field(default_factory=list)
    notations: list[str] = field(default_factory=list)
    agent: str | None = None
    precedence: int | None = None
    rule_domain: str | None = None


class Taxonomy:
    def __init__(self, scheme: dict[str, Any], concepts: list[Concept]):
        self.scheme = scheme
        self.concepts: dict[str, Concept] = {c.id: c for c in concepts}
        for c in concepts:
            if c.broader:
                self.concepts[c.broader].narrower.append(c.id)
        # Links to concepts from domain packages that are not loaded are kept aside, not followed.
        self.unresolved_related: dict[str, list[str]] = {}
        for c in concepts:
            missing = [r for r in c.related if r not in self.concepts]
            if missing:
                self.unresolved_related[c.id] = missing
                c.related = [r for r in c.related if r in self.concepts]
        # related is symmetric in SKOS
        for c in concepts:
            for r in c.related:
                other = self.concepts[r]
                if c.id not in other.related:
                    other.related.append(c.id)
        self._notation: dict[str, str] = {}
        for c in concepts:
            for n in c.notations:
                self._notation[n] = c.id
        labels: list[tuple[str, str]] = []
        for c in concepts:
            for label in {c.prefLabel, *c.altLabels}:
                labels.append((label.lower(), c.id))
        # longest labels first so "prior authorization" wins over "authorization"
        self._labels = sorted(labels, key=lambda x: -len(x[0]))

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, path: Path) -> "Taxonomy":
        data = yaml.safe_load(path.read_text())
        return cls.from_documents(data["scheme"], data["concepts"])

    @classmethod
    def from_documents(cls, scheme: dict[str, Any], concepts: list[dict[str, Any]]) -> "Taxonomy":
        return cls(scheme, [Concept(**c) for c in concepts])

    # ------------------------------------------------------------- navigation
    def get(self, concept_id: str) -> Concept:
        return self.concepts[concept_id]

    def ancestors(self, concept_id: str) -> list[str]:
        out: list[str] = []
        c = self.concepts[concept_id]
        while c.broader:
            out.append(c.broader)
            c = self.concepts[c.broader]
        return out

    def descendants(self, concept_id: str) -> list[str]:
        out: list[str] = []
        stack = list(self.concepts[concept_id].narrower)
        while stack:
            cid = stack.pop()
            out.append(cid)
            stack.extend(self.concepts[cid].narrower)
        return out

    def top_concept(self, concept_id: str) -> str:
        chain = [concept_id, *self.ancestors(concept_id)]
        return chain[-1]

    def agent_for(self, concept_id: str) -> str | None:
        for cid in [concept_id, *self.ancestors(concept_id)]:
            if self.concepts[cid].agent:
                return self.concepts[cid].agent
        return None

    def precedence(self, concept_id: str) -> int:
        for cid in [concept_id, *self.ancestors(concept_id)]:
            if self.concepts[cid].precedence is not None:
                return self.concepts[cid].precedence  # type: ignore[return-value]
        return 999

    @property
    def agents(self) -> dict[str, str]:
        """agent name -> top concept id, in adjudication order."""
        tops = [c for c in self.concepts.values() if c.agent]
        tops.sort(key=lambda c: c.precedence or 999)
        return {c.agent: c.id for c in tops}  # type: ignore[misc]

    def branch(self, agent: str) -> set[str]:
        top = self.agents[agent]
        return {top, *self.descendants(top)}

    def rule_domains(self, agent: str) -> set[str]:
        return {self.concepts[c].rule_domain for c in self.branch(agent) if self.concepts[c].rule_domain}  # type: ignore[misc]

    def concept_for_rule_domain(self, rule_domain: str) -> list[str]:
        return [c.id for c in self.concepts.values() if c.rule_domain == rule_domain]

    # -------------------------------------------------------- classification
    def classify(self, token: str) -> str | None:
        """Outcome token (reject:75, price:copay) -> concept id."""
        return self._notation.get(token)

    def match_text(self, text: str) -> list[str]:
        """Concepts whose labels appear in the text, longest labels first, no overlaps."""
        lowered = text.lower()
        taken: list[tuple[int, int]] = []
        found: list[str] = []
        for label, cid in self._labels:
            for m in re.finditer(r"(?<![\w-])" + re.escape(label) + r"(?![\w-])", lowered):
                span = (m.start(), m.end())
                if any(s < span[1] and span[0] < e for s, e in taken):
                    continue
                taken.append(span)
                if cid not in found:
                    found.append(cid)
        return found

    def synonyms(self, concept_id: str) -> list[str]:
        c = self.concepts[concept_id]
        return [c.prefLabel, *c.altLabels]

    # ---------------------------------------------------------------- export
    def to_jsonld(self) -> dict[str, Any]:
        base = self.scheme.get("namespace", "https://example.org/taxonomy/")
        graph: list[dict[str, Any]] = [{
            "@id": base + "scheme",
            "@type": "skos:ConceptScheme",
            "skos:prefLabel": self.scheme["prefLabel"],
            "owl:versionInfo": self.scheme.get("version"),
            "skos:hasTopConcept": [{"@id": base + c.id} for c in self.concepts.values() if not c.broader],
        }]
        for c in self.concepts.values():
            node: dict[str, Any] = {
                "@id": base + c.id,
                "@type": "skos:Concept",
                "skos:inScheme": {"@id": base + "scheme"},
                "skos:prefLabel": c.prefLabel,
            }
            if c.definition:
                node["skos:definition"] = c.definition
            if c.altLabels:
                node["skos:altLabel"] = c.altLabels
            if c.broader:
                node["skos:broader"] = {"@id": base + c.broader}
            if c.narrower:
                node["skos:narrower"] = [{"@id": base + n} for n in c.narrower]
            if c.related:
                node["skos:related"] = [{"@id": base + r} for r in c.related]
            if c.notations:
                node["skos:notation"] = c.notations
            graph.append(node)
        return {
            "@context": {
                "skos": "http://www.w3.org/2004/02/skos/core#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": graph,
        }

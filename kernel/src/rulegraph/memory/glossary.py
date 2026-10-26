"""Glossary: business terms, abbreviations and code meanings as a SKOS concept scheme.

The glossary is separate from the taxonomy. The taxonomy classifies outcomes
and decides which agent owns them; the glossary defines the words people use
("PA", "days supply", "OCC"), so people, search and models read the same
definitions. A term may point at the taxonomy concepts it is about.

Code meanings from the code table are added as glossary terms automatically
(`code-<value>`), so each meaning is written once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Term:
    id: str
    prefLabel: str
    definition: str
    altLabels: list[str] = field(default_factory=list)      # abbreviations and synonyms
    concepts: list[str] = field(default_factory=list)       # taxonomy concepts this term is about
    notation: str | None = None                              # e.g. a code value
    kind: str = "term"                                       # term | code
    source: str | None = None                                # where the definition comes from
    example: str | None = None


class Glossary:
    def __init__(self, scheme: dict[str, Any] | None, terms: list[Term]):
        self.scheme = scheme or {"id": "glossary", "prefLabel": "Glossary"}
        self.terms: dict[str, Term] = {t.id: t for t in terms}
        labels: list[tuple[str, str]] = []
        for t in terms:
            for label in {t.prefLabel, *t.altLabels}:
                labels.append((label.lower(), t.id))
        self._labels = sorted(labels, key=lambda x: -len(x[0]))

    @classmethod
    def from_documents(cls, scheme: dict[str, Any] | None, terms: list[dict[str, Any]],
                       codes: dict[str, str] | None = None, codes_kind: str = "code") -> "Glossary":
        out = [Term(**t) for t in terms]
        for code, meaning in (codes or {}).items():
            out.append(Term(id=f"code-{code}", prefLabel=f"{codes_kind.capitalize()} code {code}", definition=meaning,
                            notation=f"{codes_kind}:{code}", kind="code", source="code table"))
        return cls(scheme, out)

    def get(self, term_id: str) -> Term:
        return self.terms[term_id]

    def lookup(self, text: str) -> list[Term]:
        """Terms whose label or abbreviation appears in the text (whole words, longest first)."""
        low, found, taken = text.lower(), [], []
        for label, tid in self._labels:
            for m in re.finditer(rf"(?<![\w-]){re.escape(label)}(?![\w-])", low):
                span = range(m.start(), m.end())
                if any(set(span) & set(t) for t in taken):
                    continue
                taken.append(span)
                if tid not in found:
                    found.append(tid)
        return [self.terms[t] for t in found]

    def to_jsonld(self) -> dict[str, Any]:
        ns = self.scheme.get("namespace", "https://example.org/glossary/")
        graph: list[dict[str, Any]] = [{"@id": self.scheme["id"], "@type": "skos:ConceptScheme",
                                        "skos:prefLabel": self.scheme.get("prefLabel", "Glossary")}]
        for t in self.terms.values():
            node: dict[str, Any] = {"@id": ns + t.id, "@type": "skos:Concept", "skos:inScheme": {"@id": self.scheme["id"]},
                                    "skos:prefLabel": t.prefLabel, "skos:definition": t.definition}
            if t.altLabels:
                node["skos:altLabel"] = t.altLabels
            if t.notation:
                node["skos:notation"] = t.notation
            if t.example:
                node["skos:example"] = t.example
            if t.concepts:
                node["skos:closeMatch"] = [{"@id": f"concept:{c}"} for c in t.concepts]
            graph.append(node)
        return {"@context": {"skos": "http://www.w3.org/2004/02/skos/core#"}, "@graph": graph}

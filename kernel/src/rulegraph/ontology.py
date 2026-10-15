"""The domain ontology: classes and typed relations, exportable as JSON-LD."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Relation:
    name: str
    domain: str
    range: str
    aligns_with: str | None = None


class Ontology:
    def __init__(self, namespace: str, version: str, classes: dict[str, dict[str, Any]],
                 relations: dict[str, Relation]):
        self.namespace = namespace
        self.version = version
        self.classes = classes
        self.relations = relations

    @classmethod
    def load(cls, path: Path) -> "Ontology":
        return cls.from_dict(yaml.safe_load(path.read_text()))

    @classmethod
    def from_dict(cls, data: dict) -> "Ontology":
        relations = {
            name: Relation(name=name, domain=r["domain"], range=r["range"], aligns_with=r.get("aligns_with"))
            for name, r in data["relations"].items()
        }
        return cls(data["namespace"], str(data["version"]), data["classes"], relations)

    def check(self, relation: str, source_class: str, target_class: str) -> None:
        """Raise if an edge would break the ontology. Called while building the policy graph."""
        rel = self.relations.get(relation)
        if rel is None:
            raise ValueError(f"unknown relation {relation!r}")
        if (rel.domain, rel.range) != (source_class, target_class):
            raise ValueError(
                f"{relation} links {rel.domain} -> {rel.range}, not {source_class} -> {target_class}")

    def has_class(self, name: str) -> bool:
        return name in self.classes

    def to_jsonld(self) -> dict[str, Any]:
        ns = self.namespace
        graph: list[dict[str, Any]] = [
            {"@id": ns.rstrip("#"), "@type": "owl:Ontology", "owl:versionInfo": self.version}
        ]
        for name, spec in self.classes.items():
            node = {"@id": ns + name, "@type": "owl:Class", "rdfs:label": name,
                    "rdfs:comment": spec.get("description", "")}
            if spec.get("aligns_with"):
                node["skos:closeMatch"] = spec["aligns_with"]
            graph.append(node)
        for rel in self.relations.values():
            node = {"@id": ns + rel.name, "@type": "owl:ObjectProperty",
                    "rdfs:domain": {"@id": ns + rel.domain}, "rdfs:range": {"@id": ns + rel.range}}
            if rel.aligns_with:
                node["skos:exactMatch"] = rel.aligns_with
            graph.append(node)
        return {
            "@context": {
                "owl": "http://www.w3.org/2002/07/owl#",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
                "skos": "http://www.w3.org/2004/02/skos/core#",
            },
            "@graph": graph,
        }

"""Loads every kind of knowledge once and wires the memories together."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .catalog import FieldCatalog
from .memory.procedural import ProceduralMemory
from .memory.semantic import Document, SemanticMemory
from .memory.taxonomic import Taxonomy
from .ontology import Ontology
from .rules.evaluator import CelEvaluator
from .rules.graph import PolicyGraph
from .rules.store import RuleStore
from .settings import Settings


@dataclass
class Knowledge:
    taxonomy: Taxonomy
    ontology: Ontology
    catalog: FieldCatalog
    reject_codes: dict[str, str]
    rules: RuleStore
    procedures: ProceduralMemory
    semantic: SemanticMemory
    graph: PolicyGraph
    evaluator: CelEvaluator

    @classmethod
    def load(cls, settings: Settings | None = None) -> "Knowledge":
        settings = settings or Settings()
        root: Path = settings.knowledge_dir
        taxonomy = Taxonomy.load(root / "taxonomy" / "adjudication.taxonomy.yaml")
        ontology = Ontology.load(root / "ontology" / "madhava.ontology.yaml")
        catalog = FieldCatalog.load(root / "catalog" / "fields.yaml")
        codes = {str(k): v for k, v in yaml.safe_load((root / "catalog" / "reject_codes.yaml").read_text())["codes"].items()}
        rules = RuleStore.load(root / "domains")
        procedures = ProceduralMemory.load(root / "procedures")
        graph = PolicyGraph.build(ontology, taxonomy, rules, [(f.path, f.concept) for f in catalog.fields.values()])
        semantic = SemanticMemory(taxonomy)
        semantic.add(_documents(taxonomy, catalog, codes, rules))
        return cls(taxonomy, ontology, catalog, codes, rules, procedures, semantic, graph, CelEvaluator())

    def rule_concepts(self) -> dict[str, str]:
        return {rid: self.rules.latest(rid).concept for rid in self.rules.rule_ids()}

    def describe_token(self, token: str) -> str:
        kind, _, value = token.partition(":")
        if kind == "reject":
            meaning = self.reject_codes.get(value)
            return f"reject {value} ({meaning})" if meaning else f"reject {value} (code not in the reject code table)"
        if kind == "price":
            return f"{value} amount"
        return token


def _documents(taxonomy: Taxonomy, catalog: FieldCatalog, codes: dict[str, str], rules: RuleStore) -> list[Document]:
    docs: list[Document] = []
    for c in taxonomy.concepts.values():
        docs.append(Document(f"concept:{c.id}", "concept",
                             " ".join([c.prefLabel, c.definition, *c.altLabels]),
                             {"concept": c.id, "label": c.prefLabel, "definition": c.definition}))
    for f in catalog.fields.values():
        decoded = " ".join(str(v) for v in f.decoded_values.values())
        docs.append(Document(f"field:{f.path}", "field", f"{f.path.replace('.', ' ')} {f.description} {decoded}",
                             {"path": f.path, "concept": f.concept, "description": f.description,
                              "ncpdp": f.ncpdp}))
    for code, meaning in codes.items():
        docs.append(Document(f"code:{code}", "code", f"reject code {code} {meaning}",
                             {"code": code, "meaning": meaning}))
    for rid in rules.rule_ids():
        card = rules.latest(rid)
        refs = " ".join(f"{r.doc} {r.section or ''}" for r in card.source_refs)
        docs.append(Document(f"rule:{rid}", "rule", f"{card.intent} {card.template} {refs}",
                             {"rule_id": rid, "concept": card.concept, "domain": card.domain}))
    return docs

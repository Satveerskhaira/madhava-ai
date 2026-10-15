"""Load a knowledge pack once and wire the memories together."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .catalog import FieldCatalog
from .memory.procedural import ProceduralMemory
from .memory.semantic import Document, SemanticMemory
from .memory.taxonomic import Taxonomy
from .ontology import Ontology
from .pack import PackManifest
from .rules.evaluator import CelEvaluator
from .rules.graph import PolicyGraph
from .rules.store import RuleStore


@dataclass
class Knowledge:
    pack: PackManifest
    taxonomy: Taxonomy
    ontology: Ontology
    catalog: FieldCatalog
    codes: dict[str, str]
    rules: RuleStore
    procedures: ProceduralMemory
    semantic: SemanticMemory
    graph: PolicyGraph
    evaluator: CelEvaluator

    @classmethod
    def load(cls, pack_dir: Path, planner_procedures: Path | None = None) -> "Knowledge":
        """Load a pack. `planner_procedures` adds a core planner's procedure file kept outside the pack."""
        pack = PackManifest.load(Path(pack_dir))
        taxonomy = Taxonomy.load(pack.path("taxonomy"))
        ontology = Ontology.load(pack.path("ontology"))
        catalog = FieldCatalog.load(pack.path("catalog"))
        codes: dict[str, str] = {}
        if pack.has("codes"):
            codes = {str(k): v for k, v in yaml.safe_load(pack.path("codes").read_text())["codes"].items()}
        rules = RuleStore.load(pack.path("domains"))
        procedures = ProceduralMemory.load(pack, planner_procedures)
        graph = PolicyGraph.build(ontology, taxonomy, rules, [(f.path, f.concept) for f in catalog.fields.values()])
        semantic = SemanticMemory(taxonomy)
        semantic.add(_documents(taxonomy, catalog, codes, rules, pack.codes_kind))
        return cls(pack, taxonomy, ontology, catalog, codes, rules, procedures, semantic, graph,
                   CelEvaluator(pack.record_root, pack.reference_root))

    def rule_concepts(self) -> dict[str, str]:
        return {rid: self.rules.latest(rid).concept for rid in self.rules.rule_ids()}

    def describe_token(self, token: str) -> str:
        kind, _, value = token.partition(":")
        template = self.pack.token_labels.get(kind, self.pack.token_labels["*"])
        meaning = self.codes.get(value, "code not in the code table") if kind == self.pack.codes_kind else ""
        return template.format(kind=kind, value=value, meaning=meaning)


def _documents(taxonomy: Taxonomy, catalog: FieldCatalog, codes: dict[str, str], rules: RuleStore,
               codes_kind: str) -> list[Document]:
    docs: list[Document] = []
    for c in taxonomy.concepts.values():
        docs.append(Document(f"concept:{c.id}", "concept",
                             " ".join([c.prefLabel, c.definition, *c.altLabels]),
                             {"concept": c.id, "label": c.prefLabel, "definition": c.definition}))
    for f in catalog.fields.values():
        decoded = " ".join(str(v) for v in f.decoded_values.values())
        docs.append(Document(f"field:{f.path}", "field", f"{f.path.replace('.', ' ')} {f.description} {decoded}",
                             {"path": f.path, "concept": f.concept, "description": f.description, **f.extra}))
    for code, meaning in codes.items():
        docs.append(Document(f"code:{code}", "code", f"{codes_kind} code {code} {meaning}",
                             {"code": code, "meaning": meaning, "kind": codes_kind}))
    for rid in rules.rule_ids():
        card = rules.latest(rid)
        refs = " ".join(f"{r.doc} {r.section or ''}" for r in card.source_refs)
        docs.append(Document(f"rule:{rid}", "rule", f"{card.intent} {card.template} {refs}",
                             {"rule_id": rid, "concept": card.concept, "domain": card.domain}))
    return docs

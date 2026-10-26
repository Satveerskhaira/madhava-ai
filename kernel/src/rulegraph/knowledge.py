"""Load a knowledge pack once and wire the memories together."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .catalog import FieldCatalog
from .memory.glossary import Glossary
from .memory.procedural import ProceduralMemory
from .memory.semantic import Document, SemanticMemory
from .memory.taxonomic import Taxonomy
from .ontology import Ontology
from .pack import PackManifest
from .rules.evaluator import CelEvaluator
from .rules.graph import PolicyGraph
from .rules.store import RuleStore
from .sources import DirectorySource, KnowledgeSource, PackDocuments, compose, policy_key


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
    origin: str = ""
    glossary: Glossary | None = None
    policies: dict[str, dict[str, Any]] | None = None     # policy section id -> {doc, section, title, text, ...}
    documents: PackDocuments | None = None                # the composed documents this was built from

    @classmethod
    def load(cls, pack_dir: Path, planner_procedures: Path | None = None) -> "Knowledge":
        """Load one pack directory. `planner_procedures` adds a core planner's procedure file kept outside the pack."""
        return cls.compose([DirectorySource(Path(pack_dir))], planner_procedures)

    @classmethod
    def compose(cls, sources: Iterable[KnowledgeSource | PackDocuments],
                planner_procedures: Path | dict[str, Any] | None = None) -> "Knowledge":
        """A base pack plus any number of domain fragments, from any sources (files, database, memory)."""
        docs = [s if isinstance(s, PackDocuments) else s.load() for s in sources]
        return cls.from_documents(compose(docs), planner_procedures)

    @classmethod
    def from_documents(cls, docs: PackDocuments,
                       planner_procedures: Path | dict[str, Any] | None = None) -> "Knowledge":
        if docs.scheme is None or docs.ontology is None or docs.investigator is None:
            raise ValueError(f"pack {docs.name}: a base pack needs a taxonomy scheme, an ontology and an "
                             "investigator playbook")
        pack = PackManifest.from_dict(docs.manifest, docs.root)
        taxonomy = Taxonomy.from_documents(docs.scheme, docs.concepts)
        ontology = Ontology.from_dict(docs.ontology)
        catalog = FieldCatalog.from_entries(docs.fields)
        codes = {str(k): v for k, v in docs.codes.items()}
        rules = RuleStore.from_documents(docs.domains)
        procedures = ProceduralMemory.from_documents(docs.investigator, docs.domain_planner, docs.intents,
                                                     planner_procedures)
        glossary = Glossary.from_documents(docs.glossary_scheme, docs.terms, codes, pack.codes_kind)
        policies = {policy_key(p): dict(p) for p in docs.policies}
        graph = PolicyGraph.build(
            ontology, taxonomy, rules, [(f.path, f.concept) for f in catalog.fields.values()],
            field_params={f.path: list(f.params.values()) for f in catalog.fields.values() if f.params},
            policies=list(policies), terms=[(t.id, t.concepts) for t in glossary.terms.values() if t.kind == "term"])
        semantic = SemanticMemory(taxonomy)
        semantic.add(_documents(taxonomy, catalog, codes, rules, pack.codes_kind, glossary, policies))
        return cls(pack, taxonomy, ontology, catalog, codes, rules, procedures, semantic, graph,
                   CelEvaluator(pack.record_root, pack.reference_root), docs.origin, glossary, policies, docs)

    def rule_concepts(self) -> dict[str, str]:
        return {rid: self.rules.latest(rid).concept for rid in self.rules.rule_ids()}

    def sources_for(self, rule_ids: Iterable[str]) -> list[dict[str, Any]]:
        """The policy passages behind these rules (latest versions), for citing next to an answer."""
        out, seen = [], set()
        for rid in rule_ids:
            if rid not in self.rules.rule_ids():
                continue
            for ref in self.rules.latest(rid).source_refs:
                key = ref.doc + (f" / {ref.section}" if ref.section else "")
                if (rid, key) in seen:
                    continue
                seen.add((rid, key))
                p = (self.policies or {}).get(key, {})
                out.append({"rule": rid, "policy": key, "doc": ref.doc, "section": ref.section,
                            "title": p.get("title"), "text": p.get("text"), "url": p.get("url")})
        return out

    def describe_token(self, token: str) -> str:
        kind, _, value = token.partition(":")
        template = self.pack.token_labels.get(kind, self.pack.token_labels["*"])
        meaning = self.codes.get(value, "code not in the code table") if kind == self.pack.codes_kind else ""
        return template.format(kind=kind, value=value, meaning=meaning)


def _documents(taxonomy: Taxonomy, catalog: FieldCatalog, codes: dict[str, str], rules: RuleStore,
               codes_kind: str, glossary: Glossary | None = None,
               policies: dict[str, dict[str, Any]] | None = None) -> list[Document]:
    docs: list[Document] = []
    for t in (glossary.terms.values() if glossary else []):
        if t.kind == "term":
            docs.append(Document(f"term:{t.id}", "term", " ".join([t.prefLabel, t.definition, *t.altLabels]),
                                 {"term": t.id, "label": t.prefLabel, "definition": t.definition,
                                  "abbreviations": t.altLabels, "concepts": t.concepts}))
    for key, p in (policies or {}).items():
        docs.append(Document(f"policy:{key}", "policy", f"{p.get('title', '')} {p.get('text', '')}",
                             {"policy": key, "doc": p["doc"], "section": p.get("section"), "title": p.get("title"),
                              "text": p.get("text")}))
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

"""JSON Schemas for every knowledge file type, and a checker over composed pack documents.

The same schemas serve three places: editors (YAML language server, via
`rulegraph schemas --out DIR`), CI (`validate`), and a database (for example
MongoDB `$jsonSchema` collection validators).
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

NAMES = ["pack", "concept", "term", "ontology", "field", "rule-domain", "rule-card", "policy", "procedures",
         "investigator", "intents", "glossary"]


@lru_cache(maxsize=None)
def load(name: str) -> dict[str, Any]:
    return json.loads((resources.files("rulegraph.schemas") / f"{name}.schema.json").read_text())


def _validator(name: str):
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    registry = Registry().with_resources(
        (load(n)["$id"].rsplit("/", 1)[0] + f"/{n}.schema.json", Resource.from_contents(load(n))) for n in NAMES)
    return Draft202012Validator(load(name), registry=registry)


def check(name: str, doc: Any, where: str) -> list[str]:
    return [f"{where}: {'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
            for e in _validator(name).iter_errors(doc)]


def schema_issues(docs: Any) -> list[str]:
    """Schema problems in composed PackDocuments (rulegraph.sources.PackDocuments)."""
    issues = check("pack", docs.manifest, f"pack {docs.name}")
    for c in docs.concepts:
        issues += check("concept", c, f"concept {c.get('id')}")
    for t in docs.terms:
        issues += check("term", t, f"glossary term {t.get('id')}")
    if docs.ontology:
        issues += check("ontology", docs.ontology, "ontology")
    for f in docs.fields:
        issues += check("field", f, f"field {f.get('path')}")
    for d in docs.domains:
        meta = {k: v for k, v in d.items() if k != "rules"}
        issues += check("rule-domain", meta, f"domain {d.get('domain')}")
        for card in d.get("rules", []):
            issues += check("rule-card", card, f"rule {card.get('id')}@{card.get('version')}")
    for p in docs.policies:
        issues += check("policy", p, f"policy {p.get('doc')} / {p.get('section')}")
    if docs.domain_planner:
        issues += check("procedures", docs.domain_planner, "domain planner procedures")
    if docs.investigator:
        issues += check("investigator", docs.investigator, "investigator playbook")
    if docs.intents:
        issues += check("intents", docs.intents, "intents")
    return issues

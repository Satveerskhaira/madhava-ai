"""Knowledge sources: where packs come from, and how a base pack and domain fragments combine.

A *base* pack holds what every domain shares: manifest (roots, nouns, wording),
ontology, code table, the top of the taxonomy (one concept per domain agent,
with decision order), common catalog fields, and the shared procedures.

A *fragment* adds one or more domains to a base: taxonomy concepts under the
base's top concepts, catalog fields, rule domains with their rules, and
per-agent investigator notes. Each domain can live in its own repository and
release on its own; `compose` merges them and refuses conflicts.

Every source produces the same plain documents (dicts), so a pack can come
from files in Git today and from a database (for example MongoDB collections)
later without changing anything above this module:

    DirectorySource(path)     pack.yaml + YAML files (the Git layout)
    DocumentSource(docs)      documents already loaded (from a database, an API, a test)

A database source only needs a `load() -> PackDocuments` that fills the same fields.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from .rules.store import read_domain_documents


@dataclass
class PackDocuments:
    name: str
    kind: str = "base"                                  # base | fragment
    extends: str | None = None                          # fragments: the base pack name
    manifest: dict[str, Any] = field(default_factory=dict)
    scheme: dict[str, Any] | None = None                # taxonomy scheme (base)
    concepts: list[dict[str, Any]] = field(default_factory=list)
    ontology: dict[str, Any] | None = None              # {namespace, version, classes, relations}
    fields: list[dict[str, Any]] = field(default_factory=list)
    codes: dict[str, str] = field(default_factory=dict)
    domains: list[dict[str, Any]] = field(default_factory=list)   # domain.yaml content + "rules": [card, ...]
    investigator: dict[str, Any] | None = None          # {default: {...}, agents: {name: {notes}}}
    domain_planner: dict[str, Any] | None = None        # {procedures: {...}}
    intents: dict[str, Any] | None = None               # {patterns: {...}, cost_words: ...}
    origin: str = ""
    root: Path | None = None


class KnowledgeSource(Protocol):
    def load(self) -> PackDocuments: ...


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


class DirectorySource:
    """A pack directory: pack.yaml with `files` pointing at YAML files (any subset for a fragment)."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def load(self) -> PackDocuments:
        manifest = _yaml(self.root / "pack.yaml")
        files = manifest.get("files", {})

        def f(key: str) -> Path | None:
            p = self.root / files[key] if key in files else None
            return p if p and p.exists() else None

        tax = _yaml(f("taxonomy")) if f("taxonomy") else {}
        codes = _yaml(f("codes"))["codes"] if f("codes") else {}
        return PackDocuments(
            name=manifest["name"], kind=manifest.get("kind", "base"), extends=manifest.get("extends"),
            manifest=manifest, scheme=tax.get("scheme"), concepts=tax.get("concepts", []),
            ontology=_yaml(f("ontology")) if f("ontology") else None,
            fields=(_yaml(f("catalog")) or {}).get("fields", []) if f("catalog") else [],
            codes={str(k): v for k, v in (codes or {}).items()},
            domains=read_domain_documents(f("domains")) if f("domains") else [],
            investigator=_yaml(f("investigator")) if f("investigator") else None,
            domain_planner=_yaml(f("domain_planner")) if f("domain_planner") else None,
            intents=_yaml(f("intents")) if f("intents") else None,
            origin=str(self.root), root=self.root,
        )


class DocumentSource:
    """Documents already in memory, for example read from database collections."""

    def __init__(self, documents: PackDocuments):
        self.documents = documents

    def load(self) -> PackDocuments:
        return copy.deepcopy(self.documents)


class CompositionError(ValueError):
    pass


def compose(packs: list[PackDocuments]) -> PackDocuments:
    """Merge one base pack with any number of fragments. Raises CompositionError listing every conflict."""
    bases = [p for p in packs if p.kind == "base"]
    if len(bases) != 1:
        raise CompositionError(f"need exactly one base pack, got {[p.name for p in bases]}")
    base = copy.deepcopy(bases[0])
    errors: list[str] = []
    concept_owner = {c["id"]: base.name for c in base.concepts}
    field_owner = {fd["path"]: (base.name, fd) for fd in base.fields}
    domain_owner = {d["domain"]: base.name for d in base.domains}
    notation_owner = {n: c["id"] for c in base.concepts for n in c.get("notations", [])}
    names = {base.name}
    for frag in (p for p in packs if p.kind != "base"):
        if frag.name in names:
            errors.append(f"pack {frag.name} added twice")
            continue
        names.add(frag.name)
        if frag.extends != base.name:
            errors.append(f"fragment {frag.name} extends {frag.extends!r}, not base {base.name!r}")
            continue
        for c in frag.concepts:
            if c["id"] in concept_owner:
                errors.append(f"concept {c['id']} defined by {concept_owner[c['id']]} and {frag.name}")
                continue
            concept_owner[c["id"]] = frag.name
            for n in c.get("notations", []):
                if n in notation_owner:
                    errors.append(f"notation {n} on {notation_owner[n]} and {c['id']} ({frag.name})")
                notation_owner[n] = c["id"]
            base.concepts.append(c)
        for fd in frag.fields:
            owner = field_owner.get(fd["path"])
            if owner and owner[1] != fd:
                errors.append(f"field {fd['path']} defined differently by {owner[0]} and {frag.name}")
            elif not owner:
                field_owner[fd["path"]] = (frag.name, fd)
                base.fields.append(fd)
        for code, meaning in frag.codes.items():
            if code in base.codes and base.codes[code] != meaning:
                errors.append(f"code {code} has different meanings in {base.name} and {frag.name}")
            base.codes.setdefault(code, meaning)
        for d in frag.domains:
            if d["domain"] in domain_owner:
                errors.append(f"rule domain {d['domain']} defined by {domain_owner[d['domain']]} and {frag.name}")
                continue
            domain_owner[d["domain"]] = frag.name
            base.domains.append(d)
        if frag.ontology:
            onto = base.ontology or {"classes": {}, "relations": {}}
            for kind in ("classes", "relations"):
                for k, v in (frag.ontology.get(kind) or {}).items():
                    if k in onto[kind] and onto[kind][k] != v:
                        errors.append(f"ontology {kind[:-1] if kind == 'classes' else 'relation'} {k} "
                                      f"differs in {frag.name}")
                    onto[kind].setdefault(k, v)
        if frag.investigator:
            if frag.investigator.get("default"):
                errors.append(f"fragment {frag.name} may only add investigator notes under `agents`")
            inv = base.investigator or {}
            inv.setdefault("agents", {})
            for agent, spec in (frag.investigator.get("agents") or {}).items():
                inv["agents"][agent] = spec
            base.investigator = inv
        for key in ("domain_planner", "intents", "scheme"):
            if getattr(frag, key):
                errors.append(f"fragment {frag.name} may not set {key}; it belongs to the base pack")
    if errors:
        raise CompositionError("; ".join(errors))
    base.origin = " + ".join([base.name, *(p.name for p in packs if p.kind != "base")])
    return base

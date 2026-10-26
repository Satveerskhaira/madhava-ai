"""MongoDB knowledge store (optional: pip install "rulegraph[mongo]").

Git stays where knowledge is authored and reviewed; CI publishes each pack (a base pack and its domain
fragments) to MongoDB, and the application loads the published release.

    publish(db, [base_docs, *fragment_docs], release="current")    # write documents, then flip the release
    sources = mongo_sources(db, release="current")                 # one source per published pack
    kn = Knowledge.compose(sources, planner_procedures=...)

Collections (one document per item, all tagged with `pack` and `pack_version`):

    packs         manifest and base-only parts (scheme, ontology, procedures, intents, codes, playbook)
    concepts      taxonomy concepts and glossary terms (`scheme`: taxonomy | glossary)
    fields        field catalog entries
    rule_domains  domain.yaml content with releases
    rules         one immutable rule card version per document (_id ends with R-PA-001@2)
    policies      policy sections with text
    releases      {_id: <release>, packs: [{name, version}]}: what the application loads

Publishing writes every document first and updates the release document last, so a reader sees either
the old release or the new one, never a half-published mix. Values are JSON-safe: dates become ISO strings.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from ..sources import PackDocuments, policy_key

COLLECTIONS = ["packs", "concepts", "fields", "rule_domains", "rules", "policies", "releases"]


def _plain(value: Any) -> Any:
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def publish(db: Any, packs: list[PackDocuments], release: str = "current", note: str = "") -> dict[str, Any]:
    """Write each pack's documents, then point `release` at exactly these pack versions."""
    written: dict[str, int] = {}

    def put(coll: str, _id: str, doc: dict[str, Any]) -> None:
        db[coll].replace_one({"_id": _id}, _plain({"_id": _id, **doc}), upsert=True)
        written[coll] = written.get(coll, 0) + 1

    entries = []
    for p in packs:
        version = str(p.manifest.get("version", "0"))
        tag = {"pack": p.name, "pack_version": version}
        pid = f"{p.name}@{version}"
        put("packs", pid, {**tag, "name": p.name, "version": version, "kind": p.kind, "extends": p.extends,
                           "manifest": p.manifest, "scheme": p.scheme, "glossary_scheme": p.glossary_scheme,
                           "ontology": p.ontology, "investigator": p.investigator,
                           "domain_planner": p.domain_planner, "intents": p.intents,
                           "codes": [{"code": k, "meaning": v} for k, v in p.codes.items()]})
        for c in p.concepts:
            put("concepts", f"{pid}:taxonomy:{c['id']}", {**tag, "scheme": "taxonomy", "doc": c})
        for t in p.terms:
            put("concepts", f"{pid}:glossary:{t['id']}", {**tag, "scheme": "glossary", "doc": t})
        for f in p.fields:
            put("fields", f"{pid}:{f['path']}", {**tag, "doc": f})
        for d in p.domains:
            meta = {k: v for k, v in d.items() if k != "rules"}
            put("rule_domains", f"{pid}:{d['domain']}", {**tag, "domain": d["domain"], "doc": meta})
            for card in d.get("rules", []):
                put("rules", f"{pid}:{card['id']}@{card['version']}",
                    {**tag, "domain": d["domain"], "rule_id": card["id"], "doc": card})
        for pol in p.policies:
            put("policies", f"{pid}:{policy_key(pol)}", {**tag, "doc": pol})
        entries.append({"name": p.name, "version": version, "kind": p.kind})
    db["releases"].replace_one({"_id": release}, {"_id": release, "packs": entries, "note": note,
                                                  "published_at": _dt.datetime.now(_dt.timezone.utc).isoformat()},
                               upsert=True)
    return {"release": release, "packs": entries, "documents": written}


class MongoSource:
    """One published pack version, read back as the same documents a DirectorySource produces."""

    def __init__(self, db: Any, name: str, version: str):
        self.db, self.name, self.version = db, name, version

    def load(self) -> PackDocuments:
        q = {"pack": self.name, "pack_version": self.version}
        head = self.db["packs"].find_one({"_id": f"{self.name}@{self.version}"})
        if head is None:
            raise LookupError(f"pack {self.name}@{self.version} is not published")
        concepts = list(self.db["concepts"].find({**q, "scheme": "taxonomy"}).sort("_id", 1))
        terms = list(self.db["concepts"].find({**q, "scheme": "glossary"}).sort("_id", 1))
        domains = []
        for d in self.db["rule_domains"].find(q).sort("_id", 1):
            meta = dict(d["doc"])
            meta["rules"] = [r["doc"] for r in self.db["rules"].find({**q, "domain": d["domain"]}).sort("_id", 1)]
            domains.append(meta)
        return PackDocuments(
            name=self.name, kind=head["kind"], extends=head.get("extends"), manifest=head["manifest"],
            scheme=head.get("scheme"), concepts=[c["doc"] for c in concepts], ontology=head.get("ontology"),
            fields=[f["doc"] for f in self.db["fields"].find(q).sort("_id", 1)],
            codes={c["code"]: c["meaning"] for c in head.get("codes", [])}, domains=domains,
            investigator=head.get("investigator"), domain_planner=head.get("domain_planner"),
            intents=head.get("intents"), glossary_scheme=head.get("glossary_scheme"),
            terms=[t["doc"] for t in terms],
            policies=[p["doc"] for p in self.db["policies"].find(q).sort("_id", 1)],
            origin=f"mongodb:{self.name}@{self.version}")


def mongo_sources(db: Any, release: str = "current") -> list[MongoSource]:
    """Sources for every pack in a published release (base first)."""
    rel = db["releases"].find_one({"_id": release})
    if rel is None:
        raise LookupError(f"release {release!r} is not published")
    packs = sorted(rel["packs"], key=lambda p: p["kind"] != "base")
    return [MongoSource(db, p["name"], p["version"]) for p in packs]


def ensure_indexes(db: Any) -> None:
    for coll in ("concepts", "fields", "rule_domains", "rules", "policies"):
        db[coll].create_index([("pack", 1), ("pack_version", 1)])
    db["rules"].create_index([("rule_id", 1)])
    db["concepts"].create_index([("scheme", 1)])

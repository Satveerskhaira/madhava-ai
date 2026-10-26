"""A local, offline browser for a knowledge set: one self-contained HTML file, no server, no CDN.

    rulegraph view <pack> [--fragment DIR ...] --out knowledge.html
    madhava view --out knowledge.html          # the composed pharmacy knowledge

Pages: overview, interactive graph, taxonomy, ontology, glossary, rules (versions, CEL, sources),
fields, policies, outcomes. Every item links to the items it is connected to.
"""

from __future__ import annotations

import datetime as _dt
import json
from importlib import resources
from typing import Any

from .knowledge import Knowledge


def view_data(kn: Knowledge, title: str | None = None) -> dict[str, Any]:
    tax, rules = kn.taxonomy, kn.rules
    edges = kn.graph.edges()
    readers: dict[str, list[str]] = {}
    derived: dict[str, list[str]] = {}
    for s, r, d in edges:
        if r == "reads":
            readers.setdefault(d.split(":", 1)[1], []).append(s.split(":", 1)[1])
        if r == "derived_from":
            derived.setdefault(d.split(":", 1)[1], []).append(s.split(":", 1)[1])
    rulesets_of: dict[str, list[str]] = {}
    rulesets = []
    for info in rules.domains.values():
        for rs in info.releases:
            rulesets.append({"id": rs.id, "domain": info.domain, "owner": info.owner, "from": rs.effective_from.isoformat(),
                             "to": rs.effective_to.isoformat() if rs.effective_to else None, "status": rs.status,
                             "reason": rs.reason, "previous": rs.previous, "rules": rs.rules})
            for key in rs.rules:
                rulesets_of.setdefault(key, []).append(rs.id)
    concepts = []
    for c in tax.concepts.values():
        concepts.append({"id": c.id, "label": c.prefLabel, "definition": c.definition, "broader": c.broader,
                         "narrower": c.narrower, "related": c.related, "alt": c.altLabels, "notations": c.notations,
                         "agent": c.agent or tax.agent_for(c.id), "isTop": bool(c.agent), "precedence": tax.precedence(c.id),
                         "ruleDomain": c.rule_domain,
                         "rules": sorted({r.id for r in rules.rules.values() if r.concept == c.id})})
    rule_list = []
    for rid in sorted(rules.rule_ids()):
        versions = sorted((c for c in rules.rules.values() if c.id == rid), key=lambda c: c.version)
        latest = versions[-1]
        rule_list.append({
            "id": rid, "domain": latest.domain, "concept": latest.concept, "agent": tax.agent_for(latest.concept),
            "versions": [{"version": c.version, "status": c.status, "intent": c.intent, "condition": c.condition.strip(),
                          "recordInputs": c.record_inputs, "referenceInputs": c.reference_inputs,
                          "outcome": c.outcome.model_dump(exclude_none=True), "token": c.outcome.token,
                          "overriddenBy": c.overridden_by, "template": " ".join(c.template.split()), "order": c.order,
                          "confidence": c.confidence, "owner": c.sme_owner,
                          "sources": [r.doc + (f" / {r.section}" if r.section else "") for r in c.source_refs],
                          "rulesets": rulesets_of.get(c.key, [])} for c in versions],
            "overrides": sorted({c.id for c in rules.rules.values() if rid in c.overridden_by})})
    fields = [{"path": f.path, "source": f.source, "entity": f.entity, "type": f.type, "concept": f.concept,
               "description": f.description, "pointer": f.pointer, "params": f.params,
               "readBy": sorted(set(readers.get(f.path, []))), **{k: v for k, v in f.extra.items() if isinstance(v, str)}}
              for f in kn.catalog.fields.values()]
    policies = []
    keys = set((kn.policies or {})) | set(derived)
    for key in sorted(keys):
        p = (kn.policies or {}).get(key, {})
        policies.append({"key": key, "doc": p.get("doc", key.split(" / ")[0]), "section": p.get("section"),
                         "title": p.get("title"), "text": p.get("text"), "owner": p.get("owner"),
                         "version": str(p["version"]) if p.get("version") is not None else None,
                         "effective": str(p["effective_from"]) if p.get("effective_from") else None,
                         "sample": bool(p.get("sample")), "rules": sorted(set(derived.get(key, [])))})
    tokens = {n for c in tax.concepts.values() for n in c.notations} | {
        c.outcome.token for c in rules.rules.values() if c.outcome.token} | {f"{kn.pack.codes_kind}:{k}" for k in kn.codes}
    outcomes = []
    for tok in sorted(tokens):
        kind, _, value = tok.partition(":")
        outcomes.append({"token": tok, "meaning": kn.codes.get(value) if kind == kn.pack.codes_kind else None,
                         "concept": tax.classify(tok),
                         "rules": sorted({c.id for c in rules.rules.values() if c.outcome.token == tok})})
    terms = [{"id": t.id, "label": t.prefLabel, "definition": t.definition, "alt": t.altLabels, "concepts": t.concepts,
              "kind": t.kind, "notation": t.notation, "example": t.example, "source": t.source}
             for t in (kn.glossary.terms.values() if kn.glossary else [])]
    label = {f"concept:{c['id']}": c["label"] for c in concepts}
    label.update({f"term:{t['id']}": t["label"] for t in terms})
    # group every node under the domain agent it belongs to ("shared" for base-level knowledge)
    def agent_of_concept(cid: str | None) -> str | None:
        return tax.agent_for(cid) if cid and cid in tax.concepts else None
    group: dict[str, str] = {}
    for c in concepts:
        group[f"concept:{c['id']}"] = c["agent"] or "shared"
    for r in rule_list:
        group[f"rule:{r['id']}"] = r["agent"] or "shared"
    for f in kn.catalog.fields.values():
        agents = {agent_of_concept(rules.latest(x).concept) for x in readers.get(f.path, []) if x in rules.rule_ids()}
        group[f"field:{f.path}"] = agents.pop() if len(agents) == 1 else (agent_of_concept(f.concept) if not agents and agent_of_concept(f.concept) and not tax.get(f.concept).agent else "shared")
    for o in outcomes:
        group[f"outcome:{o['token']}"] = agent_of_concept(o["concept"]) or "shared"
    for p_ in policies:
        agents = {agent_of_concept(rules.latest(x).concept) for x in p_["rules"] if x in rules.rule_ids()}
        group[f"policy:{p_['key']}"] = agents.pop() if len(agents) == 1 else "shared"
    for rs in rulesets:
        lc = next((c for c in rules.domains[rs["domain"]].concepts), None)
        group[f"ruleset:{rs['id']}"] = agent_of_concept(lc) or "shared"
    for t in terms:
        agents = {agent_of_concept(c) for c in t["concepts"]} - {None}
        group[f"term:{t['id']}"] = agents.pop() if len(agents) == 1 and not all(tax.get(c).agent for c in t["concepts"]) else "shared"
    nodes = [{"id": n, "kind": n.split(":", 1)[0], "label": label.get(n, n.split(":", 1)[1]),
              "group": group.get(n, "shared")} for n in sorted(kn.graph.nodes)]
    order = [c["agent"] for c in sorted((c for c in concepts if c["isTop"]), key=lambda c: c["precedence"])]
    agent_label = {c["agent"]: c["label"] for c in concepts if c["isTop"]}
    onto = kn.ontology
    return {
        "meta": {"title": title or f"{kn.pack.name} knowledge", "pack": kn.pack.name, "origin": kn.origin,
                 "generated": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "recordNoun": kn.pack.record_noun,
                 "referenceNoun": kn.pack.reference_noun, "taxonomyScheme": tax.scheme.get("prefLabel"),
                 "glossaryScheme": kn.glossary.scheme.get("prefLabel") if kn.glossary else None,
                 "namespace": onto.namespace},
        "concepts": concepts, "terms": terms,
        "ontology": {"classes": [{"name": k, "description": v.get("description", ""),
                                  "aligns": v.get("aligns_with")} for k, v in onto.classes.items()],
                     "relations": [{"name": r.name, "domain": r.domain, "range": r.range, "aligns": r.aligns_with}
                                   for r in onto.relations.values()]},
        "fields": fields, "rules": rule_list, "rulesets": rulesets, "policies": policies, "outcomes": outcomes,
        "graph": {"nodes": nodes, "edges": [[s, r, d] for s, r, d in edges],
                  "groups": [{"id": a, "label": agent_label.get(a, a)} for a in order]},
    }


def render_html(kn: Knowledge, title: str | None = None) -> str:
    data = view_data(kn, title)
    page = (resources.files("rulegraph") / "viewer.html").read_text()
    blob = json.dumps(data, separators=(",", ":"), default=str).replace("</", "<\\/")
    return page.replace("__TITLE__", data["meta"]["title"]).replace("__DATA__", blob)

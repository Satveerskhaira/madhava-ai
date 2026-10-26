"""Export the whole knowledge graph in standard formats.

    knowledge_graph(kn)        one JSON-LD document: ontology (OWL), taxonomy and glossary (SKOS),
                               fields, outcomes, rule versions (with CEL conditions and PROV-O sources),
                               rule sets and policy sections, all linked
    to_turtle(doc)             the same graph as RDF Turtle (needs rdflib: pip install "rulegraph[rdf]")
    shacl_shapes(kn)           SHACL shapes for that graph, in the pack's ontology namespace
    shacl_validate(kn)         run the shapes against the graph (needs pyshacl)
    to_graphml(kn)             the policy graph for graph tools (yEd, Gephi, Cytoscape)

Classes and object properties come from the pack ontology, so the export uses the same vocabulary the
ontology declares. Data properties (path, pointer, condition ...) are declared in the export itself.
"""

from __future__ import annotations

import re
from typing import Any
from xml.sax.saxutils import escape

from .knowledge import Knowledge

# Data properties the kernel writes on graph nodes (declared as owl:DatatypeProperty in the export).
DATA_PROPERTIES = {
    "ruleId": "Rule identifier, shared by all versions", "version": "Rule version number",
    "status": "Approval status of a rule version", "condition": "CEL condition",
    "conditionLanguage": "Expression language of the condition", "amount": "CEL expression for an expected amount",
    "template": "Answer wording with {{path}} placeholders", "order": "Order within the rule domain",
    "confidence": "Drafting confidence", "owner": "Owning team", "ruleDomain": "Rule domain",
    "path": "Catalog path", "pointer": "JSON Pointer into the reference payload", "source": "record or reference",
    "dataType": "Field value type", "entity": "Ontology class the field sits on",
    "effectiveFrom": "First day in force", "effectiveTo": "First day no longer in force (exclusive)",
    "releaseStatus": "candidate, active, archived or withdrawn", "reason": "Why a release was made",
    "changeRef": "Change ticket", "section": "Section of a source document", "text": "Source text",
    "sample": "Synthetic sample content", "precedence": "Decision order (lower runs first)",
    "agent": "Domain agent that owns a taxonomy branch", "codeMeaning": "Meaning of an outcome code",
}
STD = {
    "owl": "http://www.w3.org/2002/07/owl#", "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "skos": "http://www.w3.org/2004/02/skos/core#", "prov": "http://www.w3.org/ns/prov#",
    "dct": "http://purl.org/dc/terms/", "xsd": "http://www.w3.org/2001/XMLSchema#",
}


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")


class _Iri:
    def __init__(self, kn: Knowledge):
        self.onto = kn.ontology.namespace
        stem = self.onto.rstrip("#/")
        self.kg = (stem.rsplit("/", 1)[0] if "/" in stem.split("//", 1)[-1] else stem) + "/kg/"
        self.tax = kn.taxonomy.scheme.get("namespace", self.kg + "concept/")
        self.gloss = (kn.glossary.scheme.get("namespace") if kn.glossary else None) or self.kg + "term/"

    def concept(self, cid: str) -> str: return self.tax + cid
    def term(self, tid: str) -> str: return self.gloss + tid
    def outcome(self, token: str) -> str: return self.kg + "outcome/" + "/".join(map(_slug, token.split(":", 1)))
    def field(self, path: str) -> str: return self.kg + "field/" + path
    def rule(self, rid: str, version: int) -> str: return f"{self.kg}rule/{rid}/v{version}"
    def ruleset(self, rsid: str) -> str: return self.kg + "ruleset/" + _slug(rsid)
    def policy(self, key: str) -> str: return self.kg + "policy/" + _slug(key)


def knowledge_graph(kn: Knowledge) -> dict[str, Any]:
    iri, o = _Iri(kn), "o:"
    ref = lambda i: {"@id": i}                                                       # noqa: E731
    graph: list[dict[str, Any]] = []

    # ontology: classes, object properties (from the pack), data properties (kernel)
    onto = kn.ontology.to_jsonld()["@graph"]
    graph.extend(onto)
    for name, comment in DATA_PROPERTIES.items():
        graph.append({"@id": o + name, "@type": "owl:DatatypeProperty", "rdfs:comment": comment})

    # taxonomy (SKOS) with agent and precedence on top concepts
    for node in kn.taxonomy.to_jsonld()["@graph"]:
        cid = node["@id"].removeprefix(iri.tax)
        c = kn.taxonomy.concepts.get(cid)
        if c is not None:
            node["@type"] = ["skos:Concept", o + "Concept"]
            if c.agent:
                node[o + "agent"] = c.agent
                node[o + "precedence"] = c.precedence
            if c.rule_domain:
                node[o + "ruleDomain"] = c.rule_domain
            node.pop("skos:notation", None)
            if c.notations:
                node[o + "classifies"] = [ref(iri.outcome(n)) for n in c.notations]
        graph.append(node)

    # glossary (SKOS), linked to taxonomy concepts
    if kn.glossary:
        g = kn.glossary
        graph.append({"@id": iri.gloss + "scheme", "@type": "skos:ConceptScheme",
                      "skos:prefLabel": g.scheme.get("prefLabel", "Glossary")})
        for t in g.terms.values():
            node: dict[str, Any] = {"@id": iri.term(t.id), "@type": ["skos:Concept", o + "Term"],
                                    "skos:inScheme": ref(iri.gloss + "scheme"), "skos:prefLabel": t.prefLabel,
                                    "skos:definition": t.definition}
            if t.altLabels:
                node["skos:altLabel"] = t.altLabels
            if t.example:
                node["skos:example"] = t.example
            if t.notation:
                node["skos:notation"] = t.notation
            if t.concepts:
                node[o + "about"] = [ref(iri.concept(c)) for c in t.concepts if c in kn.taxonomy.concepts]
            graph.append(node)

    # outcomes
    tokens = {n for c in kn.taxonomy.concepts.values() for n in c.notations}
    tokens |= {card.outcome.token for card in kn.rules.rules.values() if card.outcome.token}
    tokens |= {f"{kn.pack.codes_kind}:{code}" for code in kn.codes}
    for tok in sorted(tokens):
        kind, _, value = tok.partition(":")
        node = {"@id": iri.outcome(tok), "@type": o + "Outcome", "skos:notation": tok, "rdfs:label": tok}
        if kind == kn.pack.codes_kind and value in kn.codes:
            node[o + "codeMeaning"] = kn.codes[value]
        graph.append(node)

    # fields
    for f in kn.catalog.fields.values():
        node = {"@id": iri.field(f.path), "@type": o + "Field", "rdfs:label": f.path, o + "path": f.path,
                o + "source": f.source, o + "dataType": f.type, o + "entity": f.entity,
                "rdfs:comment": f.description, o + "describes": ref(iri.concept(f.concept))}
        if f.pointer:
            node[o + "pointer"] = f.pointer
        if f.params:
            node[o + "depends_on"] = [ref(iri.field(p)) for p in f.params.values()]
        graph.append(node)

    # policy sections (provenance entities)
    policies = dict(kn.policies or {})
    for card in kn.rules.rules.values():
        for r in card.source_refs:
            key = r.doc + (f" / {r.section}" if r.section else "")
            policies.setdefault(key, {"doc": r.doc, "section": r.section})
    for key, p in policies.items():
        node = {"@id": iri.policy(key), "@type": [o + "Policy", "prov:Entity"], "rdfs:label": key,
                "dct:title": p.get("title") or key, "dct:source": p["doc"]}
        for k_src, k_dst in (("section", o + "section"), ("text", o + "text"), ("owner", o + "owner"),
                             ("url", "rdfs:seeAlso"), ("version", "owl:versionInfo"), ("sample", o + "sample")):
            if p.get(k_src) not in (None, ""):
                node[k_dst] = p[k_src]
        if p.get("effective_from"):
            node[o + "effectiveFrom"] = {"@value": str(p["effective_from"]), "@type": "xsd:date"}
        graph.append(node)

    # rule sets
    membership: dict[str, list[str]] = {}
    for info in kn.rules.domains.values():
        for rs in info.releases:
            node = {"@id": iri.ruleset(rs.id), "@type": o + "RuleSet", "rdfs:label": rs.id,
                    o + "ruleDomain": info.domain, o + "owner": info.owner, o + "releaseStatus": rs.status,
                    o + "effectiveFrom": {"@value": rs.effective_from.isoformat(), "@type": "xsd:date"}}
            if rs.effective_to:
                node[o + "effectiveTo"] = {"@value": rs.effective_to.isoformat(), "@type": "xsd:date"}
            if rs.reason:
                node[o + "reason"] = rs.reason
            if rs.change_ref:
                node[o + "changeRef"] = rs.change_ref
            if rs.previous:
                node[o + "supersedes"] = ref(iri.ruleset(rs.previous))
            graph.append(node)
            for key in rs.rules:
                membership.setdefault(key, []).append(iri.ruleset(rs.id))

    # rule versions
    for card in kn.rules.rules.values():
        node = {"@id": iri.rule(card.id, card.version), "@type": [o + "Rule", "prov:Entity"],
                "rdfs:label": card.key, o + "ruleId": card.id, o + "version": card.version,
                o + "status": card.status, "rdfs:comment": card.intent, o + "ruleDomain": card.domain,
                o + "in_domain": ref(iri.concept(card.concept)),
                o + "condition": card.condition.strip(), o + "conditionLanguage": "CEL",
                o + "order": card.order, o + "confidence": card.confidence,
                o + "reads": [ref(iri.field(p)) for p in card.inputs],
                "prov:wasDerivedFrom": [ref(iri.policy(r.doc + (f" / {r.section}" if r.section else "")))
                                        for r in card.source_refs]}
        if card.template:
            node[o + "template"] = " ".join(card.template.split())
        if card.outcome.token:
            node[o + "explains"] = ref(iri.outcome(card.outcome.token))
        if card.outcome.amount:
            node[o + "amount"] = card.outcome.amount.strip()
        if card.overridden_by:
            node[o + "overridden_by"] = [ref(iri.rule(x, kn.rules.latest(x).version))
                                         for x in card.overridden_by if x in kn.rules.rule_ids()]
        if card.sme_owner:
            node[o + "owner"] = card.sme_owner
        if card.key in membership:
            node[o + "member_of"] = [ref(i) for i in membership[card.key]]
        if card.version > 1 and f"{card.id}@{card.version - 1}" in kn.rules.rules:
            node["prov:wasRevisionOf"] = ref(iri.rule(card.id, card.version - 1))
        graph.append(node)

    graph.insert(0, {"@id": iri.kg, "@type": "prov:Collection", "dct:title": f"{kn.pack.name} knowledge graph",
                     "dct:source": kn.origin})
    # @graph at the top level with no sibling @id, so every node lands in the default graph
    return {"@context": {**STD, "o": kn.ontology.namespace}, "@graph": graph}


def to_turtle(doc: dict[str, Any]) -> str:
    import json

    from rdflib import Graph
    g = Graph()
    g.parse(data=json.dumps(doc), format="json-ld")
    return g.serialize(format="turtle")


SHAPES = """@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix o:    <{ns}> .

# Every rule version: identity, a CEL condition, its domain concept, the fields it reads, and a source.
o:RuleShape a sh:NodeShape ;
  sh:targetClass o:Rule ;
  sh:property [ sh:path o:ruleId ; sh:minCount 1 ; sh:maxCount 1 ; sh:datatype xsd:string ] ;
  sh:property [ sh:path o:version ; sh:minCount 1 ; sh:maxCount 1 ; sh:datatype xsd:integer ] ;
  sh:property [ sh:path o:status ; sh:minCount 1 ; sh:in ( "draft" "in_review" "approved" "deprecated" ) ] ;
  sh:property [ sh:path o:condition ; sh:minCount 1 ; sh:maxCount 1 ; sh:minLength 1 ] ;
  sh:property [ sh:path o:in_domain ; sh:minCount 1 ; sh:maxCount 1 ; sh:class o:Concept ] ;
  sh:property [ sh:path o:reads ; sh:class o:Field ] ;
  sh:property [ sh:path o:explains ; sh:maxCount 1 ; sh:class o:Outcome ] ;
  sh:property [ sh:path o:overridden_by ; sh:class o:Rule ] ;
  sh:property [ sh:path o:member_of ; sh:class o:RuleSet ] ;
  sh:property [ sh:path prov:wasDerivedFrom ; sh:minCount 1 ; sh:class o:Policy ] .

# Every catalog field: a path, a source, the concept it describes; reference fields need a JSON Pointer.
o:FieldShape a sh:NodeShape ;
  sh:targetClass o:Field ;
  sh:property [ sh:path o:path ; sh:minCount 1 ; sh:maxCount 1 ] ;
  sh:property [ sh:path o:source ; sh:minCount 1 ; sh:in ( "record" "reference" ) ] ;
  sh:property [ sh:path o:describes ; sh:minCount 1 ; sh:class o:Concept ] ;
  sh:property [ sh:path o:depends_on ; sh:class o:Field ] .
o:ReferenceFieldShape a sh:NodeShape ;
  sh:targetClass o:Field ;
  sh:or ( [ sh:path o:source ; sh:hasValue "record" ]
          [ sh:path o:pointer ; sh:minCount 1 ] ) .

# Taxonomy concepts and glossary terms are defined.
o:ConceptShape a sh:NodeShape ;
  sh:targetClass o:Concept, o:Term ;
  sh:property [ sh:path skos:prefLabel ; sh:minCount 1 ] ;
  sh:property [ sh:path skos:definition ; sh:minCount 1 ] .
o:TermShape a sh:NodeShape ;
  sh:targetClass o:Term ;
  sh:property [ sh:path o:about ; sh:class o:Concept ] .

# Rule sets are effective-dated.
o:RuleSetShape a sh:NodeShape ;
  sh:targetClass o:RuleSet ;
  sh:property [ sh:path o:effectiveFrom ; sh:minCount 1 ; sh:maxCount 1 ; sh:datatype xsd:date ] ;
  sh:property [ sh:path o:supersedes ; sh:class o:RuleSet ] .

# Policy sections a rule cites carry their text once the pack keeps policy text.
o:PolicyShape a sh:NodeShape ;
  sh:targetClass o:Policy ;
  sh:property [ sh:path o:text ; sh:minCount {policy_text} ] .
"""


def shacl_shapes(kn: Knowledge) -> str:
    return SHAPES.replace("{ns}", kn.ontology.namespace).replace("{policy_text}", "1" if kn.policies else "0")


def shacl_validate(kn: Knowledge) -> tuple[bool, str]:
    """(conforms, report text). Needs pyshacl and rdflib."""
    import json

    from pyshacl import validate as run
    from rdflib import Graph
    data = Graph().parse(data=json.dumps(knowledge_graph(kn)), format="json-ld")
    if len(data) < len(kn.rules.rules):                     # never report an empty graph as conforming
        return False, f"knowledge graph parsed to only {len(data)} triples"
    shapes = Graph().parse(data=shacl_shapes(kn), format="turtle")
    conforms, _, text = run(data, shacl_graph=shapes, inference="none")
    return bool(conforms), str(text)


def to_graphml(kn: Knowledge) -> str:
    """The policy graph (rule search graph plus structural edges) as GraphML."""
    def label(node: str) -> str:
        kind, _, rest = node.partition(":")
        if kind == "concept" and rest in kn.taxonomy.concepts:
            return kn.taxonomy.get(rest).prefLabel
        if kind == "term" and kn.glossary and rest in kn.glossary.terms:
            return kn.glossary.get(rest).prefLabel
        return rest
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
           '<key id="kind" for="node" attr.name="kind" attr.type="string"/>',
           '<key id="label" for="node" attr.name="label" attr.type="string"/>',
           '<key id="relation" for="edge" attr.name="relation" attr.type="string"/>',
           '<graph id="knowledge" edgedefault="directed">']
    for n in sorted(kn.graph.nodes):
        out.append(f'<node id="{escape(n, {chr(34): "&quot;"})}"><data key="kind">{escape(n.split(":", 1)[0])}</data>'
                   f'<data key="label">{escape(label(n))}</data></node>')
    for i, (s, r, d) in enumerate(kn.graph.edges()):
        out.append(f'<edge id="e{i}" source="{escape(s, {chr(34): "&quot;"})}" target="{escape(d, {chr(34): "&quot;"})}">'
                   f'<data key="relation">{escape(r)}</data></edge>')
    out += ["</graph>", "</graphml>"]
    return "\n".join(out) + "\n"

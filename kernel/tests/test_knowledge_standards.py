"""Glossary, policy text, structural edges, JSON Schema, SHACL, exports, MongoDB and the viewer,
checked on a freshly scaffolded project so the kernel is tested without any one domain's knowledge."""

import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from rulegraph import DirectorySource, Knowledge
from rulegraph.export import knowledge_graph, shacl_shapes, shacl_validate, to_graphml, to_turtle
from rulegraph.rules.graph import STRUCTURAL, Seed
from rulegraph.rules.validate import validate
from rulegraph.scaffold import scaffold
from rulegraph.schemas import check
from rulegraph.sources import CompositionError, PackDocuments, compose


@pytest.fixture(scope="module")
def pack(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("std")
    scaffold("subagents", root / "p", "shop_std")
    return root / "p" / "src" / "shop_std" / "pack"


@pytest.fixture(scope="module")
def kn(pack) -> Knowledge:
    return Knowledge.load(pack)


def test_scaffolded_pack_is_clean_under_every_check(kn):
    assert validate(kn) == []                                    # includes JSON Schema, glossary and policy checks
    assert {t.id for t in kn.glossary.terms.values() if t.kind == "term"} >= {"credit-limit", "shipping-fee"}
    assert "code-CR1" in kn.glossary.terms                       # code meanings become glossary terms
    assert set(kn.policies) == {"Credit Policy / 2.1", "Fulfillment Guide / 1.3", "Fulfillment Guide / 1.4",
                                "Rate Card / Shipping"}


def test_schemas_catch_malformed_documents():
    bad_rule = {"id": "R-1", "version": 1, "status": "approved", "domain": "d", "concept": "c", "intent": "x",
                "outcome": {"type": "reject", "code": "X"}}         # no condition, no source
    assert any("condition" in i for i in check("rule-card", bad_rule, "rule R-1"))
    bad_field = {"path": "terms.x", "source": "reference", "entity": "E", "type": "number", "concept": "c",
                 "description": "no pointer"}
    assert any("pointer" in i for i in check("field", bad_field, "field terms.x"))
    assert check("term", {"id": "Bad Id", "prefLabel": "x", "definition": ""}, "term")


def test_validate_reports_uncited_policy_and_unknown_term_concept(pack):
    docs = DirectorySource(pack).load()
    docs.policies = [p for p in docs.policies if p["doc"] != "Rate Card"]
    docs.terms.append({"id": "ghost", "prefLabel": "Ghost", "definition": "x", "concepts": ["no.such"]})
    issues = validate(Knowledge.from_documents(docs))
    assert any("source 'Rate Card / Shipping' has no policy section" in i for i in issues)
    assert any("glossary term ghost: unknown concept no.such" in i for i in issues)


def test_fragments_add_terms_and_policies_but_never_twice(pack):
    base = DirectorySource(pack).load()
    frag = PackDocuments(name="extra", kind="fragment", extends=base.name,
                         terms=[{"id": "credit-limit", "prefLabel": "Again", "definition": "dup"}])
    with pytest.raises(CompositionError, match="glossary term credit-limit"):
        compose([base, frag])
    frag2 = PackDocuments(name="extra2", kind="fragment", extends=base.name,
                          policies=[{"doc": "Credit Policy", "section": "2.1", "text": "dup"}])
    with pytest.raises(CompositionError, match="policy section Credit Policy / 2.1"):
        compose([base, frag2])


def test_structural_edges_exist_but_never_steer_rule_search(kn):
    rels = {r for _, r, _ in kn.graph.edges()}
    assert {"depends_on", "member_of", "about"} <= rels
    for token in ("reject:CR1", "reject:FL1", "price:shipping"):
        for hit in kn.graph.search([Seed(f"outcome:{token}", 1.0, token)], rule_concept=kn.rule_concepts()):
            assert not any(s in step for step in hit.path for s in STRUCTURAL), hit.path


def test_answers_carry_their_policy_sources(tmp_path, pack):
    import importlib
    import sys
    root = pack.parents[2]
    sys.path.insert(0, str(root / "src"))
    try:
        mod = importlib.import_module("shop_std")
        from datetime import date

        from rulegraph import Settings
        system = mod.build(Settings(today=date(2026, 10, 14)))
        import asyncio
        exp = asyncio.run(system.agents["credit"].ask("Why was this order rejected?", "ORD-1"))
        assert exp.primary.rule_id == "R-CR-001"
        assert exp.sources and exp.sources[0]["policy"] == "Credit Policy / 2.1" and "credit limit" in exp.sources[0]["text"]
        definition = asyncio.run(system.agents["credit"].ask("What is the credit limit?"))
        assert definition.answer.startswith("Credit limit (limit):")
    finally:
        sys.path.remove(str(root / "src"))
        for name in [m for m in sys.modules if m.startswith("shop_std")]:
            del sys.modules[name]


def test_knowledge_graph_exports_to_rdf_and_conforms_to_shacl(kn):
    pytest.importorskip("rdflib")
    pytest.importorskip("pyshacl")
    from rdflib import RDF, Graph, URIRef
    doc = knowledge_graph(kn)
    g = Graph().parse(data=to_turtle(doc), format="turtle")
    o = kn.ontology.namespace
    assert len(list(g.subjects(RDF.type, URIRef(o + "Rule")))) == len(kn.rules.rules)
    assert len(list(g.subjects(RDF.type, URIRef(o + "Policy")))) == len(kn.policies)
    ok, report = shacl_validate(kn)
    assert ok, report
    assert "sh:targetClass o:Rule" in shacl_shapes(kn)


def test_shacl_rejects_a_rule_without_a_source(kn, monkeypatch):
    pytest.importorskip("pyshacl")
    import rulegraph.export as ex
    real = ex.knowledge_graph

    def broken(k):
        doc = copy.deepcopy(real(k))
        for node in doc["@graph"]:
            if node.get("o:ruleId") == "R-CR-001":
                node.pop("prov:wasDerivedFrom")
        return doc
    monkeypatch.setattr(ex, "knowledge_graph", broken)
    ok, report = ex.shacl_validate(kn)
    assert not ok and "wasDerivedFrom" in report


def test_graphml_is_well_formed(kn):
    root = ET.fromstring(to_graphml(kn))
    ns = {"g": "http://graphml.graphdrawing.org/xmlns"}
    assert len(root.findall(".//g:node", ns)) == len(kn.graph.nodes)
    assert len(root.findall(".//g:edge", ns)) == kn.graph.edge_count


def test_mongodb_round_trip_gives_the_same_knowledge(pack, kn):
    mongomock = pytest.importorskip("mongomock")
    from rulegraph.adapters.mongodb import ensure_indexes, mongo_sources, publish
    db = mongomock.MongoClient()["k"]
    ensure_indexes(db)
    result = publish(db, [DirectorySource(pack).load()], release="current")
    assert result["documents"]["rules"] == len(kn.rules.rules)
    back = Knowledge.compose(mongo_sources(db, "current"))
    assert validate(back) == []
    assert sorted(back.rules.rules) == sorted(kn.rules.rules)
    assert (len(back.graph.nodes), back.graph.edge_count) == (len(kn.graph.nodes), kn.graph.edge_count)
    assert back.policies == kn.policies
    with pytest.raises(LookupError):
        mongo_sources(db, "missing")


def test_viewer_renders_one_self_contained_page(kn):
    from rulegraph.viewer import render_html, view_data
    data = view_data(kn)
    assert {g["id"] for g in data["graph"]["groups"]} == {"credit", "fulfillment", "charges"}
    html = render_html(kn)
    assert "__DATA__" not in html and "__TITLE__" not in html and "<script src" not in html
    blob = html.split('<script id="data" type="application/json">', 1)[1].split("</script>", 1)[0]
    assert json.loads(blob.replace("<\\/", "</"))["meta"]["pack"] == kn.pack.name


def test_cli_view_export_schemas(pack, tmp_path):
    from rulegraph.cli import main
    assert main(["--pack", str(pack), "validate"]) == 0
    assert main(["--pack", str(pack), "view", "--no-open", "--out", str(tmp_path / "k.html")]) == 0
    assert main(["--pack", str(pack), "export", "graph", "--out", str(tmp_path / "k.jsonld")]) == 0
    assert json.loads((tmp_path / "k.jsonld").read_text())["@graph"]
    assert main(["schemas", "--out", str(tmp_path / "schemas")]) == 0
    assert len(list((tmp_path / "schemas").glob("*.schema.json"))) == 12


def test_knowledge_service_serves_mongodb_releases_and_the_live_page(pack, kn):
    mongomock = pytest.importorskip("mongomock")
    from rulegraph.adapters.mongodb import publish
    from rulegraph.service import KnowledgeService
    db = mongomock.MongoClient()["k"]
    publish(db, [DirectorySource(pack).load()], release="current")
    svc = KnowledgeService(db=db, title="Shop")

    status, ctype, body, _ = svc.handle("/", {})
    assert status == 200 and b'type="application/json">null</script>' in body and b"api/releases" in body
    status, _, body, _ = svc.handle("/api/releases", {})
    assert json.loads(body)["releases"][0]["release"] == "current"
    status, _, body, headers = svc.handle("/api/releases/current/view", {})
    view = json.loads(body)
    assert status == 200 and view["meta"]["source"] == "mongodb" and len(view["rules"]) == len(kn.rules.rule_ids())
    assert len(view["graph"]["edges"]) == kn.graph.edge_count
    assert svc.handle("/api/releases/current/view", {}, headers["ETag"])[0] == 304       # unchanged release

    rule = json.loads(svc.handle("/api/releases/current/rules/R-CR-001", {})[2])
    assert "Credit Policy / 2.1" in rule["policyText"]
    focus = json.loads(svc.handle("/api/releases/current/graph", {"focus": ["rule:R-CR-001"]})[2])
    assert {"rule:R-CR-001", "policy:Credit Policy / 2.1"} <= {n["id"] for n in focus["nodes"]}
    hits = json.loads(svc.handle("/api/releases/current/search", {"q": ["credit limit"]})[2])
    assert hits and hits[0]["node"] == "term:credit-limit"
    assert svc.handle("/api/releases/current/export/knowledge.graphml", {})[0] == 200
    assert svc.handle("/api/releases/missing/view", {})[0] == 404

    # a republish is picked up on the next request, with a new ETag
    publish(db, [DirectorySource(pack).load()], release="current", note="again")
    assert svc.handle("/api/releases/current/view", {}, headers["ETag"])[0] == 200


def test_knowledge_service_serves_files_too(kn):
    from rulegraph.service import KnowledgeService
    svc = KnowledgeService(files=lambda: kn)
    assert json.loads(svc.handle("/api/releases/files/meta", {})[2])["source"] == "files"

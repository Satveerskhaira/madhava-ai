"""The composed pharmacy knowledge: glossary, policy sources, standard exports, MongoDB and the browser."""

import asyncio

import pytest

import pharmacy_base
from rulegraph import Knowledge
from rulegraph.rules.validate import validate

from madhava import build, procedures_path


def run(coro):
    return asyncio.run(coro)


def test_glossary_answers_abbreviations_exactly(system):
    assert run(system.core.ask("What is PA?")).answer.startswith("Prior authorization (PA, prior auth")
    assert run(system.core.ask("What does OCC mean?")).answer.startswith("Other coverage code (OCC)")
    assert run(system.core.ask("What does reject code 76 mean?")).answer == "Reject code 76: Plan Limitations Exceeded"


def test_answers_cite_the_policy_text_behind_their_rules(system):
    exp = run(system.core.ask("Why was this rejected?", "RX-1001"))
    sources = {s["policy"]: s for s in exp.sources}
    assert "Plan Setup Guide v8 / 4.2 Prior Auth" in sources
    assert "prior authorization tier" in sources["Plan Setup Guide v8 / 4.2 Prior Auth"]["text"]
    assert all(s["text"] for s in exp.sources)


def test_every_domain_contributes_terms_and_policies(system):
    kn = system.knowledge
    terms = {t.id for t in kn.glossary.terms.values() if t.kind == "term"}
    assert {"prior-authorization", "copay", "other-coverage-code", "refill-too-soon", "formulary-status"} <= terms
    assert len(kn.policies) == 19 and validate(kn) == []


def test_knowledge_graph_conforms_to_its_shacl_shapes(system):
    pytest.importorskip("pyshacl")
    from rulegraph.export import shacl_validate
    ok, report = shacl_validate(system.knowledge)
    assert ok, report


def test_published_to_mongodb_the_core_answers_the_same(system):
    mongomock = pytest.importorskip("mongomock")
    from rulegraph.adapters.mongodb import mongo_sources, publish
    db = mongomock.MongoClient()["knowledge"]
    publish(db, [pharmacy_base.base_source().load(), *[p.source.load() for p in pharmacy_base.discover_domains()]])
    kn = Knowledge.compose(mongo_sources(db), planner_procedures=procedures_path())
    assert (len(kn.graph.nodes), kn.graph.edge_count) == (len(system.knowledge.graph.nodes), system.knowledge.graph.edge_count)
    exp = run(build(system.settings, knowledge=kn).core.ask("Why did this reject?", "RX-1007"))
    assert exp.primary.rule_id == "R-NET-001"


def test_browser_page_holds_every_domain(system, tmp_path):
    from madhava.cli import main
    out = tmp_path / "k.html"
    assert main(["view", "--no-open", "--out", str(out)]) == 0
    html = out.read_text()
    assert "Utilization management" in html and "R-PA-001" in html and "<script src" not in html

from datetime import date

import pytest

from madhava.rules.graph import PolicyGraph, Seed
from madhava.rules.validate import validate


def test_knowledge_validates_cleanly(knowledge):
    assert validate(knowledge) == []


def test_taxonomy_classifies_outcomes_and_owns_agents(knowledge):
    tax = knowledge.taxonomy
    assert tax.classify("reject:75") == "um.prior_authorization"
    assert tax.agent_for("um.prior_authorization") == "utilization_management"
    assert tax.classify("price:deductible") == "cost_share.deductible"
    assert tax.classify("reject:85") is None
    # adjudication order comes from top-concept precedence
    assert tax.precedence("eligibility.member_coverage") < tax.precedence("um.prior_authorization")
    # skos:related is symmetric
    assert "um.quantity_limits" in tax.get("network.specialty_channel").related


def test_question_concepts_prefer_longest_labels(knowledge):
    found = knowledge.taxonomy.match_text("Is prior authorization needed, or is it a plan limit?")
    assert found[:2] == ["um.prior_authorization", "um.quantity_limits"]
    # "pa" must not match inside "pay"
    assert "um.prior_authorization" not in knowledge.taxonomy.match_text("why did the member pay this?")


def test_jsonld_exports(knowledge):
    tax = knowledge.taxonomy.to_jsonld()
    assert tax["@context"]["skos"].startswith("http://www.w3.org/2004/02/skos")
    pa = next(n for n in tax["@graph"] if n["@id"].endswith("um.prior_authorization"))
    assert pa["@type"] == "skos:Concept" and "reject:75" in pa["skos:notation"]
    onto = knowledge.ontology.to_jsonld()
    assert any(n["@type"] == "owl:ObjectProperty" and n["@id"].endswith("explains") for n in onto["@graph"])


def test_ontology_blocks_invalid_edges(knowledge):
    g = PolicyGraph(knowledge.ontology)
    with pytest.raises(ValueError):
        g.add_edge("field:claim.dos", "explains", "outcome:reject:75")   # only rules explain outcomes


def test_rule_sets_resolve_by_date(knowledge):
    store = knowledge.rules
    assert store.rule_set("prior_authorization", date(2026, 9, 14)).id == "RS-PA-2026.01.1"
    assert store.rule_set("prior_authorization", date(2026, 10, 14)).id == "RS-PA-2026.10.1"
    assert store.resolve_rule("R-PA-001", date(2026, 9, 14))[1].version == 1
    assert store.resolve_rule("R-PA-001", date(2026, 10, 1))[1].version == 2
    assert store.diff("prior_authorization", date(2026, 9, 1), date(2026, 10, 14))["changed"] == ["R-PA-001@1->2"]
    assert store.rule_set("prior_authorization", date(2025, 12, 31)) is None


def test_graph_search_reaches_rules_with_paths(knowledge):
    hits = knowledge.graph.search([Seed("outcome:reject:76", 1.0, "reject:76")],
                                  rule_concept=knowledge.rule_concepts())
    ids = [h.rule_id for h in hits]
    assert {"R-QL-001", "R-NET-001"} <= set(ids[:3])
    net = next(h for h in hits if h.rule_id == "R-NET-001")
    assert net.path == ["outcome:reject:76", "<-explains-", "rule:R-NET-001"]


def test_graph_search_respects_scope(knowledge):
    um = knowledge.taxonomy.branch("utilization_management")
    hits = knowledge.graph.search([Seed("outcome:reject:76", 1.0, "reject:76")], scope_concepts=um,
                                  rule_concept=knowledge.rule_concepts())
    assert "R-NET-001" not in {h.rule_id for h in hits}
    assert "R-QL-001" in {h.rule_id for h in hits}


def test_semantic_memory_uses_taxonomy_synonyms(knowledge):
    hits = knowledge.semantic.search("prior auth", kinds={"rule"}, k=3)
    assert hits[0].doc.meta["rule_id"].startswith("R-PA")

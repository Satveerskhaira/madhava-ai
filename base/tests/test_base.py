import warnings
from datetime import date

import pytest

from rulegraph import Knowledge, PackDocuments, Settings, compose
from rulegraph.rules.validate import validate
from rulegraph.sources import CompositionError
from rulegraph.tools.apis import count_leaves

import pharmacy_base as base

warnings.filterwarnings("ignore")


@pytest.fixture(scope="module")
def system():
    return base.build(Settings(today=date(2026, 10, 14)), fragments=[])


def test_base_pack_validates_on_its_own(system):
    kn = system.knowledge
    assert validate(kn) == []
    assert list(kn.taxonomy.agents) == ["eligibility", "coverage", "utilization_management", "clinical_dur",
                                        "network", "cob", "cost_share"]
    assert kn.rules.rules == {} and system.agents == {}          # no domain installed, no agents built


def test_claim_adapter_derives_fields_and_tokens(system):
    snap = system.toolbox.get_record_outcome("RX-1002")
    assert snap["record"]["history"]["daysSinceLastFill"] == 11
    assert snap["outcome_tokens"] == ["reject:79"] and snap["adverse"] is True
    assert system.toolbox.get_record_outcome("RX-1006")["amount_tokens"] == ["price:deductible"]


def test_reference_slices_are_small_and_dated(system):
    plan = system.toolbox.references.get_as_of("PLN-COMM-4T", date(2026, 9, 1))
    assert count_leaves(plan.payload) > 12000
    tier = system.toolbox.get_reference_slice("RX-1005", ["plan.formulary.drug.tier"])["plan.formulary.drug.tier"]
    assert tier.present and tier.reference_version == "2026.07"


def _frag(name, concepts=(), extends=base.BASE_NAME, domains=()):
    return PackDocuments(name=name, kind="fragment", extends=extends, concepts=list(concepts), domains=list(domains))


def test_composition_refuses_conflicts():
    b = base.base_source().load()
    c = {"id": "um.x", "prefLabel": "X", "broader": "um", "notations": ["reject:XX"]}
    with pytest.raises(CompositionError, match="concept um.x defined by a and b"):
        compose([b, _frag("a", [c]), _frag("b", [c])])
    with pytest.raises(CompositionError, match="extends 'other'"):
        compose([b, _frag("c", extends="other")])
    with pytest.raises(CompositionError, match="exactly one base"):
        compose([_frag("d")])


def test_knowledge_can_come_from_documents_not_files():
    docs = base.base_source().load()          # e.g. what a database-backed source would return
    kn = Knowledge.compose([docs])
    assert kn.pack.record_noun == "claim" and validate(kn) == []

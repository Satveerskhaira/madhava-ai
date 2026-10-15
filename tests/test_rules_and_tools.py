from madhava.models import Evidence
from madhava.rules.evaluator import referenced_paths, render
from madhava.tools.apis import count_leaves


def ev(path, value, present=True):
    return Evidence(source="plan" if path.startswith("plan") else "claim", path=path, value=value, present=present)


def test_condition_with_missing_required_input_is_unverified(knowledge):
    card = knowledge.rules.rules["R-RTS-001@1"]
    values = {"claim.history.daysSinceLastFill": ev("claim.history.daysSinceLastFill", 10),
              "claim.history.lastDaysSupply": ev("claim.history.lastDaysSupply", 30)}
    result = knowledge.evaluator.evaluate(card, values)        # threshold missing
    assert result.fired is None and "plan.dur.refillTooSoon.thresholdPct" in result.error


def test_pricing_amount_is_computed_and_capped(knowledge):
    card = knowledge.rules.rules["R-CS-002@1"]
    values = {p: ev(p, v) for p, v in {
        "claim.pharmacy.channel": "retail", "claim.pricing.submittedCost": 1000.0,
        "plan.formulary.drug.tier": 4, "plan.costShare.coinsurancePct": 25.0, "plan.costShare.maxPerFill": 150.0,
    }.items()}
    result = knowledge.evaluator.evaluate(card, values)
    assert result.fired is True and result.amount == 150.0


def test_templates_and_reference_extraction():
    values = {"plan.x": ev("plan.x", ["A", "B"]), "plan.m": ev("plan.m", 150.0)}
    assert render("{{plan.x|list}} cost ${{plan.m|money}} or {{amount|money}}", values, 12.5) == "A, B cost $150.00 or 12.50"
    assert referenced_paths("plan.a.b.exists(d, d in claim.c) && has(plan.e)") == {"plan.a.b", "claim.c", "plan.e"}


def test_benefit_payload_is_full_size_but_slices_are_small(system):
    plan = system.toolbox.benefits.get_plan_as_of("PLN-COMM-4T", __import__("datetime").date(2026, 9, 1))
    assert count_leaves(plan.payload) > 12000
    out = system.toolbox.get_benefit_slice("RX-1005", ["plan.costShare.copay"])
    assert list(out) == ["plan.costShare.copay"]
    # pointer parameters chain: tier comes from the plan, channel from the claim
    assert out["plan.costShare.copay"].pointer == "/costShare/tiers/2/retail/copay"
    assert out["plan.costShare.copay"].value == 35.0


def test_plan_setup_resolves_by_date_of_service(system):
    may = system.toolbox.get_benefit_slice("RX-1013", ["plan.costShare.copay"])["plan.costShare.copay"]
    sep = system.toolbox.get_benefit_slice("RX-1005", ["plan.costShare.copay"])["plan.costShare.copay"]
    assert (may.plan_version, may.value) == ("2026.01", 30.0)
    assert (sep.plan_version, sep.value) == ("2026.07", 35.0)


def test_claim_tool_derives_fields_and_tokens(system):
    snap = system.toolbox.get_claim_outcome("RX-1002")
    assert snap["claim"]["history"]["daysSinceLastFill"] == 11
    assert snap["outcome_tokens"] == ["reject:79"]
    paid = system.toolbox.get_claim_outcome("RX-1006")
    assert paid["price_tokens"] == ["price:deductible"]


def test_missing_drug_entry_is_absent_not_an_error(system):
    out = system.toolbox.get_benefit_slice("RX-1001", ["plan.formulary.drug.excluded"])
    assert out["plan.formulary.drug.excluded"].present is False

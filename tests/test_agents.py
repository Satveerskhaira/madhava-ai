import asyncio
from datetime import date

import pytest
import yaml

from madhava.agents.language import ModelLanguage
from madhava.evaluation import check
from madhava.models import ContextRef, DomainReport, DomainTask
from madhava.settings import REPO_ROOT

GOLDENS = yaml.safe_load((REPO_ROOT / "goldens" / "goldens.yaml").read_text())["goldens"]


def run(coro):
    return asyncio.run(coro)


def task(system, agent, claim, tokens=(), concepts=(), mode="explain", rules_mode="as_adjudicated"):
    snap = system.toolbox.get_claim_outcome(claim)
    ctx = ContextRef(claim_id=claim, plan_id="PLN-COMM-4T", dos=date.fromisoformat(snap["dos"]), rules_mode=rules_mode,
                     rules_date=system.toolbox.rules_date(snap, rules_mode))
    return DomainTask(task_id="t", agent=agent, context=ctx, mode=mode, outcome_tokens=list(tokens),
                      concepts=list(concepts), question="why")


@pytest.mark.parametrize("golden", GOLDENS, ids=[g["claim"] for g in GOLDENS])
def test_golden_claims(system, golden):
    exp = run(system.planner.ask(golden["question"], golden["claim"]))
    assert check(exp, golden["expect"]) == [], exp.answer


def test_domain_agent_checks_overrides(system):
    report = run(system.registry.agents["utilization_management"].handle(
        task(system, "utilization_management", "RX-1001", ["reject:75"], ["um.prior_authorization"])))
    pa = next(f for f in report.findings if f.rule_id == "R-PA-001")
    assert pa.status == "fired" and pa.confidence == "confirmed"
    assert pa.overrides_checked == [{"rule": "R-PA-OVR-002@1", "matched": False,
                                     "intent": "Waive prior authorization during a new member's transition window"}]
    assert pa.retrieval and pa.retrieval.path[0] == "outcome:reject:75"
    assert {e.path for e in pa.evidence} == {"claim.drug.name", "claim.priorAuth.onFile", "plan.formulary.drug.paRequired"}


def test_what_if_mode_ignores_actuals(system):
    report = run(system.registry.agents["cost_share"].handle(task(system, "cost_share", "RX-1001", mode="what_if")))
    fired = [f for f in report.findings if f.status == "fired"]
    assert [(f.rule_id, f.amount, f.confidence) for f in fired] == [("R-CS-002", 150.0, "what_if")]


def test_gap_round_is_bounded_and_finds_the_other_domain(system):
    exp = run(system.planner.ask("Why did this reject?", "RX-1007"))
    assert len(exp.rounds) == 2
    assert exp.rounds[1].reason == "gap round"
    assert exp.primary.rule_id == "R-NET-001"
    assert any("reflect: reject:76 -> network via policy graph" in t for t in exp.trace)


def test_unexplained_outcome_is_logged_not_guessed(system):
    exp = run(system.planner.ask("Why did this reject?", "RX-1008"))
    assert exp.primary is None and exp.confidence == "unexplained"
    assert "reject 85" in exp.answer and "logged for SME review" in exp.answer
    assert [g["token"] for g in system.planner.episodic.gaps] == ["reject:85"]
    assert (system.settings.episodic_dir / "gaps.jsonl").exists()


def test_current_rules_mode_uses_newer_rule_version(system):
    exp = run(system.planner.ask("Why was this rejected?", "RX-1001", rules_mode="current"))
    assert exp.primary.key == "R-PA-001@2"
    assert exp.rules_context["mode"] == "current"


def test_follow_up_in_session_reuses_findings(system):
    run(system.planner.ask("Why was this rejected?", "RX-1001", session_id="s1"))
    exp = run(system.planner.ask("What would it cost if approved?", "RX-1001", session_id="s1"))
    assert exp.rounds[0].agents == ["cost_share:what_if:as_adjudicated"]
    assert exp.primary.rule_id == "R-PA-001"
    assert exp.what_if[0].amount == 150.0


def test_slow_agent_becomes_an_error_report_not_a_failure(system):
    class Slow:
        def __init__(self, inner):
            self.inner = inner

        async def send(self, t):
            if t.agent == "coverage":
                await asyncio.sleep(5)
            return await self.inner.send(t)

    system.planner.transport = Slow(system.planner.transport)
    system.planner.settings.agent_deadline_s = 0.5
    exp = run(system.planner.ask("Why was this rejected?", "RX-1001"))
    assert exp.primary.rule_id == "R-PA-001"
    late = [r for r in system.planner.episodic.episodes[-1]["reports"] if r["agent"] == "coverage"]
    assert late and late[0]["error"].startswith("TimeoutError")


def test_knowledge_question_without_claim(system):
    exp = run(system.planner.ask("What does reject code 76 mean?"))
    assert exp.answer == "Reject code 76: Plan Limitations Exceeded"


# ------------------------------------------------------------ model layer
def _stub_llm(replies):
    from google.adk.models import BaseLlm, LlmResponse
    from google.genai import types

    class StubLlm(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=replies.pop(0))]))

    return StubLlm(model="stub")


def test_model_understanding_is_filtered_to_known_intents_and_concepts(knowledge):
    lang = ModelLanguage(knowledge.taxonomy, _stub_llm(['{"intents": ["explain_outcome", "hack"], '
                                                         '"concepts": ["um.step_therapy", "made.up"]}']))
    ask = run(lang.understand("why did this fail", "RX-1"))
    assert ask.intents == ["explain_outcome"] and ask.concepts == ["um.step_therapy"]


def test_model_narration_must_cite_only_known_rules(knowledge):
    bundle = {"rule_ids": ["R-PA-001"], "primary_rule": "R-PA-001"}
    good = ModelLanguage(knowledge.taxonomy, _stub_llm(["PA was needed [R-PA-001 v1]."]))
    bad = ModelLanguage(knowledge.taxonomy, _stub_llm(["It was R-XYZ-999 that did it."]))
    assert run(good.narrate(bundle, "template")) == "PA was needed [R-PA-001 v1]."
    assert run(bad.narrate(bundle, "template")) == "template"


def test_report_contract_round_trips_as_json(system):
    report = run(system.registry.agents["eligibility"].handle(task(system, "eligibility", "RX-1004", ["reject:69"])))
    again = DomainReport.model_validate_json(report.model_dump_json())
    assert again.findings[0].rule_id == "R-ELIG-001" and again.findings[0].confidence == "confirmed"

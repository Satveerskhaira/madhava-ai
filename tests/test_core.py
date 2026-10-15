import asyncio
from pathlib import Path

import pytest
import yaml

from rulegraph.agent.planner import Planner
from rulegraph.evaluation import check

GOLDENS = yaml.safe_load((Path(__file__).resolve().parents[1] / "goldens" / "goldens.yaml").read_text())["goldens"]


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("golden", GOLDENS, ids=[g["record"] for g in GOLDENS])
def test_core_goldens(system, golden):
    exp = run(system.core.ask(golden["question"], golden["record"]))
    assert check(exp, golden["expect"]) == [], exp.answer


def test_domain_agents_are_in_process_planners(system):
    assert set(system.core.executors) == {"eligibility", "coverage", "utilization_management", "clinical_dur",
                                          "network", "cob", "cost_share"}
    assert all(isinstance(a, Planner) and a.scope for a in system.core.executors.values())


def test_gap_round_follows_a_domain_agents_own_gap(system):
    exp = run(system.core.ask("Why did this reject?", "RX-1007"))
    assert [r.reason for r in exp.rounds] == ["initial plan", "gap round"]
    assert any("utilization_management/explain/as_decided fired=[] gaps=['reject:76']" in t for t in exp.trace)
    assert any("reflect: reject:76 -> network via policy graph" in t for t in exp.trace)
    assert exp.primary.rule_id == "R-NET-001" and exp.primary.agent == "network"


def test_unexplained_outcome_is_logged_not_guessed(system):
    exp = run(system.core.ask("Why did this reject?", "RX-1008"))
    assert exp.primary is None and exp.confidence == "unexplained"
    assert "logged for SME review" in exp.answer
    assert [g["token"] for g in system.core.episodic.gaps] == ["reject:85"]


def test_current_rules_mode(system):
    exp = run(system.core.ask("Why was this rejected?", "RX-1001", rules_mode="current"))
    assert exp.primary.key == "R-PA-001@2"


def test_follow_up_in_session_reuses_domain_reports(system):
    run(system.core.ask("Why was this rejected?", "RX-1001", session_id="s1"))
    exp = run(system.core.ask("What would it cost if approved?", "RX-1001", session_id="s1"))
    assert exp.rounds[0].agents == ["cost_share:what_if:as_decided"]
    assert exp.primary.rule_id == "R-PA-001" and exp.what_if[0].amount == 150.0


def test_slow_domain_agent_becomes_an_error_report(system):
    coverage = system.core.executors["coverage"]

    class Slow:
        name = "coverage"

        async def handle(self, task):
            await asyncio.sleep(5)
            return await coverage.handle(task)

    system.core.executors["coverage"] = Slow()
    system.core.settings.agent_deadline_s = 0.5
    exp = run(system.core.ask("Why was this rejected?", "RX-1001"))
    assert exp.primary.rule_id == "R-PA-001"
    late = [r for r in system.core.episodic.episodes[-1]["reports"] if r["agent"] == "coverage"]
    assert late and late[0]["error"].startswith("TimeoutError")


def test_knowledge_question_without_a_claim(system):
    exp = run(system.core.ask("What does reject code 76 mean?"))
    assert exp.answer == "Reject code 76: Plan Limitations Exceeded"

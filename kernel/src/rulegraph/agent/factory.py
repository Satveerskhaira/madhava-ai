"""Build planners for each level from a knowledge pack.

    domain_agents(kn, toolbox)            -> {agent name: Planner over rule-domain investigators}
    core_planner(kn, toolbox, agents)     -> Planner over those domain agents
    domain_agent(kn, toolbox, name)       -> one domain agent, for a service or test that hosts only it

The core receives domain agents as plain executors (anything with
`name` and `async handle(task)`), so they can be in-process objects from an
installed package, or a client for any transport you choose.
"""

from __future__ import annotations

from typing import Mapping

from ..knowledge import Knowledge
from ..memory.episodic import EpisodicMemory
from ..settings import Settings
from ..tools.toolbox import Toolbox
from .investigator import Investigator
from .language import Language
from .planner import Executor, Planner


def domain_agent(kn: Knowledge, toolbox: Toolbox, name: str, *, language: Language | None = None,
                 episodic: EpisodicMemory | None = None, settings: Settings | None = None) -> Planner:
    tax = kn.taxonomy
    scope = tax.branch(name)
    investigators: dict[str, Executor] = {}
    for rd in sorted(tax.rule_domains(name)):
        if rd in kn.rules.domains:
            investigators[rd] = Investigator(rd, kn, toolbox, set(tax.concept_for_rule_domain(rd)), settings)

    def unit_for(concept: str) -> str | None:
        return tax.get(concept).rule_domain

    top = tax.get(tax.agents[name])
    notes = kn.procedures.investigator.notes.get(name, "")
    return Planner(name, kn, toolbox, investigators, kn.procedures.domain_planner, unit_for, scope=scope,
                   language=language, episodic=episodic, settings=settings, label=top.prefLabel,
                   description=f"{top.definition} {notes}".strip())


def domain_agents(kn: Knowledge, toolbox: Toolbox, *, names: list[str] | None = None,
                  language: Language | None = None, episodic: EpisodicMemory | None = None,
                  settings: Settings | None = None) -> dict[str, Planner]:
    return {n: domain_agent(kn, toolbox, n, language=language, episodic=episodic, settings=settings)
            for n in (names or list(kn.taxonomy.agents))}


def core_planner(kn: Knowledge, toolbox: Toolbox, agents: Mapping[str, Executor], *, name: str = "core",
                 language: Language | None = None, episodic: EpisodicMemory | None = None,
                 settings: Settings | None = None, description: str = "") -> Planner:
    if not kn.procedures.planner:
        raise ValueError("core planner needs planner procedures: Knowledge.load(pack, planner_procedures=...)")
    return Planner(name, kn, toolbox, dict(agents), kn.procedures.planner, kn.taxonomy.agent_for, scope=None,
                   language=language, episodic=episodic, settings=settings, description=description)

"""Wire the core planner to the pharmacy domain agents.

The domain agents come from separately released packages (one per domain, or
several per package), each a knowledge fragment registered under the
`rulegraph.domains` entry point. The core composes the pharmacy base pack with
every installed fragment, so a new domain is a package install, not a core
change. Core and domain agents share that knowledge, one toolbox (a claim is
fetched once per question) and one episodic memory. Domain agents are plain
in-process executors; the core never calls them over A2A.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import pharmacy_base
from rulegraph import DomainPackage, Knowledge, Settings, Toolbox, core_planner
from rulegraph.agent.language import DeterministicLanguage, Language, ModelLanguage
from rulegraph.agent.planner import Planner
from rulegraph.memory.episodic import EpisodicMemory


def procedures_path() -> Path:
    return Path(str(resources.files("madhava") / "procedures" / "planner.yaml"))


@dataclass
class CoreSystem:
    settings: Settings
    knowledge: Knowledge
    toolbox: Toolbox
    agents: dict[str, Planner]
    core: Planner
    packages: list[DomainPackage]


def build(settings: Settings | None = None, *, model: Any = None, knowledge: Knowledge | None = None,
          fragments: list[DomainPackage] | None = None) -> CoreSystem:
    """Core planner over in-process domain agents from every installed domain package (or `fragments`).

    `model` is an ADK model name or object (optional)."""
    settings = settings or Settings()
    if settings.episodic_dir is None and os.environ.get("MADHAVA_EPISODIC_DIR"):
        settings.episodic_dir = Path(os.environ["MADHAVA_EPISODIC_DIR"])
    packages = pharmacy_base.discover_domains() if fragments is None else fragments
    knowledge = knowledge or pharmacy_base.compose_knowledge(packages, planner_procedures=procedures_path())
    language: Language
    if model is not None:
        from rulegraph.adapters.adk import adk_completion   # only needed when a model is used
        language = ModelLanguage(knowledge.taxonomy, knowledge.procedures.intents, adk_completion(model))
    else:
        language = DeterministicLanguage(knowledge.taxonomy, knowledge.procedures.intents)
    episodic = EpisodicMemory(settings.episodic_dir)
    domain = pharmacy_base.build(settings, fragments=packages, knowledge=knowledge, language=language,
                                 episodic=episodic)
    core = core_planner(knowledge, domain.toolbox, domain.agents, name="core", language=language,
                        episodic=episodic, settings=settings,
                        description="Explains why a pharmacy claim was rejected or priced the way it was, "
                                    "across eligibility, coverage, utilization management, clinical safety, "
                                    "network, coordination of benefits and cost share.")
    return CoreSystem(settings, knowledge, domain.toolbox, domain.agents, core, packages)

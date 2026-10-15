"""Wire the core planner to the pharmacy domain agents.

Core and domain agents share one knowledge pack (from pharmacy_agents) and one
toolbox, so a claim is fetched once per question. Domain agents are plain
in-process executors; the core never calls them over A2A.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import pharmacy_agents
from rulegraph import Knowledge, Settings, Toolbox, core_planner
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


def build(settings: Settings | None = None, *, model: Any = None, knowledge: Knowledge | None = None) -> CoreSystem:
    """Core planner over in-process domain agents. `model` is an ADK model name or object (optional)."""
    settings = settings or Settings()
    if settings.episodic_dir is None and os.environ.get("MADHAVA_EPISODIC_DIR"):
        settings.episodic_dir = Path(os.environ["MADHAVA_EPISODIC_DIR"])
    knowledge = knowledge or Knowledge.load(pharmacy_agents.pack_dir(), planner_procedures=procedures_path())
    language: Language
    if model is not None:
        from rulegraph.adapters.adk import adk_completion   # only needed when a model is used
        language = ModelLanguage(knowledge.taxonomy, knowledge.procedures.intents, adk_completion(model))
    else:
        language = DeterministicLanguage(knowledge.taxonomy, knowledge.procedures.intents)
    domain = pharmacy_agents.build(settings, knowledge=knowledge, language=language)
    core = core_planner(knowledge, domain.toolbox, domain.agents, name="core", language=language,
                        episodic=EpisodicMemory(settings.episodic_dir), settings=settings,
                        description="Explains why a pharmacy claim was rejected or priced the way it was, "
                                    "across eligibility, coverage, utilization management, clinical safety, "
                                    "network, coordination of benefits and cost share.")
    return CoreSystem(settings, knowledge, domain.toolbox, domain.agents, core)

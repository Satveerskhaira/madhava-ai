"""Wiring: build the toolbox, agents and planner from settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .agents.language import DeterministicLanguage, Language, ModelLanguage
from .agents.planner import Planner
from .agents.registry import A2ATransport, AgentRegistry, InProcessTransport, Transport
from .knowledge import Knowledge
from .memory.episodic import EpisodicMemory
from .settings import Settings
from .tools.apis import (FixtureBenefitSetupApi, FixtureClaimsApi, HttpBenefitSetupApi, HttpClaimsApi)
from .tools.toolbox import Toolbox


@dataclass
class System:
    settings: Settings
    knowledge: Knowledge
    toolbox: Toolbox
    registry: AgentRegistry
    planner: Planner


def build_toolbox(settings: Settings, knowledge: Knowledge) -> Toolbox:
    claims_url, benefits_url = os.environ.get("MADHAVA_CLAIMS_URL"), os.environ.get("MADHAVA_BENEFITS_URL")
    token = os.environ.get("MADHAVA_API_TOKEN")
    claims = HttpClaimsApi(claims_url, token) if claims_url else FixtureClaimsApi(settings.fixtures_dir / "claims.yaml")
    benefits = (HttpBenefitSetupApi(benefits_url, token) if benefits_url
                else FixtureBenefitSetupApi(settings.fixtures_dir / "benefit_setup"))
    return Toolbox(knowledge, claims, benefits, settings)


def build_system(settings: Settings | None = None, *, model: Any = None,
                 a2a_urls: dict[str, str] | None = None, knowledge: Knowledge | None = None) -> System:
    settings = settings or Settings()
    knowledge = knowledge or Knowledge.load(settings)
    toolbox = build_toolbox(settings, knowledge)
    registry = AgentRegistry(knowledge, toolbox, settings)
    transport: Transport = A2ATransport(a2a_urls) if a2a_urls else InProcessTransport(registry)
    language: Language = ModelLanguage(knowledge.taxonomy, model) if model else DeterministicLanguage(knowledge.taxonomy)
    planner = Planner(knowledge, toolbox, registry, transport=transport, language=language,
                      episodic=EpisodicMemory(settings.episodic_dir), settings=settings)
    return System(settings, knowledge, toolbox, registry, planner)

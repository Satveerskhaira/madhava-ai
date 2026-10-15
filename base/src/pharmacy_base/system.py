"""Compose the base pack with domain fragments and build the domain agents.

    from pharmacy_base import build
    system = build()                          # every installed pharmacy domain package
    system = build(fragments=[um.package])    # only the domains given (a domain repo's own tests)

Knowledge comes from sources: the base pack and each fragment are directories
today; a database-backed source (for example MongoDB) can replace any of them
without changing this module. Real claims and plan setup APIs: set
PHARMACY_CLAIMS_URL and PHARMACY_BENEFITS_URL (and PHARMACY_API_TOKEN).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from rulegraph import DirectorySource, DomainPackage, Knowledge, KnowledgeSource, Settings, Toolbox, discover, \
    domain_agents
from rulegraph.agent.language import Language
from rulegraph.agent.planner import Planner
from rulegraph.memory.episodic import EpisodicMemory
from rulegraph.tools.apis import FixtureRecordApi, FixtureReferenceApi, HttpRecordApi, HttpReferenceApi

from .record import ClaimAdapter

BASE_NAME = "pharmacy-claims"


def pack_dir() -> Path:
    return Path(str(resources.files("pharmacy_base") / "pack"))


def fixtures_dir() -> Path:
    return Path(str(resources.files("pharmacy_base") / "fixtures"))


def base_source() -> KnowledgeSource:
    return DirectorySource(pack_dir())


@dataclass
class DomainSystem:
    settings: Settings
    knowledge: Knowledge
    toolbox: Toolbox
    agents: dict[str, Planner]
    packages: list[DomainPackage] = field(default_factory=list)


def build_toolbox(knowledge: Knowledge, settings: Settings) -> Toolbox:
    claims_url, benefits_url = os.environ.get("PHARMACY_CLAIMS_URL"), os.environ.get("PHARMACY_BENEFITS_URL")
    token = os.environ.get("PHARMACY_API_TOKEN")
    records = (HttpRecordApi(claims_url, "/claims/{id}", token) if claims_url
               else FixtureRecordApi(fixtures_dir() / "claims.yaml", key="claims"))
    references = (HttpReferenceApi(benefits_url, "/plans/{id}/setup?asOf={date}", "/plans/{id}/versions", token)
                  if benefits_url else FixtureReferenceApi(fixtures_dir() / "benefit_setup"))
    return Toolbox(knowledge, records, references, ClaimAdapter(), settings)


def discover_domains() -> list[DomainPackage]:
    """Every installed domain package that extends this base."""
    return discover(extends=BASE_NAME)


def compose_knowledge(packages: list[DomainPackage], planner_procedures: Path | dict[str, Any] | None = None,
                      base: KnowledgeSource | None = None) -> Knowledge:
    return Knowledge.compose([base or base_source(), *[p.source for p in packages]], planner_procedures)


def build(settings: Settings | None = None, *, fragments: list[DomainPackage] | None = None,
          knowledge: Knowledge | None = None, toolbox: Toolbox | None = None, language: Language | None = None,
          episodic: EpisodicMemory | None = None, planner_procedures: Path | dict[str, Any] | None = None,
          names: list[str] | None = None) -> DomainSystem:
    """Base + fragments -> knowledge, toolbox and one planner per domain agent.

    `fragments` defaults to every installed package registered under `rulegraph.domains` that extends
    this base. Pass `knowledge`, `toolbox` and `episodic` to share them with a core planner.
    """
    settings = settings or Settings()
    packages = discover_domains() if fragments is None else fragments
    knowledge = knowledge or compose_knowledge(packages, planner_procedures)
    toolbox = toolbox or build_toolbox(knowledge, settings)
    agents = domain_agents(knowledge, toolbox, names=names, language=language,
                           episodic=episodic or EpisodicMemory(settings.episodic_dir), settings=settings)
    return DomainSystem(settings, knowledge, toolbox, agents, packages)

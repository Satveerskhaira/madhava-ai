"""Agent cards (A2A 1.0 JSON shape) for any planner.

A domain agent's card has one skill per taxonomy concept in its scope, tagged
with the concept id, its outcome notations and its rule domain. A core
planner's card has one skill per domain agent. Cards are plain dicts, so any
A2A server or registry can publish them.
"""

from __future__ import annotations

from typing import Any

from .planner import Planner


def agent_card(planner: Planner, url: str = "http://localhost:8000", version: str = "0.1.0") -> dict[str, Any]:
    kn = planner.kn
    tax = kn.taxonomy
    prefix = kn.pack.agent_prefix
    skills: list[dict[str, Any]] = []
    if planner.scope is None:
        for agent, top in tax.agents.items():
            c = tax.get(top)
            notations = [n for cid in tax.branch(agent) for n in tax.get(cid).notations]
            skills.append({"id": agent, "name": c.prefLabel, "description": c.definition,
                           "tags": [top, *notations]})
        description = planner.description or f"Explains {kn.pack.name} decisions across all domain agents."
    else:
        top = tax.agents.get(planner.name)
        for cid in sorted(planner.scope - ({top} if top else set())):
            c = tax.get(cid)
            skills.append({
                "id": cid, "name": c.prefLabel, "description": c.definition,
                "tags": [cid, *c.notations, *([f"rule_domain:{c.rule_domain}"] if c.rule_domain else [])],
                "examples": [f"Why did this record get {kn.describe_token(n)}?" for n in c.notations][:2],
            })
        description = planner.description or (planner.label or planner.name)
    return {
        "name": f"{prefix}-{planner.name}",
        "description": description,
        "version": version,
        "supportedInterfaces": [{"url": url, "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}],
        "capabilities": {"streaming": True},
        "defaultInputModes": ["application/json", "text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": skills,
    }

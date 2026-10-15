"""Agent registry: who the domain agents are, their A2A agent cards, and how to reach them.

Agents are derived from the taxonomy (top concepts with an `agent`), so adding a
domain is a taxonomy and rules change, not an orchestrator change. Each agent
card lists one skill per leaf concept, tagged with the concept id, its outcome
notations and its rule domain; routing reads the taxonomy, and the cards let
any A2A client discover the same structure.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

from ..knowledge import Knowledge
from ..models import DomainReport, DomainTask
from ..settings import Settings
from ..tools.toolbox import Toolbox
from .domain import DomainAgent


class Transport(Protocol):
    async def send(self, task: DomainTask) -> DomainReport: ...


class AgentRegistry:
    def __init__(self, knowledge: Knowledge, toolbox: Toolbox, settings: Settings | None = None):
        self.kn = knowledge
        self.settings = settings or toolbox.settings
        self.agents: dict[str, DomainAgent] = {
            name: DomainAgent(name, knowledge, toolbox, self.settings) for name in knowledge.taxonomy.agents
        }

    def names(self) -> list[str]:
        return list(self.agents)

    def card(self, name: str, url: str = "http://localhost:8000") -> dict[str, Any]:
        """A2A agent card (A2A protocol 1.0 JSON shape)."""
        tax = self.kn.taxonomy
        agent = self.agents[name]
        skills = []
        for cid in sorted(agent.scope - {agent.top_concept}):
            c = tax.get(cid)
            skills.append({
                "id": cid,
                "name": c.prefLabel,
                "description": c.definition,
                "tags": [cid, *c.notations, *([f"rule_domain:{c.rule_domain}"] if c.rule_domain else [])],
                "examples": [f"Why did this claim reject with {self.kn.describe_token(n)}?" for n in c.notations][:2],
            })
        return {
            "name": f"madhava-{name}",
            "description": f"{tax.get(agent.top_concept).prefLabel} agent. {agent.description} {agent.notes}".strip(),
            "version": "0.1.0",
            "supportedInterfaces": [{"url": url, "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}],
            "capabilities": {"streaming": True},
            "defaultInputModes": ["application/json", "text/plain"],
            "defaultOutputModes": ["application/json"],
            "skills": skills,
        }


class InProcessTransport:
    """Calls domain agents directly. Same contract as A2A, used in tests and single-process runs."""

    def __init__(self, registry: AgentRegistry):
        self.registry = registry

    async def send(self, task: DomainTask) -> DomainReport:
        # Round-trip through JSON so in-process runs exercise the same contract as A2A.
        wire = DomainTask.model_validate_json(task.model_dump_json())
        report = await self.registry.agents[task.agent].handle(wire)
        return DomainReport.model_validate_json(report.model_dump_json())


class A2ATransport:
    """Sends tasks to domain agents served over A2A (see madhava.adk.a2a)."""

    def __init__(self, urls: dict[str, str], timeout: float = 10.0):
        self.urls = urls
        self.timeout = timeout
        self._clients: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def _client(self, agent: str) -> Any:
        from a2a.client import create_client

        async with self._lock:
            if agent not in self._clients:
                client = create_client(self.urls[agent])
                if asyncio.iscoroutine(client):
                    client = await client
                self._clients[agent] = client
            return self._clients[agent]

    async def send(self, task: DomainTask) -> DomainReport:
        from a2a.helpers import get_stream_response_text, new_text_message
        from a2a.types.a2a_pb2 import ROLE_USER, SendMessageRequest

        client = await self._client(task.agent)
        message = new_text_message(task.model_dump_json(), role=ROLE_USER)
        result_text = ""
        async for response in client.send_message(SendMessageRequest(message=message)):
            kind = response.WhichOneof("payload") if hasattr(response, "WhichOneof") else None
            text = get_stream_response_text(response)
            if text and kind in ("artifact_update", "message", None):
                result_text = text
            elif text and not result_text:
                result_text = text
        if not result_text:
            return DomainReport(task_id=task.task_id, agent=task.agent, mode=task.mode,
                                rules_mode=task.context.rules_mode, error="empty A2A response")
        return DomainReport.model_validate(json.loads(result_text))

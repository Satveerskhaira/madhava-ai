"""Domain agents as ADK agents, served over A2A.

Each domain agent becomes an ADK BaseAgent whose input is a DomainTask (JSON)
and whose output is a DomainReport (JSON). `to_a2a` turns it into an A2A
server with an agent card at /.well-known/agent-card.json built from the
taxonomy (one skill per concept).

    madhava serve-agent utilization_management --port 8103
"""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.genai import types
from google.protobuf.json_format import ParseDict
from pydantic import ConfigDict

from ..agents.domain import DomainAgent
from ..agents.registry import AgentRegistry
from ..models import DomainTask


class AdkDomainAgent(BaseAgent):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    core: Any = None

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        text = ""
        if ctx.user_content and ctx.user_content.parts:
            text = "".join(p.text or "" for p in ctx.user_content.parts)
        agent: DomainAgent = self.core
        task = DomainTask.model_validate(json.loads(text))
        report = await agent.handle(task)
        yield Event(author=self.name, invocation_id=ctx.invocation_id,
                    content=types.Content(role="model", parts=[types.Part(text=report.model_dump_json())]))


def adk_domain_agent(registry: AgentRegistry, name: str) -> AdkDomainAgent:
    core = registry.agents[name]
    return AdkDomainAgent(name=name, description=core.description, core=core)


def build_a2a_app(registry: AgentRegistry, name: str, host: str = "127.0.0.1", port: int = 8100) -> Any:
    from a2a.types.a2a_pb2 import AgentCard
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    card = ParseDict(registry.card(name, url=f"http://{host}:{port}"), AgentCard(), ignore_unknown_fields=True)
    return to_a2a(adk_domain_agent(registry, name), host=host, port=port, agent_card=card)

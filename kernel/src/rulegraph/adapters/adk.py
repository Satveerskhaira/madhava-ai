"""Google ADK adapter (optional: pip install "rulegraph[adk]").

- build_workflow(planner): any planner (core or domain) as an ADK 2 workflow graph
      START -> intake -> understand -> plan -> act -> verify --gap--> act
                                                        \\--done--> synthesize -> remember
  The nodes call the same Planner methods as Planner.ask, so the graph and the
  plain loop cannot drift apart. The blackboard lives in session state ("bb").
- a2a_app(planner, ...): serve a planner over A2A with an agent card built from
  the taxonomy (use for the agent you expose, typically the core).
- adk_completion(model): wrap an ADK model (name or BaseLlm) as the plain
  `complete(system, user)` function ModelLanguage expects.
- AdkExecutor: wrap any executor (planner or investigator) as an ADK BaseAgent
  that takes a DomainTask JSON message, if you want ADK-native hierarchies.

Nothing else in the kernel imports this module.
"""

from __future__ import annotations

import json
import re
from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.adk.workflow import START, Workflow
from google.genai import types
from pydantic import ConfigDict

from ..agent.cards import agent_card
from ..agent.language import Completion
from ..agent.planner import Executor, Planner
from ..memory.working import Blackboard
from ..models import DomainTask


def _text(node_input: Any) -> str:
    parts = getattr(node_input, "parts", None)
    if parts:
        return "".join(getattr(p, "text", "") or "" for p in parts)
    return node_input if isinstance(node_input, str) else ""


def parse_request(text: str) -> dict[str, Any]:
    """JSON {"record_id", "question", ...}, or "<record id>: question", or just a question."""
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    m = re.match(r"^\s*([A-Za-z]+-[\w-]+)\s*[:|]\s*(.+)$", text, re.S)
    if m:
        return {"record_id": m.group(1), "question": m.group(2).strip()}
    return {"question": text}


def build_workflow(planner: Planner, name: str | None = None) -> Workflow:
    def load(ctx: Any) -> Blackboard:
        return Blackboard.model_validate(ctx.state["bb"])

    def save(bb: Blackboard, **kwargs: Any) -> Event:
        return Event(state={"bb": bb.model_dump(mode="json")}, **kwargs)

    async def intake(ctx: Any, node_input: Any) -> Event:
        req = ctx.state.get("request") or parse_request(_text(node_input))
        bb = Blackboard(agent=planner.name, question=req["question"], record_id=req.get("record_id"),
                        session_id=req.get("session_id"), requested_rules_mode=req.get("rules_mode", "as_decided"),
                        as_of=req.get("as_of"))
        await planner.observe(bb)
        return save(bb, output="observed")

    async def understand(ctx: Any) -> Event:
        bb = load(ctx)
        await planner.understand(bb)
        return save(bb, output=bb.ask.intents if bb.ask else [])

    async def plan(ctx: Any) -> Event:
        bb = load(ctx)
        await planner.plan(bb)
        return save(bb, output=[t.agent for t in bb.pending])

    async def act(ctx: Any) -> Event:
        bb = load(ctx)
        await planner.act(bb)
        return save(bb, output=bb.rounds[-1].agents if bb.rounds else [])

    async def verify(ctx: Any) -> Event:
        bb = load(ctx)
        route = await planner.verify(bb)
        return save(bb, route=route, output={"route": route, "gaps": bb.gaps})

    async def synthesize(ctx: Any) -> Event:
        bb = load(ctx)
        await planner.synthesize(bb)
        return save(bb, output="synthesized")

    async def remember(ctx: Any) -> Event:
        bb = load(ctx)
        await planner.remember(bb)
        exp = bb.explanation
        assert exp is not None
        return Event(output=exp.model_dump(mode="json"), message=exp.answer)

    return Workflow(name=name or f"{planner.name}_planner", edges=[
        (START, intake, understand, plan, act, verify),
        (verify, {"gap": act, "done": synthesize}),
        (synthesize, remember),
    ])


def a2a_app(planner: Planner, host: str = "127.0.0.1", port: int = 8100, card: dict[str, Any] | None = None) -> Any:
    """Starlette app serving the planner's workflow over A2A, card at /.well-known/agent-card.json."""
    from a2a.types.a2a_pb2 import AgentCard
    from google.adk.a2a.utils.agent_to_a2a import to_a2a
    from google.protobuf.json_format import ParseDict

    card_dict = card or agent_card(planner, url=f"http://{host}:{port}")
    proto = ParseDict(card_dict, AgentCard(), ignore_unknown_fields=True)
    return to_a2a(build_workflow(planner), host=host, port=port, agent_card=proto)


def adk_completion(model: Any) -> Completion:
    """Wrap an ADK model (a registered name such as "gemini-2.5-flash", or a BaseLlm) as complete(system, user)."""
    from google.adk.models import BaseLlm, LlmRequest, LLMRegistry

    llm: BaseLlm = model if isinstance(model, BaseLlm) else LLMRegistry.new_llm(model)

    async def complete(system: str, user: str) -> str:
        request = LlmRequest(
            model=getattr(llm, "model", None),
            contents=[types.Content(role="user", parts=[types.Part(text=user)])],
            config=types.GenerateContentConfig(system_instruction=system, temperature=0),
        )
        text = ""
        async for response in llm.generate_content_async(request):
            if response.content and response.content.parts:
                text += "".join(p.text or "" for p in response.content.parts)
        return text.strip()

    return complete


class AdkExecutor(BaseAgent):
    """Any executor as an ADK agent: input a DomainTask JSON message, output a DomainReport JSON message."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    core: Any = None

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        text = "".join(p.text or "" for p in (ctx.user_content.parts if ctx.user_content else []))
        executor: Executor = self.core
        report = await executor.handle(DomainTask.model_validate(json.loads(text)))
        yield Event(author=self.name, invocation_id=ctx.invocation_id,
                    content=types.Content(role="model", parts=[types.Part(text=report.model_dump_json())]))

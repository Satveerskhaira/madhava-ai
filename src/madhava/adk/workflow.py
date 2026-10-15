"""The planner as an ADK 2 workflow graph.

    START -> intake -> understand -> plan -> act -> verify --gap--> act
                                                       \\--done--> synthesize -> remember

Nodes call the same Planner methods as `Planner.ask`, so the graph and the
plain loop cannot drift apart. The blackboard (working memory) is kept in
session state under "bb", which makes every run inspectable and replayable.
The bounded loop is the single `gap` back edge; `verify` stops it once the
procedure's round limit is reached.

Input: either `state_delta={"request": {...}}` or a user message that is JSON
({"claim_id", "question", "session_id", "rules_mode"}) or "RX-1001: question".
"""

from __future__ import annotations

import json
import re
from typing import Any

from google.adk.events.event import Event
from google.adk.workflow import START, Workflow

from ..agents.planner import Planner
from ..memory.working import Blackboard


def _text(node_input: Any) -> str:
    parts = getattr(node_input, "parts", None)
    if parts:
        return "".join(getattr(p, "text", "") or "" for p in parts)
    return node_input if isinstance(node_input, str) else ""


def parse_request(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    m = re.match(r"^\s*(RX-[\w-]+)\s*[:|-]\s*(.+)$", text, re.S)
    if m:
        return {"claim_id": m.group(1), "question": m.group(2).strip()}
    return {"question": text}


def build_workflow(planner: Planner, name: str = "madhava_planner") -> Workflow:
    def load(ctx: Any) -> Blackboard:
        return Blackboard.model_validate(ctx.state["bb"])

    def save(bb: Blackboard, **kwargs: Any) -> Event:
        return Event(state={"bb": bb.model_dump(mode="json")}, **kwargs)

    async def intake(ctx: Any, node_input: Any) -> Event:
        req = ctx.state.get("request") or parse_request(_text(node_input))
        bb = Blackboard(question=req["question"], claim_id=req.get("claim_id"), session_id=req.get("session_id"),
                        requested_rules_mode=req.get("rules_mode", "as_adjudicated"), as_of=req.get("as_of"))
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

    return Workflow(name=name, edges=[
        (START, intake, understand, plan, act, verify),
        (verify, {"gap": act, "done": synthesize}),
        (synthesize, remember),
    ])

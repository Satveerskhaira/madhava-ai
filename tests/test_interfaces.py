"""The core agent's external interfaces: A2A (core only), ADK workflow, MCP."""

import asyncio
import json
import socket
import threading
import time

import httpx
import uvicorn

from rulegraph.adapters.adk import a2a_app, build_workflow, parse_request
from rulegraph.adapters.mcp import build_mcp_server


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_parse_request_forms():
    assert parse_request("RX-1001: why?") == {"record_id": "RX-1001", "question": "why?"}
    assert parse_request('{"record_id": "RX-1", "question": "q"}')["record_id"] == "RX-1"


def test_core_runs_as_adk_workflow_with_gap_edge(system):
    from google.adk.runners import InMemoryRunner

    async def go():
        runner = InMemoryRunner(node=build_workflow(system.core), app_name="madhava")
        session = await runner.session_service.create_session(app_name="madhava", user_id="u")
        out, routes = None, []
        async for ev in runner.run_async(user_id="u", session_id=session.id,
                                         state_delta={"request": {"record_id": "RX-1007", "question": "Why did this reject?"}}):
            if ev.actions and ev.actions.route:
                routes.append(ev.actions.route)
            if isinstance(ev.output, dict) and "answer" in ev.output:
                out = ev.output
        return out, routes

    out, routes = asyncio.run(go())
    assert routes == ["gap", "done"]
    assert out["primary"]["rule_id"] == "R-NET-001"


def test_core_is_served_over_a2a(system):
    from a2a.client import create_client
    from a2a.helpers import get_stream_response_text, new_text_message
    from a2a.types.a2a_pb2 import ROLE_USER, SendMessageRequest

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(a2a_app(system.core, port=port), host="127.0.0.1", port=port,
                                           log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    try:
        deadline = time.time() + 15
        while True:
            try:
                card = httpx.get(f"http://127.0.0.1:{port}/.well-known/agent-card.json").json()
                break
            except httpx.HTTPError:
                assert time.time() < deadline
                time.sleep(0.2)
        assert card["name"] == "pharmacy-core"
        assert {s["id"] for s in card["skills"]} >= {"utilization_management", "cost_share"}

        async def ask(text: str) -> str:
            client = create_client(f"http://127.0.0.1:{port}")
            if asyncio.iscoroutine(client):
                client = await client
            answer = ""
            async for resp in client.send_message(SendMessageRequest(message=new_text_message(text, role=ROLE_USER))):
                t = get_stream_response_text(resp)
                if t:
                    answer = t
            return answer

        answer = asyncio.run(ask(json.dumps({"record_id": "RX-1009", "question": "Why was this rejected?"})))
        assert "R-COB-001" in answer and "Primary reason for reject 41" in answer
    finally:
        server.should_exit = True


def test_mcp_tools_use_pharmacy_nouns(system):
    from mcp.client import Client

    async def go():
        server = build_mcp_server(system.toolbox, system.core, record_noun="claim", reference_noun="benefit")
        async with Client(server) as c:
            names = [t.name for t in (await c.list_tools()).tools]
            outcome = (await c.call_tool("get_claim_outcome", {"record_id": "RX-1001"})).structured_content
            sl = (await c.call_tool("get_benefit_slice", {"record_id": "RX-1005",
                                                          "paths": ["plan.costShare.copay"]})).structured_content
            exp = (await c.call_tool("explain_claim", {"record_id": "RX-1009", "question": "why?"})).structured_content
            return names, outcome, sl, exp

    names, outcome, sl, exp = asyncio.run(go())
    assert {"get_claim_outcome", "get_benefit_slice", "explain_claim", "search_rules"} <= set(names)
    assert "record" not in outcome and outcome["codes"][0]["code"] == "75"
    assert sl["values"]["plan.costShare.copay"]["value"] == 35.0
    assert exp["primary"]["rule_id"] == "R-COB-001"

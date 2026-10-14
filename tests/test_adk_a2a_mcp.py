import asyncio
import socket
import threading
import time

import httpx
import uvicorn

from madhava.adk.a2a import build_a2a_app
from madhava.adk.workflow import build_workflow, parse_request
from madhava.agents.registry import A2ATransport
from madhava.tools.mcp_server import build_mcp_server


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_parse_request_forms():
    assert parse_request("RX-1001: why?") == {"claim_id": "RX-1001", "question": "why?"}
    assert parse_request('{"claim_id": "RX-1", "question": "q"}')["claim_id"] == "RX-1"


def test_planner_runs_as_adk_workflow_with_gap_edge(system):
    from google.adk.runners import InMemoryRunner

    async def go():
        runner = InMemoryRunner(node=build_workflow(system.planner), app_name="madhava")
        session = await runner.session_service.create_session(app_name="madhava", user_id="u")
        out, routes = None, []
        async for ev in runner.run_async(user_id="u", session_id=session.id,
                                         state_delta={"request": {"claim_id": "RX-1007", "question": "Why did this reject?"}}):
            if ev.actions and ev.actions.route:
                routes.append(ev.actions.route)
            if isinstance(ev.output, dict) and "answer" in ev.output:
                out = ev.output
        return out, routes

    out, routes = asyncio.run(go())
    assert routes == ["gap", "done"]
    assert out["primary"]["rule_id"] == "R-NET-001"


def test_planner_uses_domain_agents_over_a2a(system):
    names = system.registry.names()
    ports = {n: free_port() for n in names}
    servers = []
    for n in names:
        app = build_a2a_app(system.registry, n, port=ports[n])
        srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=ports[n], log_level="error"))
        threading.Thread(target=srv.run, daemon=True).start()
        servers.append(srv)
    try:
        deadline = time.time() + 15
        for n in names:
            while True:
                try:
                    card = httpx.get(f"http://127.0.0.1:{ports[n]}/.well-known/agent-card.json").json()
                    break
                except httpx.HTTPError:
                    assert time.time() < deadline
                    time.sleep(0.2)
            assert card["name"] == f"madhava-{n}"
        um = httpx.get(f"http://127.0.0.1:{ports['utilization_management']}/.well-known/agent-card.json").json()
        skill = next(s for s in um["skills"] if s["id"] == "um.prior_authorization")
        assert "reject:75" in skill["tags"] and "rule_domain:prior_authorization" in skill["tags"]

        system.planner.transport = A2ATransport({n: f"http://127.0.0.1:{p}" for n, p in ports.items()})
        system.planner.settings.agent_deadline_s = 15
        exp = asyncio.run(system.planner.ask("Why was this rejected, and what would it cost if approved?", "RX-1001"))
        assert exp.primary.rule_id == "R-PA-001" and exp.what_if[0].amount == 150.0
    finally:
        for s in servers:
            s.should_exit = True


def test_mcp_server_exposes_tools(system):
    from mcp.client import Client

    async def go():
        async with Client(build_mcp_server(system)) as c:
            names = [t.name for t in (await c.list_tools()).tools]
            outcome = await c.call_tool("get_claim_outcome", {"claim_id": "RX-1001"})
            rules = await c.call_tool("search_rules", {"rules_date": "2026-09-14", "outcome_tokens": ["reject:75"]})
            return names, outcome.structured_content, rules.structured_content

    names, outcome, rules = asyncio.run(go())
    assert {"get_claim_outcome", "get_benefit_slice", "search_rules", "explain_claim"} <= set(names)
    assert "claim" not in outcome and outcome["rejects"][0]["code"] == "75"
    top = rules["result"][0] if isinstance(rules, dict) and "result" in rules else rules[0]
    assert top["rule_id"] == "R-PA-001"

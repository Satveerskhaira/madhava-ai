"""MCP server exposing the toolbox (and the planner) to any MCP client.

Tool names follow design doc section 9 where they overlap. Responses are
small, decoded and PHI-minimised: claim tools return outcome summaries and
only the fields asked for, never the full claim or the 12k-field plan payload.

    madhava mcp                         # stdio
    madhava mcp --transport streamable-http --port 8200
"""

from __future__ import annotations

from datetime import date
from typing import Any

from mcp.server.mcpserver import MCPServer

from ..app import System


def build_mcp_server(system: System) -> MCPServer:
    tools = system.toolbox
    server = MCPServer("madhava", instructions=(
        "Tools for explaining pharmacy claim outcomes. Start with get_claim_outcome, or call explain_claim "
        "to run the full planner. Every explanation cites rule ids and versions; never state a reason that "
        "no tool returned."))

    @server.tool(description="Claim status, reject codes with meanings, outcome tokens and the plan version pinned by date of service.")
    def get_claim_outcome(claim_id: str) -> dict[str, Any]:
        snap = tools.get_claim_outcome(claim_id)
        return {k: v for k, v in snap.items() if k != "claim"}

    @server.tool(description="Specific claim fields by catalog path, e.g. claim.daysSupply.")
    def get_claim_fields(claim_id: str, paths: list[str]) -> dict[str, Any]:
        unknown = [p for p in paths if p not in system.knowledge.catalog]
        values = tools.claim_values(claim_id, [p for p in paths if p not in unknown])
        return {"values": {p: e.value for p, e in values.items()}, "unknown_paths": unknown}

    @server.tool(description="Only the requested benefit setup fields (plan.* catalog paths), resolved as of the claim's date of service.")
    def get_benefit_slice(claim_id: str, paths: list[str]) -> dict[str, Any]:
        unknown = [p for p in paths if p not in system.knowledge.catalog]
        values = tools.get_benefit_slice(claim_id, [p for p in paths if p not in unknown])
        return {"values": {p: {"value": e.value, "present": e.present, "pointer": e.pointer,
                               "plan_version": e.plan_version} for p, e in values.items()},
                "unknown_paths": unknown}

    @server.tool(description="Search the rules/policy graph for candidate rules in force on a date. Returns scores and the graph path that reached each rule.")
    def search_rules(rules_date: str, outcome_tokens: list[str] | None = None, concepts: list[str] | None = None,
                     text: str = "", k: int = 10) -> list[dict[str, Any]]:
        hits = tools.search_rules(date.fromisoformat(rules_date), outcome_tokens or [], concepts or [], text, k=k)
        return [{"rule_id": h.rule_id, "score": round(h.score, 3), "path": h.path,
                 "intent": tools.get_rule(h.rule_id).intent} for h in hits]

    @server.tool(description="A rule card: intent, condition, fields read, outcome, precedence, overrides and sources.")
    def get_rule(rule_id: str, version: int | None = None) -> dict[str, Any]:
        return tools.get_rule(rule_id, version).model_dump(mode="json")

    @server.tool(description="Timeline of a rule domain's effective-dated releases with reasons.")
    def list_rule_history(domain: str) -> list[dict[str, Any]]:
        return tools.list_rule_history(domain)

    @server.tool(description="Rules added, removed or changed in a domain between two dates.")
    def diff_rule_sets(domain: str, from_date: str, to_date: str) -> dict[str, Any]:
        return tools.diff_rule_sets(domain, date.fromisoformat(from_date), date.fromisoformat(to_date))

    @server.tool(description="Taxonomy concepts recognised in text, with definitions and owning agents.")
    def lookup_concept(text: str) -> list[dict[str, Any]]:
        return tools.lookup_concept(text)

    @server.tool(description="Semantic search over concepts, catalog fields, reject codes and rules.")
    def search_knowledge(query: str, kinds: list[str] | None = None, k: int = 5) -> list[dict[str, Any]]:
        return tools.search_knowledge(query, kinds, k)

    @server.tool(description="Run the planner and domain agents to answer a question about a claim. rules_mode: as_adjudicated (default), current, as_of.")
    async def explain_claim(claim_id: str, question: str, rules_mode: str = "as_adjudicated",
                            as_of: str | None = None, session_id: str | None = None) -> dict[str, Any]:
        exp = await system.planner.ask(question, claim_id, session_id=session_id, rules_mode=rules_mode,
                                       as_of=date.fromisoformat(as_of) if as_of else None)
        return exp.model_dump(mode="json", exclude={"checked", "trace"})

    return server

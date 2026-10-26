"""MCP adapter (optional: pip install "rulegraph[mcp]").

Serves the toolbox (and optionally a planner) as MCP tools, so any MCP client
or any agent SDK with MCP support can use the same tools. Tool names use the
pack's nouns, for example get_claim_outcome and get_benefit_slice.

Responses are small, decoded and minimised: record tools return outcome
summaries and only the fields asked for, never a full record or a full
reference payload.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from mcp.server.mcpserver import MCPServer

from ..agent.planner import Planner
from ..tools.toolbox import Toolbox


def build_mcp_server(toolbox: Toolbox, planner: Planner | None = None, name: str = "rulegraph",
                     record_noun: str = "record", reference_noun: str = "reference") -> MCPServer:
    kn = toolbox.kn
    server = MCPServer(name, instructions=(
        f"Tools for explaining {record_noun} outcomes. Start with get_{record_noun}_outcome"
        + (f", or call explain_{record_noun} to run the planner" if planner else "")
        + ". Every explanation cites rule ids and versions; never state a reason no tool returned."))

    def outcome(record_id: str) -> dict[str, Any]:
        snap = toolbox.get_record_outcome(record_id)
        return {k: v for k, v in snap.items() if k != "record"}

    def fields(record_id: str, paths: list[str]) -> dict[str, Any]:
        unknown = [p for p in paths if p not in kn.catalog]
        values = toolbox.record_values(record_id, [p for p in paths if p not in unknown])
        return {"values": {p: e.value for p, e in values.items()}, "unknown_paths": unknown}

    def ref_slice(record_id: str, paths: list[str]) -> dict[str, Any]:
        unknown = [p for p in paths if p not in kn.catalog]
        values = toolbox.get_reference_slice(record_id, [p for p in paths if p not in unknown])
        return {"values": {p: {"value": e.value, "present": e.present, "pointer": e.pointer,
                               "reference_version": e.reference_version} for p, e in values.items()},
                "unknown_paths": unknown}

    server.add_tool(outcome, name=f"get_{record_noun}_outcome",
                    description=f"Status, outcome codes with meanings, outcome tokens and the {reference_noun} version pinned by service date.")
    server.add_tool(fields, name=f"get_{record_noun}_fields",
                    description=f"Specific {record_noun} fields by catalog path.")
    server.add_tool(ref_slice, name=f"get_{reference_noun}_slice",
                    description=f"Only the requested {reference_noun} fields, resolved as of the {record_noun}'s service date.")

    @server.tool(description="Search the rules/policy graph for candidate rules in force on a date, with the graph path that reached each.")
    def search_rules(rules_date: str, outcome_tokens: list[str] | None = None, concepts: list[str] | None = None,
                     text: str = "", k: int = 10) -> list[dict[str, Any]]:
        hits = toolbox.search_rules(date.fromisoformat(rules_date), outcome_tokens or [], concepts or [], text, k=k)
        return [{"rule_id": h.rule_id, "score": round(h.score, 3), "path": h.path,
                 "intent": toolbox.get_rule(h.rule_id).intent} for h in hits]

    @server.tool(description="A rule card: intent, condition, fields read, outcome, precedence, overrides and sources.")
    def get_rule(rule_id: str, version: int | None = None) -> dict[str, Any]:
        return toolbox.get_rule(rule_id, version).model_dump(mode="json")

    @server.tool(description="Timeline of a rule domain's effective-dated releases with reasons.")
    def list_rule_history(domain: str) -> list[dict[str, Any]]:
        return toolbox.list_rule_history(domain)

    @server.tool(description="Rules added, removed or changed in a rule domain between two dates.")
    def diff_rule_sets(domain: str, from_date: str, to_date: str) -> dict[str, Any]:
        return toolbox.diff_rule_sets(domain, date.fromisoformat(from_date), date.fromisoformat(to_date))

    @server.tool(description="Taxonomy concepts recognised in text, with definitions and owning agents.")
    def lookup_concept(text: str) -> list[dict[str, Any]]:
        return toolbox.lookup_concept(text)

    @server.tool(description="Glossary terms and abbreviations found in text, with definitions and linked concepts.")
    def lookup_term(text: str) -> list[dict[str, Any]]:
        return toolbox.lookup_term(text)

    @server.tool(description="A source policy section's text and the rules derived from it ('<doc> / <section>').")
    def get_policy_section(policy: str) -> dict[str, Any]:
        return toolbox.get_policy(policy)

    @server.tool(description="Semantic search over glossary terms, concepts, catalog fields, codes, rules and policy text.")
    def search_knowledge(query: str, kinds: list[str] | None = None, k: int = 5) -> list[dict[str, Any]]:
        return toolbox.search_knowledge(query, kinds, k)

    if planner is not None:
        async def explain(record_id: str, question: str, rules_mode: str = "as_decided",
                          as_of: str | None = None, session_id: str | None = None) -> dict[str, Any]:
            exp = await planner.ask(question, record_id, session_id=session_id, rules_mode=rules_mode,
                                    as_of=date.fromisoformat(as_of) if as_of else None)
            return exp.model_dump(mode="json", exclude={"checked", "trace"})

        server.add_tool(explain, name=f"explain_{record_noun}",
                        description="Run the planner to answer a question about a record. rules_mode: as_decided (default), current, as_of.")
    return server

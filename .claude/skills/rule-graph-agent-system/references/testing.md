# Testing

The reference suite (`tests/`, 44 tests) runs in about 5 seconds with no network and no model. Keep it, and add domain cases as knowledge grows.

| File | Covers |
|---|---|
| `test_knowledge.py` | `validate()` returns no issues; taxonomy classification, agents, precedence and symmetric `related`; longest-label matching ("pa" does not match "pay"); SKOS/OWL JSON-LD export; the ontology rejects invalid edges; rule sets resolve by date and diff; graph paths and scope filtering; semantic synonym expansion |
| `test_rules_and_tools.py` | A missing input makes a finding unverified; pricing amount and cap; templates; reference extraction ignores method calls; full-size payload but small slices; chained pointer parameters; plan versions by service date; derived record fields; missing entries are absent rather than errors |
| `test_agents.py` | Every golden, parametrised; overrides checked; what-if ignores actuals; the gap round is bounded and finds the other domain; unexplained outcomes are logged; current rules mode uses newer versions; session follow-ups reuse findings; a slow agent becomes an error report; definition questions; model guardrails (unknown intents and concepts filtered, narration with unknown rule ids rejected); report JSON round trip |
| `test_adk_a2a_mcp.py` | The ADK workflow takes `gap` then `done`; all agents served over A2A on free ports with correct cards, and the planner answers through them; MCP tool listing, PHI-minimised outcome, rule search |

When adding a domain:
1. Add fixtures and goldens first; they should fail.
2. Add knowledge until `validate` is clean and the goldens pass.
3. Add a focused unit test for any new CEL pattern, pointer parameter or derived field.
4. Pin `--today` (or `Settings(today=...)`) so current-rules results do not drift.

CI (`.github/workflows/ci.yml`) runs `validate`, `eval` and `pytest` on every pull request.

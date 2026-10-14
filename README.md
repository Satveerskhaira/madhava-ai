# madhava

A pharmacy claim explainability layer: agents answer "why did this claim behave this way?" using evidence from claim data, plan setup data (resolved as of date of service), and SME-approved, effective-dated business rules.

A planner agent routes each question through an adjudication taxonomy to seven domain agents that run in parallel over A2A. Each domain agent searches a rules/policy graph for candidate rules, reads only the claim and benefit setup fields those rules need, evaluates them with CEL, and returns findings with evidence. The planner checks that every observed outcome is explained, loops once when it is not, and answers with rule citations, stating any gap instead of guessing.

## Quick start

```bash
pip install -e ".[dev]"
madhava validate        # CI checks: catalog links, CEL compiles, releases, ontology
madhava eval            # golden claims (13/13)
madhava ask RX-1001 "Why was this rejected, and what would it cost if approved?"
madhava ask RX-1007 "Why did this reject?" --trace
madhava ask RX-1012 "Would this claim still pay today?"
pytest                  # 44 tests, including A2A and MCP end to end
```

Sample claims cover every agent: prior authorization, step therapy, quantity and specialty channel limits, refill too soon, drug interaction, exclusion, eligibility, COB, copay, coinsurance and deductible, plus an unexplained code and a rule change after the claim.

## Layout

| Path | What it is |
|---|---|
| `knowledge/ontology/` | Domain ontology (classes and typed relations), enforced on the policy graph |
| `knowledge/taxonomy/` | SKOS adjudication taxonomy: agents, precedence, synonyms, outcome notations |
| `knowledge/catalog/` | Field catalog (NCPDP ids, JSON Pointers into benefit setup) and reject codes |
| `knowledge/domains/` | Rule cards (CEL) and effective-dated rule sets per rule domain |
| `knowledge/procedures/` | Procedural memory: planner procedures and the domain agent playbook |
| `fixtures/` | Synthetic claims and a versioned benefit setup (~12k fields) |
| `goldens/` | Golden claims replayed by `madhava eval` |
| `src/madhava/agents/` | Planner, domain agents, synthesis, language layer, registry and A2A transport |
| `src/madhava/memory/` | Working, episodic, semantic, procedural and taxonomic memory |
| `src/madhava/rules/` | Rule store, policy graph search, CEL evaluator, CI validation |
| `src/madhava/tools/` | Claims and benefit setup API clients, toolbox, MCP server |
| `src/madhava/adk/` | Planner as an ADK 2 workflow graph; domain agents served over A2A |

## Docs

- [`docs/agent-system.md`](docs/agent-system.md): the agent system: layers, knowledge model, memory types, graph search, loops, standards, scaling
- [`docs/claim-explainability-design.md`](docs/claim-explainability-design.md): initial design (v0.1)
  - Rule graph: claim fields, rules, plan fields
  - LLM-assisted rule card generation from existing docs
  - MongoDB data model with Atlas Vector Search
  - Git-first rule lifecycle, with a UI option later
  - Historical rule sets and rule-mode evaluation
  - MCP tools, guardrails, rollout, open questions
- [`docs/madhava-architecture.html`](docs/madhava-architecture.html): interactive architecture page (open in a browser)
  - How it explains: layers, domain agents over A2A, rule cards linked to plan data, trust controls, scaling
  - End-to-end flow: step-through diagrams for a member question, a rule change and a batch audit
  - Backend technology: Python and Google ADK for the orchestrator and agents, Java and Spring Boot for MCP tool servers, where model calls happen
  - Agent layers: how Madhava maps to AI, agents, harness, loops and graphs, with roles and implementation steps for each

## Status

Reference implementation over synthetic data. The Python toolbox defines the tool contract that the production Java MCP servers will implement. Real claims and benefit setup APIs plug in through `MADHAVA_CLAIMS_URL` and `MADHAVA_BENEFITS_URL`.

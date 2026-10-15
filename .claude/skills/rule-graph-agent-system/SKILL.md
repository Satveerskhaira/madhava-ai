---
name: rule-graph-agent-system
description: Build or extend a multi-agent "why did this behave this way?" explanation system - a planner agent plus domain agents that search a rules/policy graph, read only the needed fields from source APIs, evaluate CEL rule cards deterministically and answer with cited evidence. Uses a taxonomy, ontology, field catalog, effective-dated rule sets, procedural/semantic/taxonomic/episodic/working memory, bounded agent loops, Google ADK workflow graphs, A2A and MCP. Use this whenever someone wants to build, scaffold, extend or review an agent that explains claims, adjudication, rejects, pricing, eligibility, policy or rule outcomes (pharmacy, medical, insurance, payments, credit, compliance), mentions planner and domain/sub-domain agents, rule or policy graph search, taxonomy or ontology for agents, agent memory types, or the madhava architecture - even if they don't name this skill.
---

# Rule-graph agent system

A proven pattern for answering "why did this transaction behave the way it did?" when the engine that decided it emits no trace. The reference implementation is the **madhava** pharmacy-claim explainer (this repository: `src/madhava`, `knowledge/`, `docs/agent-system.md`). It works end to end: 13/13 golden claims, 44 tests, A2A and MCP verified. Reuse it rather than re-deriving it.

## The shape of the system

```
question + record id
  -> planner (ADK workflow graph)
       observe -> understand -> plan -> act (A2A fan-out) -> verify --gap (max 1)--> act
                                                                \--done--> synthesize -> remember
  -> domain agents (one per top taxonomy concept), each running a fixed playbook:
       recall_rules (policy graph) -> resolve_inputs (APIs via field catalog) -> evaluate (CEL)
       -> apply_overrides -> widen_if_unexplained (max 1) -> phrase
  -> answer: primary reason, supporting facts, overrides, what-if, gaps, rules/plan context, citations
```

Read `references/architecture.md` for the full design (layers, memory types, graph search, loops, standards, scaling).

## Design rules, and why they matter

1. **Models never decide outcomes.** Routing, rule retrieval, evaluation, overrides, precedence and coverage are deterministic. A model only understands the question and phrases the answer, at most two calls. This makes answers auditable, replayable and cheap, and it stops the model from inventing a reason.
2. **Rules are data, not code.** Each rule is a versioned card: inputs from a closed field catalog, a CEL condition, an outcome token, a template and sources. Releases are effective-dated per rule domain. You can then explain old records with the rules in force at the time, and compare them with today's rules.
3. **The taxonomy defines the agents.** Top concepts carry `agent` and `precedence` (decision order); leaves carry `rule_domain` and `notations` (outcome tokens). A new domain is then a knowledge change, not a code change, and the primary reason follows the real decision order.
4. **The ontology is enforced.** Catalog fields sit on ontology classes, and the policy graph rejects edges the ontology does not declare. This keeps the graph meaningful as it grows.
5. **Agents read slices, not payloads.** Catalog paths map to JSON Pointers, with parameters filled from the record or other fields. Agents get a context reference (ids and dates), never a copy of sensitive data. Large source payloads (12k+ fields) never reach a model or an agent.
6. **Every loop has a verifier and a cap.** The planner's gap round is stopped by a coverage check (every observed outcome token needs a fired rule). A domain agent widens its search at most once. If an outcome is still unexplained, say so and log it for experts; never guess.
7. **Retrieval explains itself.** Graph hits keep the path that reached them (`outcome:reject:76 <-explains- rule:R-NET-001`), so traces show why a rule was considered.

## Build order

Work inside out by risk. The harness carries the correctness, so prove it before adding agents, and add loops last, once a verifier exists to stop them. After each step, `validate`, `eval` and `pytest` must stay green.

### 0. Gather the domain facts

Ask the user, or find in their docs and code, before writing anything:
- the **records** being explained and their API (fields, ids, dates, outcome codes)
- the **reference data API** the rules read (plan or product setup), and how it is versioned by date
- the **outcomes** to explain (reject or decline codes, price components, statuses) and their official code list
- the **decision order** of the engine (which checks run first)
- where **rules** are written down today (policy docs, BRDs, config guides), and who owns each area
- which date applies the rules: decision date or service date

If the user only gives a domain name, propose a first taxonomy and a few outcome codes and confirm them before building.

### 1. Scaffold from the reference

```bash
python .claude/skills/rule-graph-agent-system/scripts/scaffold.py --dest ../<project> --package <name>
cd ../<project> && pip install -e ".[dev]" && <name> validate && <name> --today 2026-10-14 eval && pytest
```

This copies the working implementation under a new package name, with the pharmacy example knowledge, so the project starts green. Outside this repository, clone `https://github.com/Satveerskhaira/madhava-ai` and pass `--source`. If scaffolding is not wanted (another language or framework), port the same modules, using `references/architecture.md` as the spec.

### 2. Replace the knowledge, one domain at a time

Templates and field-by-field guidance are in `references/knowledge-schemas.md`. Order:
1. **Taxonomy:** top concepts per agent with precedence; leaves with `rule_domain`, `notations` and synonyms (`altLabels`); `related` links where one outcome can come from another domain.
2. **Ontology:** classes for the domain's entities, keeping the rule, graph and evidence classes.
3. **Code table and field catalog:** every field a rule may read; pointers and params for reference-data fields.
4. **Rule domains:** `domain.yaml` with effective-dated releases, then rule cards. Draft cards from source documents, but only with catalog paths, and mark low-confidence ones for expert review.
5. **Procedures:** planner procedures per question type; the domain playbook rarely changes.

Run `<name> validate` after every change; it catches undeclared paths, CEL that does not compile, unknown codes, release overlaps and ontology violations.

### 3. Adapt the domain-specific code

A small number of functions encode pharmacy assumptions; the list is in `references/adapting-a-domain.md`. The main ones are claim snapshot derivation and outcome tokens (`tools/toolbox.py`), API clients (`tools/apis.py`), intent patterns (`agents/language.py`) and answer wording (`agents/synthesis.py`). Keep the contracts in `models.py` stable; A2A tasks, reports and findings depend on them.

### 4. Fixtures and goldens

Write one synthetic record per outcome, plus the hard cases: an override, a what-if, an outcome only another domain explains (exercises the gap round), an unknown code (must come back unexplained), a rule change after the record (compare), and reference data that changed by date. Add each to `goldens/goldens.yaml` with the expected primary rule and confidence. The match rate is the quality bar.

### 5. Agents, graph, protocols

These need little change if the knowledge is right. Check the planner trace (`ask ... --trace`), then run as the ADK workflow (`--workflow`), then over A2A (`serve-agents` with `ask --a2a-base-port`), then MCP (`mcp`). Framework specifics that cost time to discover are in `references/framework-notes.md`: ADK 2 workflow routes and state, `to_a2a` agent cards, A2A client responses, MCP 2.x `MCPServer`, and CEL null and number handling. Read it before touching those layers.

### 6. Finish

- Tests: keep the reference suite and add domain cases (`references/testing.md`).
- CI: `validate`, `eval` and `pytest` on every pull request (the scaffold includes the workflow).
- Docs: adapt `docs/reference-architecture.md` to the domain, and state clearly what is sample data.

## When the pattern does not fit

- The engine already emits a rule trace: read the trace instead of reconstructing it.
- Outcomes come from a learned model rather than rules: use model explainability (feature attributions), not rule cards.
- One-off questions over a handful of documents: plain retrieval is enough.

## Delivery checklist

- [ ] `validate` reports OK; every rule input is in the catalog
- [ ] Goldens cover every agent, an override, a what-if, a gap round, an unexplained code, a compare and a date-resolved reference value; the match rate is 100% on fixtures
- [ ] Answers cite rule id and version, state gaps plainly, and name the rules mode, rule sets and reference-data version used
- [ ] No agent or model receives full records or full reference payloads
- [ ] Loops are capped and visible in the trace
- [ ] Sample data is labelled; code lists and field ids are marked for verification against official sources

## Reference files

These files sit next to this SKILL.md. If they are missing (for example, an account-level install), read them from `.claude/skills/rule-graph-agent-system/` in a checkout of `https://github.com/Satveerskhaira/madhava-ai`.

- `references/architecture.md`: the full design, including memory types, graph search, loops, standards and production mapping. Read it first when designing or explaining the system.
- `references/knowledge-schemas.md`: YAML templates for the taxonomy, ontology, catalog, rule cards, releases, procedures and goldens. Read it when writing knowledge.
- `references/adapting-a-domain.md`: which code to change for a new domain, with a worked example. Read it in step 3.
- `references/framework-notes.md`: verified ADK 2.12, a2a-sdk 1.2, MCP 2.3 and cel-python 0.5 APIs and pitfalls. Read it before changing protocol or framework code.
- `references/testing.md`: the test map and how to add cases.
- `scripts/scaffold.py`: copies the reference into a new project under a new package name.

---
name: rule-graph-agent-system
description: Build, extend or review a planner-based multi-agent system that explains why a record was decided the way it was (claims, orders, payments, credit, eligibility, pricing, compliance) by searching a rules/policy graph, reading only needed fields from source APIs, evaluating CEL rule cards deterministically and answering with cited evidence. Covers both the core agent (a planner over domain agents) and domain agents/subagents (each a planner over rule-domain investigators, shippable as its own repo), built on the SDK-neutral rulegraph kernel with adapters for Google ADK, A2A and MCP and patterns for other agent SDKs (LangGraph, OpenAI Agents SDK, Claude Agent SDK, CrewAI, Semantic Kernel). Use whenever someone wants a core or domain/sub-domain agent that plans, routes by taxonomy, loops on gaps, explains rejects or amounts, or asks about taxonomy, ontology, rule graphs, agent memory types or planner hierarchies - even if they don't name this skill.
---

# Rule-graph agent system

A pattern for answering "why did this record behave the way it did?" when the engine that decided it leaves no trace. The code lives in the **rulegraph** kernel (a plain Python package, no agent SDK in its core). Projects built on it contain knowledge (YAML), one small record adapter, and wiring. Reuse the kernel; do not re-derive it.

## The shape

Every level is the same planner with a different scope and different executors.

```
user / any agent SDK --(adapter: ADK workflow, A2A, MCP, your SDK)--> CORE planner
  CORE planner          executors = domain agents (in process, installed packages; no network hop)
    understand -> plan -> act (fan-out) -> verify --gap round (bounded)--> act -> synthesize -> remember
  DOMAIN agent          = planner scoped to one taxonomy branch; executors = rule-domain investigators
    same loop: routes by its own taxonomy branch, sweeps its scope if unrouted, own gap round,
    answers directly ("from the credit point of view...") or reports findings + gaps to the core
  INVESTIGATOR          fixed playbook, no model:
    recall_rules (policy graph) -> resolve_inputs (field catalog -> API slices) -> evaluate (CEL)
    -> apply_overrides -> widen_if_unexplained (max 1) -> phrase
```

`Planner` is also an `Executor` (`name` + `async handle(DomainTask) -> DomainReport`). So a domain agent can be used by a core planner, asked directly, tested alone, or put behind any transport. Read `references/architecture.md` for the full design.

Code and knowledge are split so each domain can be owned and released on its own:

```
rulegraph kernel        planner, investigators, memory, rule graph, CEL, sources, composition, discovery
base package            base pack (manifest, ontology, codes, top concepts with decision order, shared fields,
                        shared procedures) + record adapter + toolbox + fixtures + CLI
domain package / repo   one fragment per domain agent: concepts under its top concept, its fields,
  (one per domain)      rule domains and rules, investigator notes, domain goldens; entry point
                        in `rulegraph.domains`
core repo               core procedures, cross-domain goldens, exposure (A2A, MCP, ...); composes the base
                        with every installed domain package at start-up
```

At run time the core builds one knowledge set (base + fragments, from files today or a database later), one toolbox and one episodic memory, and hands them to every domain agent: domains share context and memory in process, not over a network.

## Design rules, and why

1. **Models never decide outcomes.** Routing, retrieval, evaluation, overrides, precedence and coverage are deterministic. A model only understands the question and phrases the answer (optional, at most two calls). Answers are then auditable, replayable and cheap, and a model cannot invent a reason.
2. **Rules are data.** Versioned rule cards (inputs from a closed field catalog, CEL condition, outcome token, template, sources) in effective-dated releases. Old records are explained with the rules in force then, and compared with today's.
3. **The taxonomy defines the agents.** Top concepts carry `agent` and `precedence`; leaves carry `rule_domain` and `notations` (outcome tokens). A new domain agent is a knowledge change.
4. **The ontology is enforced.** The policy graph rejects edges the ontology does not declare.
5. **Agents read slices, not payloads.** Catalog paths map to JSON Pointers. Tasks carry a context reference (ids, dates), never record data.
6. **Every loop has a verifier and a cap.** Coverage check: every observed outcome token needs a fired rule. Gap rounds are bounded at each level. Still unexplained means say so and log it for experts; never guess.
7. **The kernel is SDK-neutral.** Agent SDKs are adapters at the edges. Models plug in as a plain `async complete(system, user) -> str`.
8. **Retrieval explains itself.** Graph hits keep the path that reached them (`outcome:reject:FL1 <-explains- rule:R-FL-001`), so traces show why a rule was considered.
9. **Core to domain agents is in process.** Domain agents are installed packages the core discovers and imports. Only the agent you expose (usually the core) gets a network interface. Add a transport between core and domain agents only if they must scale or deploy separately, by wrapping a client as an `Executor`.

## Decide what is being built

- **Base package**: the shared base pack, record adapter, toolbox and fixtures. Built once per business area (one record API). Lives with the core until it needs its own repo.
- **Domain agent repo** (one per domain, the default when domains have different owners): a fragment pack that extends the base, a `DomainPackage` entry point, domain goldens, tests, CI. Knows nothing about the core or other domains. See `references/domain-repos.md`.
- **Combined domain agents repo**: several fragments in one package (each still its own fragment and entry point), when one team owns several domains or while splitting gradually. Moving a domain out is moving one folder.
- **Core agent repo**: depends on the base, discovers installed domain packages, adds core procedures, cross-domain goldens and the external interface (A2A, MCP, ADK, or another SDK).
- **Single-pack project**: one pack with every domain (what `rulegraph new subagents` scaffolds). Fine to start with; split into base + fragments when domains get separate owners.
- **Extending** an existing project: skip to the build steps that apply.

## Build steps

Keep `validate`, `eval` and the tests green after every step.

### 0. Gather the domain facts

Ask the user, or find in their docs and code:
- the **records** being explained and their API (ids, dates, status, outcome codes, amounts)
- the **reference data** the rules read (plan, product, account terms) and how it is versioned by date
- the **outcomes** to explain and the official code list
- the engine's **decision order** (which checks run first)
- where **rules** are written today (policy docs, config guides) and who owns each area
- which date applies the rules: decision date or service date

If you only get a domain name, propose a first taxonomy and a few outcome codes and confirm them before building.

### 1. Get the kernel

`pip show rulegraph`. If missing, look for a `rulegraph` pin in the repo's `requirements*.txt` or `pyproject.toml`; otherwise ask the user for the kernel's pip spec (while it lives inside another repo it is a git URL ending `#subdirectory=kernel`). Extras: `rulegraph[adk]` (Google ADK + A2A), `rulegraph[mcp]`.

### 2. Scaffold

```bash
rulegraph new subagents --dest <dir> --package <pkg>_agents --kernel-spec "<kernel pip spec>"
rulegraph new core --dest <dir> --package <pkg>_core --agents-package <pkg>_agents \
    --kernel-spec "<kernel pip spec>" --agents-spec "<agents pip spec, e.g. git URL>"
cd <dir> && pip install -r requirements-dev.txt && <dist> validate && <dist> --today <YYYY-MM-DD> eval && pytest
```

The subagents template ships a small working example pack (orders: credit, fulfillment, charges) so it starts green. The core template is wired to it. `--dest` may be an empty cloned repo. These templates are single-pack; to give each domain its own repo, split the pack into a base and one fragment per domain as in `references/domain-repos.md` (the split is mechanical: fields read by one domain go with it, the rest to the base).

### 3. Replace the knowledge, one domain agent at a time

Follow `references/adapting-a-domain.md`; schemas are in `references/knowledge-pack.md`. Per domain agent: taxonomy branch, catalog fields, codes, rule domains with releases and rule cards, glossary terms, the policy sections the rules cite (with their text), fixtures, then domain goldens (`agent`, `record`, `question`, `expect`). Run `<dist> validate` after each file; it checks JSON Schema per file and the links between files. `rulegraph schemas --out .schemas` gives editors the same schemas.

### 4. Write the record adapter

`record.py` maps the record API to a `RecordView`: status, `adverse`, outcome tokens (`<codes_kind>:<code>`), amount tokens (`price:<component>` when charged), reference id, service and decision dates, and derived fields rules need (for example days since enrollment). It is the only domain code. Point the toolbox at HTTP APIs via `HttpRecordApi`/`HttpReferenceApi` path templates, or implement the two small protocols.

### 5. Tune procedures

- Domain agents: `pack/procedures/domain_planner.yaml` (`delegated_explain`, `delegated_what_if` for core tasks; `explain_outcome`, `explain_cost`, `what_if_approved`, `compare_current`, `define_term` for direct questions). Keep `sweep_if_unrouted: true` and `loop.expand: [policy_graph, related_concepts, sweep_scope]`.
- Investigators: `investigator.yaml` playbook and per-agent notes.
- Core: `procedures/planner.yaml`; `speculative` lists agents always asked (`"*"` = all), `must_explain` sets the coverage check.
- Intents: regex patterns in `intents.yaml`, used when no model is configured.
- Wording: `pack.yaml` `phrases` and `token_labels` (see `references/knowledge-pack.md`).

### 6. Core goldens and loop checks

Core goldens prove cross-domain behaviour. Include at least: one per domain agent, one gap-round case (`agents_in_round_2`), one unexplained case (`gap`), one amount, one what-if, one `compare_current`. See `references/testing.md`.

### 7. Expose the agent through an SDK

Only at the edges. ADK is implemented and tested (`rulegraph.adapters.adk`: `build_workflow`, `a2a_app`, `adk_completion`, `AdkExecutor`); MCP too (`rulegraph.adapters.mcp.build_mcp_server`). For any other SDK follow `references/sdk-adapters.md`: wrap `planner.ask` as that SDK's tool, node or agent, and pass a `complete` function for model understanding. Never import an SDK in pack, adapter or kernel-core code.

### 8. Make the knowledge visible and portable

- `view` (on the kernel CLI or any project CLI built with `rulegraph.commands`): one offline HTML page with the graph, taxonomy, ontology, glossary, rules, fields, policies and outcomes. Use it in SME reviews.
- `export graph --format ttl|jsonld`, `export taxonomy|ontology|glossary|shapes|graphml`: standard files for Protégé, graph stores, yEd or Gephi.
- `shacl`: the exported graph must conform to its shapes.
- `publish`: packs to MongoDB with a release pointer, when the knowledge moves off files.

### 9. Verify and hand over

Run `validate`, `eval`, `pytest` in each repo; ask a few questions with `--trace` and read the trace (routes, rounds, gaps). Each domain repo's CI installs the kernel and base and tests its fragment alone on the base; the core's CI installs kernel, base and every domain package from their pins and replays cross-domain goldens. Tag releases so pins are reproducible. The kernel's distribution name `rulegraph` is also taken on PyPI by an unrelated project, so always install it from its pinned source (git URL or internal index), never by bare name.

## When the pattern does not fit

- The engine already emits a rule trace: read the trace instead of reconstructing it.
- Outcomes come from a learned model rather than rules: use model explainability (feature attributions), not rule cards.
- One-off questions over a handful of documents: plain retrieval is enough.

## Delivery checklist

- [ ] `validate` reports OK in every repo; every rule input is in the catalog
- [ ] Each domain fragment composes with the base alone and passes its own goldens; the core composes all of them with no conflicts
- [ ] Every rule cites a policy section that has text; the glossary covers the domain's terms and abbreviations; the graph conforms to its SHACL shapes
- [ ] Domain goldens per agent (own outcome, amount, out-of-scope token, gap); core goldens cover every agent, an override, a what-if, a gap round, an unexplained code, a compare and a date-resolved reference value; 100% on fixtures
- [ ] Answers cite rule id and version, state gaps plainly, and name the rules mode, rule sets and reference-data version used
- [ ] No agent or model receives full records or full reference payloads
- [ ] Loops are capped at every level and visible in the trace
- [ ] No SDK import outside adapter modules; the deterministic path runs with no model
- [ ] Sample data is labelled; code lists and field ids are marked for verification against official sources

## When reviewing an existing system

Check: models deciding outcomes; rules embedded in code; unbounded loops or loops without a verifier; agents receiving full payloads; domain agents called over a network without need; an SDK imported in core logic; outcome codes with no notation and no goldens; release ranges overlapping; goldens missing for gap and unexplained cases.

## References

- `references/architecture.md`: levels, loop, memory types, graph search, routing, scaling
- `references/knowledge-pack.md`: every pack file with schema and example; base packs, fragments and composition
- `references/domain-repos.md`: one repo per domain: what goes in the base and in each domain repo, entry points, sources (files, MongoDB), CI, splitting a single pack
- `references/adapting-a-domain.md`: checklist for replacing the example knowledge
- `references/sdk-adapters.md`: ADK, A2A, MCP, and patterns for LangGraph, OpenAI Agents SDK, Claude Agent SDK, CrewAI, Semantic Kernel
- `references/kernel-notes.md`: SDK-neutral details: CEL pitfalls, async and deadlines
- `references/adapters/<sdk>.md`: verified notes for one adapter, read only when using it: `google-adk.md` (ADK workflow, A2A, ADK models), `mcp.md`. Add a file here when an adapter for another SDK is built and tested
- `references/testing.md`: goldens, expect keys, tests every project should keep

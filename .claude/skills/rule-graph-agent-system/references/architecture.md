# Architecture

Contents: 1 Layers · 2 Planner graph · 3 Domain agent playbook · 4 Memory · 5 Policy graph search · 6 Picking the answer · 7 Loops · 8 Data access · 9 Standards · 10 Production mapping

## 1. Layers

| Layer | Question | Reference modules |
|---|---|---|
| AI | What needs language understanding? | `agents/language.py`: deterministic default; `ModelLanguage` wraps any ADK `BaseLlm` with guardrails |
| Agents | Who owns which part of the answer? | `agents/planner.py`, `agents/domain.py`, `agents/registry.py` (agent cards, in-process and A2A transports) |
| Harness | What can an agent see and do? | `tools/toolbox.py`, `tools/mcp_server.py`, `catalog.py`, `rules/` (store, graph, evaluator, validate), `knowledge/` |
| Loops | When to retry, and when to stop? | `Planner.verify`/`reflect`, domain `widen_if_unexplained`, session reuse, `evaluation.py` |
| Graphs | How does work move? | `adk/workflow.py` (ADK 2 Workflow), A2A fan-out, `rules/graph.py` |

## 2. Planner graph

Nodes share one blackboard (`memory/working.py`, a pydantic model kept in ADK session state):

- **observe:** fetch the record once (`get_claim_outcome`), derive fields, build outcome tokens (`reject:<code>`) and price tokens (`price:<component>`), pin the reference-data version by service date.
- **understand:** intents (`explain_outcome`, `explain_cost`, `what_if_approved`, `compare_current`, `define_term`) and taxonomy concepts from the question. Defaults: rejected → explain_outcome; paid → explain_cost; cost words on a rejected record → what-if.
- **plan:** select procedures by intent (procedural memory). Route each token through `taxonomy.classify` → concept → `agent_for`; route question concepts the same way; add each procedure's speculative agents. One `DomainTask` per (agent, mode, rules_mode). Set `must_explain`, `max_rounds`, the expansion strategies and the rules modes (as_adjudicated, plus current when comparing). Reuse session reports from episodic memory for follow-ups.
- **act:** send pending tasks in parallel (`asyncio.gather` with a per-agent deadline). A late or failed agent becomes an error report, not a failure.
- **verify:** the coverage check. Each `must_explain` token needs a fired finding with confidence confirmed or likely, in explain mode and the primary rules mode. Returns `done`, or `gap` when tokens are missing, rounds remain and `reflect` found new work.
- **reflect:** for each gap token, try in order:
  - `policy_graph`: search the whole graph from the token; rules whose outcome is that token name their agent and become hints.
  - `related_concepts`: `skos:related` concepts of the token's concept.
  Skip agents already asked about that token.
- **synthesize:** pick the primary reason (section 6), list supporting facts, overrides, what-if estimates, conflicts, unverified rules, agents with no issue, gaps, the rules context (rule sets used, changes since the record) and the reference-data context. Optionally let a model narrate, with its citations checked.
- **remember:** write the episode (with reports) and each gap to episodic memory.

ADK wiring: `(START, intake, understand, plan, act, verify)`, `(verify, {"gap": act, "done": synthesize})`, `(synthesize, remember)`.

## 3. Domain agent playbook

Fixed steps from `knowledge/procedures/domain.yaml`:

1. **recall_rules:** graph search seeded by the task's tokens, concepts (or the agent's top concept), hints and question text. Restricted to the agent's taxonomy branch and to rules in force on the rules date; filtered by the mode's outcome types.
2. **resolve_inputs:** `toolbox.inputs_for(record, cards)` returns the union of declared inputs: record fields, plus a reference-data slice.
3. **evaluate:** CEL condition per rule in `order`; pricing rules also compute `amount` and read `actual`.
4. **apply_overrides:** for each fired rule with `overridden_by`, evaluate the override (resolving its version, even in another domain). If it fires, the rule becomes `overridden`.
5. **widen_if_unexplained:** if a task token is still unexplained in explain mode, recall once more with token descriptions and the top concept, and evaluate only new rules.
6. **phrase:** render templates with evidence values (`{{path}}`, `{{amount|money}}`, `{{list|list}}`).

Confidence per finding:
- `confirmed`: fired, its token was observed, and (for pricing) amount equals actual
- `likely`: fired but the amount differs, or its token was not observed (a conflict)
- `supporting`: info and override rules
- `what_if`: what-if mode
- `unverified`: inputs missing or the expression failed

## 4. Memory

| Type | Holds | Used for | Reference store | Production store |
|---|---|---|---|---|
| Working | Blackboard for one question | Graph state, replay | ADK session state | ADK session service (Firestore, Redis, SQL) |
| Episodic | Episodes with reports; gaps | Audit, session follow-ups, expert backlog | JSONL | BigQuery or a warehouse; explanation log |
| Semantic | Rules, fields, concepts, codes as text | Text seeds, definitions, widening | BM25 with taxonomy synonym expansion; optional `embed` | Vector search (for example MongoDB Atlas) |
| Procedural | Planner procedures, domain playbook | What to ask, what must be explained, loop caps, investigation steps | YAML | YAML in Git |
| Taxonomic | SKOS concept scheme | Classify tokens, recognise concepts, agent ownership, precedence, related expansion | YAML | YAML, exported as JSON-LD |

## 5. Policy graph search

Nodes: `concept:`, `outcome:`, `rule:`, `field:`, `policy:`. Edges (checked by the ontology): `broader`, `related`, `classifies` (taxonomy); `explains`, `in_domain`, `reads`, `overridden_by`, `derived_from` (rule cards); `describes` (catalog). The graph is the union over rule versions; version filtering happens at search time.

Walk weights by (relation, direction):

| Relation and direction | Weight |
|---|---|
| explains in | 1.0 |
| classifies in | 1.0 |
| in_domain in | 0.9 |
| broader in (to narrower concepts) | 0.85 |
| overridden_by out | 0.7 |
| related | 0.6 |
| reads in | 0.6 ÷ √(readers of the field) |
| derived_from in | 0.6 |
| classifies out | 0.6 |
| broader out | 0.5 |
| in_domain out | 0.5 |
| explains out | 0.4 |
| reads out | 0.3 |

Best-first walk per seed, up to four hops, minimum score 0.05. A rule's score is the sum over seeds of the best path product. Hits keep that path.

Seeds: outcome tokens 1.0, concepts 0.8, hints 1.0, semantic text hits 0.5 × normalised BM25.

## 6. Picking the answer

From explain-mode findings in the primary rules mode, the explaining findings are those that fired, are confirmed or likely, and whose token was observed. Sort them by (taxonomy precedence, rule order). The first is the **primary reason**; the rest are supporting.

- Overall confidence is the primary's confidence: `likely` when some tokens are still gaps; `unexplained` when there is no primary and gaps remain; `likely` for a compare-only projection.
- Compare mode: diff the fired reject tokens as adjudicated against current rules, and name the releases between the two dates that caused the difference.

## 7. Loops

| Loop | Verifier | Cap |
|---|---|---|
| Planner gap round | Coverage of `must_explain` tokens | Procedure `max_rounds` (2); only new agent/token pairs |
| Domain widening | CEL evaluation | Once per task |
| Session follow-up | Coverage per answer | User-driven |
| Improvement | Golden match rate, gap log | Every change must keep the match rate |

## 8. Data access

- Record API: fetched once and cached per record. Derived fields are computed in the tool (days since enrollment, days since last fill).
- Reference-data API: versioned by service date; cached per (plan, date) and per (plan, version, pointer).
- Field catalog pointers: `"/costShare/tiers/{tier}/{channel}/copay"` with `params: {tier: plan.formulary.drug.tier, channel: claim.pharmacy.channel}`. Parameters resolve recursively from the record or other reference fields.
- Rules date: by default the decision (adjudication) date; current; or a given date. Reference data always uses the service date.

## 9. Standards

| Need | Standard |
|---|---|
| Agent to agent | A2A 1.0 (agent cards, JSON-RPC, streaming) |
| Agent to tools | MCP (Model Context Protocol) |
| Orchestration | Google ADK 2 workflow graph |
| Rule expressions | CEL |
| Taxonomy | W3C SKOS (JSON-LD) |
| Ontology | OWL / RDFS (JSON-LD) |
| Schemas | JSON Schema via pydantic |
| Field access | JSON Pointer, RFC 6901 |
| Tracing | OpenTelemetry |
| Pharmacy domain | NCPDP Telecommunication D.0 (F6 next), NCPDP Formulary and Benefit, HL7 FHIR R4 (Claim, ClaimResponse, ExplanationOfBenefit, Coverage) |
| Other domains | Use the domain's code list and exchange standard (X12 835/837 for medical claims, ISO 20022 for payments, and so on) in the code table and the ontology's `aligns_with` |

## 10. Production mapping

| Concern | Production choice |
|---|---|
| Agents | One stateless deployment per domain agent, autoscaled on in-flight tasks |
| Tools | MCP servers in the platform's language, implementing the same tool contract |
| Rules and releases | Git-first authoring, published to a database; agents refresh on change streams |
| Policy graph | In memory per pod (rules number in the thousands), rebuilt on change |
| Caches | Redis keyed by version |
| Models | LLM gateway; small model to understand, stronger model to narrate |

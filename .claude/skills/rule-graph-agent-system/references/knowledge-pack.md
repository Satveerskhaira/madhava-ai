# Knowledge pack

A pack is a folder (usually inside the domain agents package, `src/<pkg>/pack/`) with a `pack.yaml` manifest. `rulegraph validate <pack>` (or `<dist> validate`) checks it. YAML gotcha: in flow mappings `{...}`, quote values containing a comma, a colon-space, or a `?`.

## Base packs and fragments

A pack is either a **base** (`kind: base`, the default) or a **fragment** (`kind: fragment`, `extends: <base name>`, `agents: [...]`) that adds domains to a base. A fragment's `files` may name only `taxonomy` (a `concepts:` list, no scheme), `catalog`, `codes`, `domains`, `ontology` (extra classes or relations) and `investigator` (`agents:` notes only). `Knowledge.compose([base, *fragments])` merges them and fails on conflicts; `Knowledge.load(dir)` is a single base pack. See `domain-repos.md` for what belongs where.

## pack.yaml

```yaml
name: orders-agents
version: "0.1"
agent_prefix: orders          # agent card names: <prefix>-<agent>
roots: {record: order, reference: terms}     # first segment of catalog paths
nouns: {record: order, reference: account terms}   # used in answers and MCP tool names
codes_kind: reject            # outcome token kind for codes: reject:<code>
files:
  taxonomy: taxonomy/outcomes.taxonomy.yaml
  glossary: glossary/glossary.yaml          # SKOS glossary scheme + terms (fragments: terms only)
  policies: policies/policies.yaml          # source policy sections with text
  ontology: ontology/domain.ontology.yaml
  catalog: catalog/fields.yaml
  codes: catalog/codes.yaml
  domains: domains
  investigator: procedures/investigator.yaml
  domain_planner: procedures/domain_planner.yaml
  intents: procedures/intents.yaml
rules_modes: {as_decided: as decided, current: current, as_of: as of}   # labels in answers
token_labels: {reject: "reason {value} ({meaning})", price: "{value} charge"}
phrases:                      # override any default answer phrase
  amount_paid: "The customer was charged {actual}. Reason: {narrative} {cite}"
```

Phrase keys and placeholders: `primary_outcome` {token, narrative, cite}; `amount_paid` {actual, narrative, cite}; `supporting`; `overridden` {intent, rule, reason}; `what_if` {narrative, rule}; `conflict` {intent, token, rule}; `unverified` {rule, error}; `checked` {when, names}; `gap` {token}; `gap_scoped` {token} (domain agents); `out_of_scope` {tokens}; `reference_line` {id, version, effective_from, service_date}; `agent_header` {label}.

## Taxonomy (SKOS)

```yaml
scheme: {id: "orders:outcomes", prefLabel: Order decisions, version: "0.1", namespace: "https://example.org/orders/taxonomy/"}
concepts:
  - {id: fulfillment, prefLabel: Fulfillment, agent: fulfillment, precedence: 20,
     definition: Whether the order can be shipped as placed., altLabels: [fulfillment]}
  - {id: fulfillment.region, broader: fulfillment, prefLabel: Delivery region, rule_domain: regions,
     notations: ["reject:FL1"], definition: ..., altLabels: [region, delivery area], related: [charges.shipping]}
```

- One top concept per domain agent (`agent`, `precedence` = engine decision order, lower first). Keep 5 to 9 agents.
- Leaves: `rule_domain` and `notations` (outcome tokens). Each code in scope in exactly one notation, unless deliberately left for the gap round.
- `altLabels` drive deterministic understanding (whole word, longest first); avoid common words.
- `related` links concepts whose outcomes can come from each other (used by the gap round).

## Ontology

Keep the graph classes (`Outcome, Concept, Rule, RuleSet, Policy, Field, Term, Finding, Evidence`) and relations with their domains and ranges:
- used by rule search: `explains, in_domain, reads, overridden_by, derived_from, broader, related, classifies, describes`
- structural (export, impact analysis, browsing; never steer search): `depends_on` (Field→Field, from catalog `params`), `member_of` (Rule→RuleSet), `supersedes` (RuleSet→RuleSet, `aligns_with: prov:wasRevisionOf`), `about` (Term→Concept, `aligns_with: skos:closeMatch`). A structural edge is added only when the ontology declares its relation.

Add domain classes (the record, the reference entities) freely; catalog `entity` values must be classes here. Data-model relations between them (claim has member, and so on) describe the record's shape and are never filled into the graph.

## Glossary (SKOS)

```yaml
scheme: {id: "orders:glossary", prefLabel: Order glossary, namespace: "https://example.org/orders/glossary/"}   # base only
terms:
  - {id: credit-limit, prefLabel: Credit limit, altLabels: [limit], concepts: [credit.limit],
     definition: "The most an account may owe at once under its terms."}
```
Separate from the taxonomy: the taxonomy classifies outcomes and owns routing; the glossary defines words and abbreviations. `id` is kebab-case; labels and abbreviations must be unique across the glossary; `concepts` must exist (a base term may only point at top concepts). Code meanings from the code table become terms `code-<value>` automatically, so write each meaning once. Definition questions are answered from exact term and abbreviation matches first.

## Policy sections

```yaml
policies:
  - {doc: Fulfillment Guide, section: "1.3", title: Delivery regions, owner: fulfillment-team,
     version: "3", effective_from: "2026-01-01",
     text: "Orders ship only to regions listed in the account terms; other regions are rejected."}
```
`doc` + `section` must match the rule cards' `source_refs` exactly (the id is `<doc> / <section>`, or `<doc>` with no section). Once a pack has any policy text, every cited section must have text. Mark illustrative text `sample: true`. Answers carry these passages in `Explanation.sources`.

## Codes

```yaml
codes: {CR1: Credit limit exceeded, "75": Prior Authorization Required}
```
Quote numeric codes. Record the source edition.

## Field catalog

```yaml
fields:
  - {path: order.region, source: record, entity: Order, type: string, concept: fulfillment.region, description: ...}
  - {path: terms.shipping.fee, source: reference, entity: AccountTerms, type: number,
     pointer: "/shipping/fees/{region}", params: {region: order.region}, concept: charges.shipping, description: ...}
```
- `source: record` paths mirror the record JSON below the root segment. Derived fields from the record adapter are declared the same way.
- `source: reference` paths need `pointer`; every `{param}` needs a `params` entry whose value is another catalog path.
- Extra keys (for example a standard's field id) are kept in `FieldDef.extra`.

## Rule domain

`domains/<rule_domain>/domain.yaml`:
```yaml
domain: regions
concepts: [fulfillment.region]
owner: fulfillment-team
releases:
  - {id: RS-REG-1, effective_from: "2026-01-01", effective_to: "2026-10-01", status: archived, reason: ..., rules: [R-FL-001@1]}
  - {id: RS-REG-2, effective_from: "2026-10-01", effective_to: null, status: active, previous: RS-REG-1, reason: ..., rules: [R-FL-001@2]}
```
Ranges must not overlap (`effective_to` exclusive). Published versions are immutable: change a rule with a new version file and a new release.

## Rule card

`domains/<rule_domain>/rules/<ID>.v<N>.yaml`:
```yaml
id: R-FL-001
version: 1
status: approved                    # draft | in_review | approved | deprecated
domain: regions
concept: fulfillment.region
intent: Reject when the account terms do not allow delivery to the region
outcome: {type: reject, code: FL1}  # reject | pricing | info | override
record_inputs: [order.region]
reference_inputs: [terms.shipping.regions]
condition: "!(order.region in terms.shipping.regions)"
order: 10
overridden_by: []                   # rule ids, any domain
template: "Region {{order.region}} is not in the account's delivery regions ({{terms.shipping.regions|list}})."
source_refs: [{doc: Fulfillment Guide, section: "1.3"}]
confidence: high
```
Pricing: `outcome: {type: pricing, component: shipping, amount: "<CEL>", actual: order.charges.shipping}` (token `price:shipping`; `actual` must be a declared record input). `info` gives a supporting fact; `override` waives rules listing it in `overridden_by`.

Template filters: `money` (2 decimals with thousands separators; put the currency symbol in the template, e.g. `${{x|money}}`) and `list`; booleans print yes/no; `{{amount}}` is the computed pricing amount.

CEL conventions: only declared inputs exist; guard optional ones with `has(...)` (an unguarded missing value makes the finding `unverified`, the right result when data is absent); compare ISO dates as strings; `double()` when mixing integers and decimals.

## Procedures

`investigator.yaml`: `default.steps` (recall_rules k, resolve_inputs, evaluate, apply_overrides, widen_if_unexplained max, phrase), `default.modes` (explain / what_if outcome types), `agents.<name>.notes` (added to the agent's description).

`domain_planner.yaml` and the core `planner.yaml`: procedures with `intents`, `route_from` (`outcome_tokens`, `amount_tokens`, `question_concepts`, `task_tokens`, `task_concepts`, `task_hints`), `speculative` (units always asked, `"*"` = all), `sweep_if_unrouted`, `task_mode` (explain | what_if | none), `must_explain` (outcome_tokens | amount_tokens | task_tokens | none), `rules_modes`, `loop: {max_rounds, expand: [policy_graph, related_concepts, sweep_scope]}`. Domain packs must define `delegated_explain` and `delegated_what_if` (intents with the same names) for core tasks.

`intents.yaml`: `patterns` (intent -> regex, case-insensitive) and `cost_words` (makes a compare question about amounts).

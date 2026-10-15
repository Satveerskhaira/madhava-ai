# Knowledge schemas and templates

All knowledge lives under `knowledge/` and is validated by `<pkg> validate`. Working examples are in the reference repository (`knowledge/`). YAML gotcha: in flow mappings (`{...}`), quote any value containing a comma.

## Taxonomy: `knowledge/taxonomy/<scheme>.taxonomy.yaml`

```yaml
scheme: {id: <pkg>:adjudication, prefLabel: <Domain> decisions, version: "2026.10"}
concepts:
  - id: um                         # top concept = one domain agent
    prefLabel: Utilization management
    definition: Clinical and quantity controls applied before payment.
    agent: utilization_management  # agent name (A2A card madhava-<agent>)
    precedence: 30                 # decision order; lower runs earlier in the engine
    altLabels: [utilization management, um]
  - id: um.prior_authorization     # leaf = one rule domain
    broader: um
    prefLabel: Prior authorization
    definition: Approval required before the item is covered.
    rule_domain: prior_authorization
    notations: ["reject:75"]       # outcome tokens this concept classifies
    altLabels: [prior authorization, prior auth, pa, authorization]
    related: [coverage.tier]       # used by the gap round; symmetric
```

Guidance:
- One top concept per agent; keep 5 to 9 agents.
- Every outcome code in scope should appear in exactly one notation, unless it is deliberately left unclassified so the gap round finds it.
- `altLabels` drive question understanding. Avoid labels that collide with common words ("approved", "pay"); matching is whole-word, longest first.
- Use `related` where one outcome code is produced by more than one domain (for example a "plan limits" reject coming from quantity limits or from channel rules).

## Ontology: `knowledge/ontology/<pkg>.ontology.yaml`

```yaml
namespace: https://<pkg>.example/ontology#
version: "2026.10"
classes:
  Claim: {description: ..., aligns_with: [NCPDP Telecommunication D.0 claim, FHIR Claim]}
  # keep these: Outcome, Concept, Rule, RuleSet, Policy, Field, Finding, Evidence
relations:
  explains:      {domain: Rule, range: Outcome}
  in_domain:     {domain: Rule, range: Concept}
  reads:         {domain: Rule, range: Field}
  overridden_by: {domain: Rule, range: Rule}
  derived_from:  {domain: Rule, range: Policy}
  broader:       {domain: Concept, range: Concept, aligns_with: skos:broader}
  related:       {domain: Concept, range: Concept, aligns_with: skos:related}
  classifies:    {domain: Concept, range: Outcome, aligns_with: skos:notation}
  describes:     {domain: Field, range: Concept}
```

The graph-building relations above must keep these names and ranges. Add domain classes and relations freely.

## Code table: `knowledge/catalog/reject_codes.yaml`

```yaml
codes:
  "75": Prior Authorization Required
  MR: Product Not On Formulary
```

Quote numeric codes. Mark the source edition and tell the user to verify it.

## Field catalog: `knowledge/catalog/fields.yaml`

```yaml
fields:
  - {path: claim.dos, source: claim, entity: Claim, type: date, ncpdp: 401-D1, concept: eligibility,
     description: Date of service}
  - {path: plan.costShare.copay, source: plan, entity: BenefitSetting, type: number,
     pointer: "/costShare/tiers/{tier}/{channel}/copay",
     params: {tier: plan.formulary.drug.tier, channel: claim.pharmacy.channel},
     concept: cost_share.copay, description: Copay for the drug's tier and channel}
```

- `claim.*` paths mirror the record API JSON (the first segment is dropped when reading).
- `plan.*` paths need `pointer`. Each `{param}` must appear in `params`, whose sources are catalog paths.
- `entity` must be an ontology class; `concept` must be a taxonomy concept (this creates `describes` edges).
- The `ncpdp` key is optional; rename it in your copy for other standards (for example `x12`).

## Rule domain: `knowledge/domains/<rule_domain>/domain.yaml`

```yaml
domain: prior_authorization
concepts: [um.prior_authorization]     # each must have rule_domain: prior_authorization
owner: um-sme-team
releases:
  - id: RS-PA-2026.01.1
    effective_from: "2026-01-01"
    effective_to: "2026-10-01"          # exclusive; null while active
    status: archived                    # candidate | active | archived | withdrawn
    change_ref: ENG-REL-4001
    reason: Baseline rules
    rules: [R-PA-001@1, R-PA-OVR-002@1]
  - id: RS-PA-2026.10.1
    effective_from: "2026-10-01"
    effective_to: null
    status: active
    previous: RS-PA-2026.01.1
    reason: PA applies to every drug on a PA tier
    rules: [R-PA-001@2, R-PA-OVR-002@1]
```

Ranges must not overlap. Published versions are immutable: change a rule by adding a new version file and a new release.

## Rule card: `knowledge/domains/<rule_domain>/rules/<ID>.v<N>.yaml`

```yaml
id: R-PA-001
version: 2
status: approved                         # draft | in_review | approved | deprecated
domain: prior_authorization
concept: um.prior_authorization          # leaf concept of this rule domain
intent: Reject when the drug needs prior authorization and none is on file
outcome: {type: reject, code: "75"}      # or pricing / info / override (below)
claim_inputs: [claim.drug.name, claim.priorAuth.onFile]
plan_inputs: [plan.formulary.drug.paRequired, plan.formulary.drug.tier, plan.um.priorAuth.requiredTiers]
condition: >
  ((has(plan.formulary.drug.paRequired) && plan.formulary.drug.paRequired)
   || (has(plan.formulary.drug.tier) && plan.formulary.drug.tier in plan.um.priorAuth.requiredTiers))
  && !claim.priorAuth.onFile
order: 20                                # order within the domain
overridden_by: [R-PA-OVR-002]            # rule ids, any domain
template: "{{claim.drug.name}} needs prior authorization on this plan ..."
source_refs: [{doc: Plan Setup Guide v8, section: "4.2 Prior Auth"}]
confidence: high                         # low | medium | high (drafting confidence)
sme_owner: um-sme-team
```

Pricing outcome:

```yaml
outcome:
  type: pricing
  component: coinsurance                 # token price:coinsurance
  amount: >                              # CEL; double() everything numeric
    double(claim.pricing.submittedCost) * double(plan.costShare.coinsurancePct) / 100.0
      < double(plan.costShare.maxPerFill)
    ? double(claim.pricing.submittedCost) * double(plan.costShare.coinsurancePct) / 100.0
    : double(plan.costShare.maxPerFill)
  actual: claim.pricing.coinsurance      # must be a declared claim input
```

Other outcome types: `info` (supporting fact such as tier placement, no token) and `override` (waives the rules that list it in `overridden_by`).

CEL conventions:
- Only declared inputs exist; missing values are omitted. Guard optional inputs with `has(...)`.
- An unguarded missing value makes the finding `unverified`, which is the right result when data is absent.
- Compare ISO dates as strings (`claim.dos > claim.member.coverageEnd`).
- Use `double()` when mixing integers and decimals.
- List membership is `x in list`; intersection is `list.exists(d, d in other)`.

Templates: `{{path}}`, `{{path|money}}`, `{{path|list}}`, `{{amount|money}}`. Only declared inputs and `amount` are allowed.

## Procedures: `knowledge/procedures/planner.yaml`

```yaml
procedures:
  explain_outcome:
    description: Explain why a record was rejected or approved.
    intents: [explain_outcome]
    route_from: [outcome_tokens, question_concepts]   # also: price_tokens
    speculative_agents: [eligibility, coverage]       # cheap agents always asked
    task_mode: explain                                # explain | what_if | none
    must_explain: outcome_tokens                      # outcome_tokens | price_tokens | none
    rules_modes: []                                   # [as_adjudicated, current] for compare
    loop: {max_rounds: 2, expand: [policy_graph, related_concepts]}
```

`domain.yaml` (the playbook) normally stays as in the reference. Per-agent `notes` appear in agent cards.

## Goldens: `goldens/goldens.yaml`

```yaml
goldens:
  - claim: RX-1007
    question: Why did this reject with plan limitations exceeded?
    expect: {primary: R-NET-001, confidence: confirmed, agents_in_round_2: [network]}
  - claim: RX-1008
    question: Why did this claim reject?
    expect: {primary: null, confidence: unexplained, gap: "reject:85"}
  - claim: RX-1012
    question: Would this claim still pay today?
    expect: {compare_differs: true, current_primary: R-PA-001}
```

Supported expectation keys: `primary`, `confidence`, `contains` (case-insensitive), `what_if`, `overridden`, `agents_in_round_2`, `gap`, `compare_differs`, `current_primary`.

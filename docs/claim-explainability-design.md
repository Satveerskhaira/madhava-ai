# Pharmacy Claim Explainability: Rule Graph + LLM via MCP

**Status:** Initial design (v0.1)
**Goal:** Let an LLM answer "why did this claim behave this way?" using evidence from claim data, plan setup data, and SME-approved business rules, without instrumenting the adjudication engine.

---

## 1. Problem

- Plan setup API returns ~12k fields per plan. Too large and too noisy to hand to an LLM.
- Claim outcomes (rejects, pricing, copay) are driven by business rules that read a small subset of claim + plan fields.
- The adjudication engine does not emit rule-level traces, and adding them is expensive.
- Outcomes change with plan setup, effective dates, overrides, and accumulator state, so answers must be grounded in the correct version of each.

## 2. Core Idea

Build the missing link **outside the engine**:

```
Claim fields  ->  Business Rule  ->  Plan setup fields
                       |
                       v
                    Outcome (reject code / pricing effect / copay logic)
```

- SMEs own the rules (with LLM-drafted first versions).
- Each rule declares which claim fields and plan fields it reads.
- A lightweight **shadow evaluator** checks rules against the claim + plan snapshot and reports which rule explains the outcome.
- The LLM only **narrates the evidence bundle**. It never infers rules itself.

## 3. Architecture

```
                 +----------------------+
 User question ->|  LLM (via MCP client)|
                 +----------+-----------+
                            | tool calls
                 +----------v-----------+
                 |     MCP Server       |
                 |  (shaping layer)     |
                 +--+------+------+-----+
                    |      |      |
        +-----------+  +---+---+  +-----------+
        |              |       |              |
 +------v-----+  +-----v----+ +v----------+ +-v------------+
 | Claim API  |  | Plan API | | Rule Store| | Field Catalog|
 | (outcome,  |  | (12k     | | (rule     | | (12k paths + |
 |  inputs)   |  |  fields, | |  cards +  | |  descriptions|
 |            |  |  versioned| |  edges)   | |  + groups)   |
 +------------+  +----------+ +-----------+ +--------------+
                       |
              +--------v---------+
              | Plan Snapshot    |  cached, effective-dated,
              | Cache            |  never sent raw to LLM
              +------------------+
```

### Components

| Component | Purpose |
|---|---|
| **Field Catalog** | Every claim and plan field path with type, description, decoded values, domain group. The grounding vocabulary for everything else. |
| **Rule Store** | Rule cards (YAML in Git, loaded to DB) plus edge tables linking rules to fields and outcomes. |
| **Plan Snapshot Cache** | Full plan payload cached by planId + version + as-of date. Never exposed to the LLM. |
| **Shadow Evaluator** | Evaluates rule conditions against claim + plan values in precedence order. |
| **MCP Server** | Exposes small, intent-level tools that return evidence bundles, not raw JSON. |

## 4. Rule Card Schema

One rule card per business rule. SME-owned, versioned in Git.

```yaml
id: R-PA-001
version: 3
status: approved            # draft | in_review | approved | deprecated
domain: prior_authorization   # rules are organized and released by domain
intent: Reject when the drug requires PA on this plan and no PA is on file
outcome:
  type: reject
  code: "75"
claim_inputs:
  - claim.ndc
  - claim.dos
  - claim.paNumber
plan_inputs:
  - formulary.priorAuth.required
  - formulary.priorAuth.effectiveDate
condition: >
  plan.formulary.priorAuth.required == true
  AND claim.paNumber IS EMPTY
  AND claim.dos >= plan.formulary.priorAuth.effectiveDate
order: 20                    # precedence within the family
overridden_by: [R-PA-OVR-002]
# effective dating is assigned at the domain rule-set level (see section 8.7)
source_refs:                 # where this rule was derived from
  - doc: "Plan Setup Guide v7", section: "4.2 Prior Auth"
  - doc: "Reject Code Table", row: "75"
confidence: medium           # LLM-drafted confidence before SME review
sme_owner: <name/team>
reviewed_by: <name>
reviewed_on: <date>
```

## 5. Runtime Flow (per question)

1. `get_claim_outcome(claimId)` returns status, reject codes, pricing summary, plus pinned plan id / version / DOS.
2. Map the outcome to its **domain(s)** (e.g., reject 75 to `prior_authorization`), load each domain's **rule set resolved for this claim** (default: the one in effect when the claim was adjudicated; see 8.7), and select candidate rules for that outcome, ordered by `order`.
3. Load the plan setup and other reference data **as of the date of service (DOS)** and the claim inputs.
4. Evaluate each candidate's `condition` in order.
5. Check `overridden_by` rules.
6. Build the **evidence bundle**: matched rule, values that triggered it, plan fields responsible, other rules that would also fire, overrides checked.
7. LLM narrates the bundle.

### Confidence labels

- **Confirmed:** condition matches the data and the rule's outcome agrees with the actual outcome.
- **Likely:** rule matches, but other rules could also explain it.
- **Unexplained:** no rule matches. Say so, log the gap for SMEs. The LLM must never guess.

### Evidence bundle example

```json
{
  "outcome": {"status": "Rejected", "code": "75", "meaning": "PA required"},
  "confidence": "confirmed",
  "explanation_rule": {
    "id": "R-PA-001", "version": 3,
    "intent": "Reject when drug requires PA and no PA on file"
  },
  "evidence": [
    {"source": "plan", "path": "formulary.priorAuth.required", "value": true, "asOf": "2026-09-01"},
    {"source": "claim", "path": "claim.paNumber", "value": null}
  ],
  "other_rules_checked": [{"id": "R-PA-OVR-002", "matched": false}],
  "drill_down": ["get_plan_section(formulary.priorAuth)"]
}
```

## 6. Generating Rule Cards from Existing Documents (Critical Section)

This is the make-or-break step. If we can reliably produce rule cards that are already linked to real claim and plan field paths, the rest of the system is straightforward evaluation and narration. The approach below is designed so the LLM does the drafting, but **deterministic checks and SMEs decide what gets trusted**.

### 6.1 Why this can work

- Rules already exist as **prose** in reject code tables, plan setup guides, BRDs, and config screens. We are converting them to structure, not inventing them.
- LLMs are good at extraction into a fixed schema when given (a) a narrow chunk of source text and (b) a closed vocabulary to choose from.
- The **field catalog is the closed vocabulary**. The LLM may only reference fields that exist in it. This is what makes linking to data tractable: the link is made at draft time, and can be machine-verified.
- SMEs review and correct instead of authoring from a blank page.

### 6.2 Source inventory

| Source | What it gives us | Typical extraction |
|---|---|---|
| Reject code tables (NCPDP + payer-specific) | Outcome codes, meaning, sometimes trigger text | One or more rules per code; the `outcome` block and intent |
| Plan setup guides / config manuals | How each setup section is meant to behave | Conditions and the plan fields they read |
| Business requirement docs (BRDs) | Intent, exceptions, precedence, overrides | `order`, `overridden_by`, edge cases |
| Adjudication config screens (screenshots, exports, field help text) | UI labels that map to setup fields | Label to API path mapping, decoded values |
| Existing test cases / UAT scripts | Given-when-then examples | Golden claims and rule validation |
| Historical claims (masked) | Real outcome + inputs | Backtesting (see Step 6 in 6.3) |

### 6.3 Pipeline

```
Sources -> Chunk by rule family -> Retrieve relevant catalog fields
        -> LLM drafts rule cards (structured output)
        -> Automated validation -> Data backtest -> SME review -> Approved
                    ^                                              |
                    +---------- gap loop (unexplained claims) -----+
```

**Step 0: Prerequisite. Build the field catalog first.**
Extract all field paths from the OpenAPI spec or sample payloads (claim and plan). Draft descriptions and domain groups with an LLM, then SME-review. Without this, rule linking has nothing to anchor to. Include:
- path, type, description, decoded values (code to label)
- domain group (eligibility, formulary, benefit/copay, pricing, limits, DUR, PA, accumulators, network)
- **UI label(s)** from config screens if known (used for mapping in 6.4)

**Step 1: Chunk sources by rule family, not by page.**
Organize by outcome family: PA rejects, quantity limits, refill-too-soon, eligibility, copay tiering, pricing/MAC, deductible/MOOP, DUR, etc. Each LLM call gets one narrow family, so context stays small and rules stay coherent.

**Step 2: Retrieve candidate fields for the chunk.**
For each chunk, pull the top-k relevant catalog fields (semantic search over descriptions and labels, filtered by domain group), roughly 30 to 100 fields. The LLM sees only these, not all 12k. This keeps prompts small and constrains linking.

**Step 3: LLM drafts rule cards with a strict prompt and structured output.**

Prompt requirements:
- Output must match the rule card JSON schema (use structured output / JSON schema mode).
- `claim_inputs` and `plan_inputs` must be chosen **only** from the provided field list. If a needed field is not in the list, put it in `missing_fields` with a description. Do not invent paths.
- Every rule must include `source_refs` quoting the doc and section it came from.
- If the source is ambiguous, set `confidence: low` and list the open question in `sme_questions`. Do not guess.
- Separate rules: one outcome or one condition per card. Precedence and overrides go in `order` / `overridden_by`, only if stated in the source.

Prompt skeleton:

```
You are converting pharmacy adjudication documentation into structured rule cards.

SOURCE TEXT:
<chunk with doc name + section>

ALLOWED FIELDS (use only these paths):
<list of path | type | description | decoded values>

REJECT / OUTCOME CODES IN SCOPE:
<code table rows>

Produce rule cards as JSON matching the schema below.
Rules:
1. Only reference fields from ALLOWED FIELDS. Unknown needs -> missing_fields.
2. Quote the supporting source text in source_refs.
3. Do not infer precedence or overrides that the source does not state.
4. Ambiguity -> confidence "low" + a specific sme_question.
```

**Step 4: Automated validation (deterministic, no LLM).**
Reject or flag any card that fails:
- Every path in `claim_inputs` / `plan_inputs` exists in the field catalog.
- Every path referenced in `condition` is declared in the inputs, and vice versa.
- `condition` parses in the chosen expression engine (JsonLogic / JSONata / SpEL).
- Referenced outcome code exists in the code table.
- Types are compatible (e.g., not comparing a date field to a boolean).
- Decoded values used in the condition are valid for that field.
- No duplicate or contradictory rules for the same outcome and order.

This step catches most hallucinated links before a human sees them.

**Step 5: Label-to-path mapping for config screens.**
Config screens speak in UI labels ("Prior Authorization Required: Y"), while the API speaks in paths. Build a mapping table:
1. Exact and fuzzy match UI label to catalog description / label.
2. Semantic search fallback (embed labels and descriptions).
3. LLM proposes the match from top-3 candidates; SME confirms.
4. Store confirmed mappings, so they are reusable across all rules.

Once the mapping exists, every future doc that mentions a screen label resolves to a real path automatically.

**Step 6: Data backtest (the strongest automatic signal).**
Use masked historical claims with known outcomes:
- For claims that rejected with code X, evaluate all draft rules for X against the claim + plan snapshot. **Does at least one rule fire?** Measure recall.
- For claims that paid, does any reject rule for code X fire? Those are **false positives** and mean the condition is too broad or an override is missing.
- Rules that never fire on any claim are suspect (wrong field, wrong condition, or rare rule).
- Rules that fire together on the same claim reveal missing precedence.

Report per rule: recall, false-positive rate, and example claim IDs for SMEs to inspect.

**Step 7: SME review workflow.**
- Review queue sorted by (low confidence first, then by claim volume for the outcome code).
- Each review item shows: source excerpt, drafted card, linked fields with descriptions, backtest results, and specific SME questions.
- SME actions: approve, edit, reject, or answer question. Edits are made in a form/YAML diff; approval merges via pull request.
- Target: SMEs correct roughly 20-30% of cards instead of writing 100%.

**Step 8: Gap loop.**
Claims classified `Unexplained` in production or testing feed back as tasks: "Outcome code 79 on claim X has no matching rule." SMEs or the LLM (with SME review) draft the missing rule. Coverage grows with use.

### 6.4 How this links to claim and plan data

The link is created and verified at draft time, then materialized as edges:

```
rule.claim_inputs -> edge(ClaimField -> Rule)
rule.plan_inputs  -> edge(Rule -> PlanField)
rule.outcome      -> edge(Rule -> Outcome)
rule.overridden_by-> edge(Rule -> Rule)
```

Because inputs must come from the catalog and pass validation, every edge points to a real field. At runtime, tools can traverse in both directions:
- Outcome to rules to plan fields ("what setup drives reject 75?")
- Plan field to rules to outcomes ("what claims could this setting affect?", useful for change impact analysis)

### 6.5 Handling the hard cases

| Challenge | Approach |
|---|---|
| Implicit rules not documented anywhere | Found through backtest recall gaps and the Unexplained loop; SME interviews for those families |
| Conflicting documents | Card carries all `source_refs`; validator flags conflicts; SME resolves and records the winning source |
| Stale documents | Backtest against recent claims exposes rules that no longer match behavior |
| Complex pricing (MAC tiers, COB, accumulators) | Start with descriptive drivers ("copay from tier 2 = $X") and decompose into smaller rules later; do not attempt full recalculation initially |
| Payer / plan-specific variants | Rule cards with scoping fields (`applies_to: plan_type / client`) or override cards |
| Multi-condition rules | Keep `condition` in a small expression language; split into multiple cards when the logic is naturally separable |
| LLM invents fields or logic | Closed vocabulary + validation + mandatory source quotes + SME approval gate |

### 6.6 Quality metrics

Track from day one:
- **Field-link validity:** % of draft cards passing catalog validation (target > 98%)
- **Backtest recall:** % of rejected claims explained by at least one approved rule
- **False-positive rate** per rule
- **SME edit rate:** % of cards changed during review (indicates prompt quality)
- **Unexplained rate** in production (target trending down)
- **Coverage:** % of claim volume by outcome code that has approved rules

### 6.7 Suggested pilot (2 to 3 weeks)

1. Pick 5 to 8 high-volume reject codes (e.g., 75 PA, 76 plan limits, 79 refill too soon, 70 not covered, 65 patient not covered) plus one copay path.
2. Build the field catalog slice for those families only.
3. Run the pipeline on the related docs; produce roughly 30 to 60 draft rule cards.
4. Backtest on a few hundred masked claims.
5. SME review sessions; measure edit rate and recall.
6. Decide go / no-go on scaling the approach to other families.

## 7. Data Model (MongoDB)

MongoDB is sufficient. The graph is shallow (1 to 3 hops), and edges are stored as arrays of field paths and rule IDs inside each rule document. No joins are needed. A dedicated graph DB is only warranted if deep multi-hop traversal or SME graph visualization becomes a real requirement; if so, sync a read-only copy to Neo4j rather than replacing Mongo. **Atlas Vector Search is available**, so field-catalog semantic search lives in the same database.

### Collections

| Collection | Purpose |
|---|---|
| `field_catalog` | Claim and plan field paths, types, descriptions, decoded values, domain group, UI labels, embedding (Atlas Vector Search) |
| `rules` | One document per rule **version** (immutable once published), tagged with its `domain` |
| `rule_sets` | Published, **effective-dated** releases per domain, pointing to specific rule versions. Never edited or deleted; this is the rule history |
| `outcome_domain_map` | Which domain(s) own each outcome code or pricing/copay question |
| `label_mappings` | Confirmed UI label to field path mappings |
| `field_aliases` | Renamed or moved field paths across catalog versions, so historical rules still resolve |
| `backtest_results` | Per-rule and per-domain-release backtest metrics |
| `explanation_log` | Audit of every explanation: rule sets, rule IDs, plan version, DOS, confidence |
| `config` | Runtime settings (size caps, thresholds, enabled domains, tool flags) |
| `rule_drafts` | *(Option B only)* UI-authored drafts, approvals, and comments |

### `field_catalog`

```json
{
  "_id": "plan.formulary.priorAuth.required",
  "source": "plan",
  "type": "boolean",
  "description": "Whether the drug requires prior authorization on this plan",
  "decoded_values": {"true": "PA required", "false": "No PA"},
  "domain_group": "prior_authorization",
  "ui_labels": ["Prior Authorization Required"],
  "embedding": [ ... ]
}
```

**Atlas Vector Search index** (used by `search_plan_fields` and label-to-path mapping):

```json
{
  "fields": [
    {"type": "vector", "path": "embedding", "numDimensions": 1024, "similarity": "cosine"},
    {"type": "filter", "path": "source"},
    {"type": "filter", "path": "domain_group"}
  ]
}
```

`numDimensions` must match the chosen embedding model. The `source` and `domain_group` filters let rule generation (section 6) retrieve only relevant fields per domain chunk. Embed the description + UI labels + decoded values text for best matching.

### `rules` (edges are embedded arrays)

```json
{
  "_id": "R-PA-001@3",
  "rule_id": "R-PA-001",
  "version": 3,
  "status": "approved",
  "domain": "prior_authorization",
  "intent": "Reject when drug requires PA and no PA on file",
  "outcome": {"type": "reject", "code": "75"},
  "claim_inputs": ["claim.paNumber", "claim.dos"],
  "plan_inputs": ["formulary.priorAuth.required", "formulary.priorAuth.effectiveDate"],
  "overridden_by": ["R-PA-OVR-002"],
  "order": 20,
  "condition": { "and": [ ... ] },
  "source_refs": [{"doc": "Plan Setup Guide v7", "section": "4.2", "excerpt": "..."}],
  "confidence": "medium",
  "sme_owner": "pharmacy-benefits-team",
  "git_commit": "a1b2c3d"
}
```

Plan setup values are **not** stored on the rule. The rule only names the paths; values are read from the plan snapshot as of DOS at evaluation time.

### `rule_sets` (domain-scoped, effective-dated releases)

```json
{
  "_id": "RS-PA-2026.10.1",
  "domain": "prior_authorization",
  "status": "active",
  "effective_from": "2026-10-01",
  "effective_to": null,
  "change_ref": "ENG-REL-4412",
  "reason": "Step therapy check moved ahead of PA-on-file check",
  "catalog_version": "cat-2026.09",
  "rules": [{"rule_id": "R-PA-001", "version": 3}, ...],
  "git_tag": "rules-pa-2026.10.1",
  "published_by": "release-pipeline",
  "published_at": "2026-09-29T14:00:00Z",
  "validation_report": {"field_link_validity": 0.99, "parse_errors": 0},
  "backtest_summary": {"recall": 0.91, "false_positive_rate": 0.02},
  "previous": "RS-PA-2026.09.2",
  "corrects": null,
  "superseded_by": null
}
```

- `effective_from` / `effective_to` are **business dates**: when this logic applied in the adjudication engine. They are not the publish date (`published_at`), which can be later, for example when history is backfilled.
- `status`: `candidate` (not yet live), `active` (currently in effect), `archived` (superseded, kept for history), `withdrawn` (published in error).
- Effective ranges must **not overlap** within a domain; the publish API enforces this.
- Published rule sets and rule versions are **immutable**. The only permitted change is closing `effective_to` on the previous set when a new one activates (audited).

### `outcome_domain_map`

```json
{"_id": "reject:75", "domains": ["prior_authorization"]}
{"_id": "question:copay", "domains": ["benefit_copay", "accumulators", "network"]}
```

Some outcomes (copay, pricing) span several domains. The evaluator loads the resolved rule set for each (section 8.7) and merges results in precedence order.

### `explanation_log`

```json
{
  "claim_id": "...",
  "dos": "2026-09-01",
  "adjudicated_at": "2026-09-03T10:15:00Z",
  "plan_version": "...",
  "rules_mode": "as_adjudicated",
  "rule_resolution_date": "2026-09-03",
  "rule_sets": ["RS-PA-2026.09.2"],
  "rules_changed_since_claim": true,
  "matched_rules": ["R-PA-001@2"],
  "confidence": "confirmed",
  "created_at": "..."
}
```

### Indexes

| Index | Serves |
|---|---|
| `rules: { "outcome.code": 1, "order": 1, "status": 1 }` | Outcome to candidate rules in precedence order (main runtime query) |
| `rules: { "domain": 1, "status": 1 }` | Domain browsing, review queues, release building |
| `rules: { "plan_inputs": 1 }` (multikey) | Plan field to rules (change-impact analysis) |
| `rules: { "claim_inputs": 1 }` (multikey) | Claim field to rules |
| `rule_sets: { "domain": 1, "effective_from": -1, "effective_to": 1 }` | Resolve the rule set in effect on a given date (current or historical) |
| `rule_sets: { "domain": 1, "status": 1 }` | Find the currently active set per domain |
| `label_mappings: { "ui_label": 1 }` | Label to path lookup |
| `explanation_log: { "claim_id": 1 }` | Audit lookup |
| `field_catalog`: Atlas Vector Search index above | Semantic field search |

### Traversal patterns

- **Outcome to domain to rules to plan fields:** `outcome_domain_map` lookup, then one indexed query on `outcome.code`, sorted by `order`.
- **Plan field to outcomes:** multikey query on `plan_inputs`, grouped by `outcome.code`.
- **Override chains:** `$graphLookup` on `overridden_by`.
- **Schema validation:** use a collection-level JSON Schema validator so malformed cards cannot be stored.

## 8. Rule Lifecycle and Publishing

### 8.1 Principle

**The runtime reads only published, versioned rule sets from MongoDB.** How rules are authored (Git or a UI) is a separate, swappable layer. Rule *content* changes only through review, validation, and release. Only operational settings are runtime-configurable.

### 8.2 Authoring options (both retained; decide details at implementation)

| | **Option A: Git-first** | **Option B: DB/UI-native** |
|---|---|---|
| Source of truth | Git repo (YAML cards) | MongoDB (`rule_drafts` + `rules`) |
| Editing | PRs; SMEs review rendered diffs or a simple review page, a technical partner commits | Form-based UI with draft / in-review / approved states |
| Validation | CI on every PR | Same validators run on save and on submit-for-approval |
| Audit / rollback | Git history, blame, tags | Must be built: version history, approvals, audit log |
| Backtest visibility | Report attached to PR | Shown inline in the UI |
| Effort | Low | High (UI, workflow, permissions) |
| SME friendliness | Low to medium | High |

**Decision: start with Option A (Git-first), move to a UI-based option later.** When the time comes, there are two routes, and the choice can be made during implementation:

- **B1: Thin UI over Git.** SMEs use forms; the backend creates branches and PRs. Keeps Git's audit and CI.
- **B2: DB-native authoring.** Git demoted to export/backup; approvals and audit live in the app.

**Keep the switch cheap.** These invariants make moving from A to B a change to the authoring layer only:

- One rule card schema, shared by both options
- One validation library, used by CI (A) and by UI save/submit (B)
- Immutable rule versions
- One publish API that both options call to create domain rule sets
- Runtime (evaluator + MCP) unaffected, because it only reads `rule_sets` and `rules`

### 8.3 Domain-driven organization

Rules are organized, owned, and released **by domain** (for example: prior authorization, quantity limits / refill-too-soon, eligibility, formulary, benefit and copay, accumulators, pricing, DUR, network).

- Each domain has a **named SME owner** (CODEOWNERS in Git, role permissions in a future UI).
- Each domain has its **own rule set versions and release cadence**. A PA rule fix does not require re-releasing pricing.
- Validation, backtest, and golden claims run **per domain**, plus a periodic cross-domain regression run.
- Outcomes that span domains (copay, pricing) are handled through `outcome_domain_map`; the evaluator loads each involved domain's resolved rule set and merges by precedence.
- Cross-domain dependencies (a rule in one domain reading a result from another) should be avoided; if unavoidable, declare them explicitly and validate in the cross-domain run.

### 8.4 Option A details (Git-first)

**Repository layout**

```
rules-repo/
  catalog/                   # field catalog exports + descriptions
  label_mappings/            # confirmed UI label -> path mappings
  domains/
    prior_authorization/
      R-PA-001.yaml
      R-PA-OVR-002.yaml
      domain.yaml            # owner, outcome codes in scope
    refill_too_soon/
    benefit_copay/
    accumulators/
    pricing/
  goldens/                   # SME-verified golden claims (masked), per domain
  schema/rule-card.schema.json
  CODEOWNERS                 # SME owner per domain directory
```

**Rule states**

```
draft  ->  in_review  ->  approved  ->  deprecated
   ^           |
   +-----------+  (changes requested)
```

- `draft`: LLM-generated or in-progress. Never visible to the evaluator.
- `in_review`: PR open; validation and backtest results attached.
- `approved`: merged and eligible for a domain release.
- `deprecated`: retained for archived rule sets; excluded from new releases.

**Change workflow**

1. LLM-drafted card or SME edit lands on a branch.
2. **CI gates (must pass to merge):**
   - Schema validation of the card
   - Every claim and plan path exists in the field catalog
   - Condition parses and only references declared inputs
   - Outcome code exists in the code table
   - No conflicting rule at the same outcome and order within the domain
   - **Golden claims** for the domain pass (no regression)
   - **Backtest** on masked historical claims; recall and false-positive deltas reported on the PR
3. CODEOWNERS routes review to the domain's SME owner. Approval required.
4. Merge marks the card `approved`.

### 8.5 Option B details (UI-based, later)

- Form authoring that pulls field pickers from `field_catalog` (semantic search via Atlas Vector Search), so linking to real paths is guided.
- LLM-assisted drafting from pasted source text, using the same pipeline as section 6.
- Draft, in-review, approved workflow with domain-based permissions (SME owner approves).
- Inline validation, backtest results, and "what changed" diffs.
- Full audit trail and version history in MongoDB (`rule_drafts`, approvals, comments).
- Same publish API as Option A (section 8.6).
- **B1 variant:** the UI writes to Git branches/PRs instead of `rule_drafts`, so CI and Git audit stay in place.

### 8.6 Publishing a domain rule set (effective-dated)

Every release carries a manifest (a file in Git for Option A, a publish form in Option B):

```yaml
domain: prior_authorization
effective_from: 2026-10-01     # business date the logic went live in the engine
change_ref: ENG-REL-4412       # engine release / change ticket
reason: Step therapy check moved ahead of PA-on-file check
```

1. **Trigger:** release tag for a domain (e.g., `rules-pa-2026.10.1`) or the UI publish action.
2. The publish API builds the rule set: exact `(rule_id, version)` pairs, `catalog_version`, validation and backtest summaries.
3. It writes new rule versions to `rules` (immutable) and a `rule_sets` document with `status: candidate`.
4. **Date checks:** no overlap with existing ranges in the domain; `effective_from` must not precede the current active set's, unless the release is flagged `backfill` or `correction` (see 8.7 and 8.8).
5. **Smoke test:** run the domain's golden claims through the real evaluator, **including boundary claims** adjudicated just before and just after `effective_from`.
6. **Activate:** the new set becomes `active` with `effective_to: null`; the previous set's `effective_to` is closed at the new `effective_from` and it becomes `archived` (never deleted).

Publishing is a **whole-domain-release** operation, never rule by rule.

### 8.7 Historical rules and rule-mode evaluation

**Principle:** every rule set ever published is kept. Old claims are explained with the logic that was in force when they were adjudicated, **automatically**, with no user action. Users can also ask for other views (current rules, a specific date, a comparison).

**Default behavior (automatic)**

When a claim is explained, the resolver picks, for each involved domain, the rule set whose effective range contains the claim's **rule resolution date**. That date is the claim's **adjudication date** by default (configurable to DOS; see open questions), because that is when the engine applied its logic.

**Rule modes (tool parameter)**

| Mode | Parameter | Typical question |
|---|---|---|
| **As adjudicated** (default) | `rules_mode="as_adjudicated"` | "Why did this claim reject?" |
| **Current** | `rules_mode="current"` | "Would this claim reject today?" |
| **As of a date** | `rules_mode="as_of", as_of="2026-01-15"` | "How would this have processed under January's rules?" |
| **Specific version** | `rules_mode="version", rule_set_id="RS-PA-2026.10.1"` | Audit, or testing a `candidate` set before release |
| **Compare** | `compare_rules_over_time(claimId)` | "Did the rules change? Would the outcome differ now?" |

Three related tools support this: `list_rule_history` (timeline of versions with effective dates and reasons), `diff_rule_sets` (what changed between two dates, in business language), and `compare_rules_over_time`. See section 9.

**What varies by mode and what does not**

| Data | Resolved by | Changes with `rules_mode`? |
|---|---|---|
| Rules | Rule set per domain (by mode) | **Yes** |
| Plan setup and reference data | **DOS** | No |
| Claim inputs | The claim record | No |

Plan overrides for what-if analysis remain a separate concern (`simulate_with_override`) and can be combined with any mode.

**Automatic notice in every bundle**

The evidence bundle always states which rules were used, and flags when rules have changed since the claim, so the LLM can offer a comparison without an extra call:

```json
"rules_context": {
  "mode": "as_adjudicated",
  "resolution_date": "2026-09-03",
  "rule_sets": [{"domain": "prior_authorization", "id": "RS-PA-2026.09.2",
                 "effective": "2026-07-01 to 2026-10-01"}],
  "changed_since_claim": true,
  "changes": ["prior_authorization changed 2026-10-01 (RS-PA-2026.10.1)"],
  "hint": "compare_rules_over_time(claimId) shows whether current rules would differ"
}
```

**Compare output (shape)**

```json
{
  "as_adjudicated": {"rule_set": "RS-PA-2026.09.2", "matched": "R-PA-001@2", "outcome": "Reject 75"},
  "current":        {"rule_set": "RS-PA-2026.10.1", "matched": "R-STEP-004@1", "outcome": "Reject 76"},
  "differs": true,
  "changed_rules": [{"id": "R-STEP-004", "change": "added", "effective": "2026-10-01",
                     "reason": "Step therapy check moved ahead of PA-on-file check"}]
}
```

**Edge cases**

- **Claim older than recorded history** (before the earliest rule set): the resolver falls back to the earliest set, caps confidence at `likely`, and states "claim predates recorded rule history". Alternatively it returns `unexplained`; choose per SME preference.
- **Gaps in history:** no set contains the date, so the result is `unexplained` with a clear note. A nightly check reports gaps and overlaps per domain.
- **Reprocessed or reversed claims:** use the adjudication timestamp of the specific transaction being explained.
- **Field renames across plan API versions:** `field_catalog` entries are versioned, each rule set pins a `catalog_version`, and `field_aliases` map old paths to new ones so the evaluator reads the correct plan fields for the period.

**Backfilling history (one-time and ongoing)**

- Author rule versions for past periods using the same pipeline as section 6, from historical docs, config change logs, and engine release/change records.
- Take `effective_from` dates from engine release or change-management records, not from when we authored the rules.
- Publish with the `backfill` flag (allows past effective dates), then backtest each period against claims adjudicated in that period.
- Do not backfill everything: prioritize high-volume domains and the claim age range users actually ask about (for example, audit or dispute windows).
- Going forward, every engine logic change needs a matching effective-dated release. Tie this to the engine's change process so it cannot be skipped.

**Pre-release impact testing (bonus)**

`rules_mode="version"` can evaluate a `candidate` set against historical claims, showing which past outcomes would change under a proposed rule before it goes live.

### 8.8 Rollback and corrections

Two different situations:

- **Bad publish (a set just released is wrong):** mark it `withdrawn`, reopen the previous set (`effective_to: null`, `active`). No deployment or data rewrite. Explanations already logged against the withdrawn set stay traceable via `explanation_log`.
- **Historical error (an old set was wrong):** publish a corrected set with the **same effective range**, flagged `correction`, with `corrects: <old set id>`. The old set gets `superseded_by`. The resolver always prefers the non-superseded set. Past explanations remain in the log for audit, and affected ones can be identified by rule set id and re-run.

### 8.9 Runtime-configurable (without a release)

Kept in `config`, changeable with audit logging:

- Response size caps and token budgets
- Confidence thresholds and labeling behavior
- Which domains are enabled for answering
- Feature flags per MCP tool
- Default `rules_mode` and `rule_resolution_date` source (adjudication date or DOS)
- `outcome_domain_map` entries (reviewed, logged)
- Label-to-path mappings, if SME hot-fixes are allowed (logged and reconciled back to the source of truth)

**Not runtime-configurable:** rule conditions, outcomes, precedence, and overrides. These always go through review, validation, and release.

### 8.10 Evolution path

| Stage | Authoring experience |
|---|---|
| **Phase 1 (now)** | Option A: Git + PRs, per-domain CODEOWNERS, CI validation and backtest |
| **Phase 2** | Option B1: thin SME UI creating PRs behind the scenes, inline backtest results |
| **Phase 3 (optional)** | Option B2: DB-native authoring, only if SME volume justifies building approvals and audit in-house |

The choice between B1 and B2 is deferred to implementation time, based on SME adoption and effort.

## 9. MCP Tools

| Tool | Description (model-facing) |
|---|---|
| `get_claim_outcome(claimId)` | Returns claim status, reject codes, pricing summary, and pinned plan/version/DOS. Start here for any claim question. |
| `explain_outcome(claimId, code?, rules_mode?, as_of?, rule_set_id?)` | Runs the shadow evaluator. Default mode `as_adjudicated` uses the rules in force when the claim was processed. Returns confidence, matched rule, evidence values, other rules checked, and a `rules_context` notice. Use for "why" questions. |
| `get_rule(ruleId)` | Returns business intent, condition, fields read, precedence, and overrides. |
| `get_plan_section(planId, section, asOfDate)` | Returns a size-capped, normalized plan section for drill-down. |
| `search_plan_fields(query)` | Semantic search over the field catalog when no rule covers the question. |
| `simulate_with_override(claimId, planOverrides)` | Re-runs the evaluator with changed plan values (reject / eligibility logic only). |
| `diff_plan_versions(planId, dateA, dateB)` | Shows setup changes that could explain behavior changes. |
| `compare_rules_over_time(claimId)` | Evaluates the claim under the rules as adjudicated and the current rules. Reports whether the matched rule or outcome differs and which rule changes caused it. Use for "would this still reject today?" |
| `list_rule_history(domain or ruleId)` | Timeline of rule set versions with effective dates, reasons, and change references. |
| `diff_rule_sets(domain, fromDate, toDate)` | Rules added, changed, or removed between two dates, with business intent. Use for "what changed in PA rules this quarter?" |

Design rules: each response capped at ~2 to 4k tokens; nulls and defaults stripped; codes decoded; PHI minimized; every response includes `drill_down` hints.

## 10. Guardrails for the LLM

- Every statement in an answer must cite a returned rule ID and field path + value.
- If confidence is `unexplained`, say so; do not speculate.
- Always state the plan version and as-of date used, plus the rules mode, rule set IDs, and their effective dates.
- Explain claims with the rules in force at adjudication unless the user asks otherwise. If `changed_since_claim` is true, mention it once and offer a comparison.
- Never present a current-rules result as what happened to the claim. Label comparisons clearly: "what happened" vs. "what would happen under current rules".
- Never present "likely" as "confirmed".
- Mask PHI; return only the member fields required for the explanation.

## 11. Governance

- Rule cards live in Git; changes through pull requests with SME approval (see section 8), organized and owned per domain. Runtime reads published domain rule sets from MongoDB only.
- Each explanation logs rule set + plan version for auditability.
- Golden claim set (50 to 100 SME-verified) re-run on every rule, catalog, or plan schema change.
- Named SME owner per rule family; periodic review cadence.
- Catalog regeneration when the plan API schema changes, with diff review.

## 12. Phased Rollout

| Phase | Scope | Exit criteria |
|---|---|---|
| 0 | Field catalog for pilot families (versioned); label mapping; effective-dated rule set schema | Catalog reviewed by SMEs |
| 1 | Pilot rule generation (section 6.7) + backtest | Recall and edit-rate targets met |
| 2 | Shadow evaluator with rule set resolution (default as adjudicated) + MCP server (read-only tools) | Golden set passes; Unexplained rate measured |
| 3 | Expand to more reject codes; copay/pricing descriptive drivers; backfill rule history for prioritized domains | Coverage of ~80% of reject volume; history backtested per period |
| 4 | `simulate_with_override`, `diff_plan_versions`, `compare_rules_over_time`, `diff_rule_sets`, gap loop automation | SME workflow adopted |

## 13. Risks and Open Questions

**Risks**
- Undocumented rules cap achievable recall; mitigated by backtest and SME interviews.
- Shadow model can diverge from real engine behavior; mitigated by confidence labels and continuous backtesting.
- SME availability is the bottleneck; mitigated by prioritized review queue and small pilot.

**Resolved decisions**
1. **Authoring:** Git first, UI later. Both options stay documented (section 8.2); B1 vs B2 decided during implementation.
2. **Rules are domain-driven:** organized, owned, versioned, and released per domain.
3. **Plan setup and other reference data are DOS-driven:** resolved as of date of service.
4. **Atlas Vector Search is available:** the field catalog and semantic search live in MongoDB.
5. **Rule history is retained and resolved automatically:** old claims are explained with the rules in force when adjudicated. Users can request current rules, a specific date or version, or a comparison (section 8.7).

**Open questions**
1. Does the plan API support as-of-DOS queries natively, or must we reconstruct from versions and effective dates?
2. Do claims store the plan version used at adjudication (useful to cross-check DOS resolution)?
3. Is a masked historical claim set available for backtesting?
4. Which documents are authoritative when they conflict?
5. Preferred language for the MCP server and evaluator (Java/Spring vs. Python/TypeScript)?
6. Which expression engine is acceptable (JsonLogic, JSONata, SpEL)?
7. Which SME team owns each domain, and do they work in Git today?
8. How should outcomes spanning multiple domains (copay, pricing) be split, and who owns the merged view?
9. Do engine rule changes take effect by adjudication date or DOS? (Design default: adjudication date, configurable.)
10. Which embedding model and dimension for the Atlas vector index?
11. How far back does reliable rule-change history go (the baseline date), and how should claims older than that be answered (`likely` with a note, or `unexplained`)?
12. Can engine release / change-management records supply exact `effective_from` dates for backfill?

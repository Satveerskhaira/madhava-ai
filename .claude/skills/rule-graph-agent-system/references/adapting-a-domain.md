# Adapting the reference to a new domain

Most of a new domain is knowledge (`knowledge-schemas.md`). The code below holds the remaining domain assumptions. Paths are relative to `src/<pkg>/`. The package keeps the word "claim" in names; a different record type can keep those names or rename them consistently.

| File and symbol | Pharmacy assumption | What to change |
|---|---|---|
| `tools/apis.py` `HttpClaimsApi`, `HttpBenefitSetupApi` | `GET /claims/{id}`, `GET /plans/{id}/setup?asOf=` | Your endpoints, auth and response mapping; return the record as JSON whose keys match `claim.*` catalog paths |
| `tools/apis.py` `FixtureBenefitSetupApi` | Versions with `setup` and `changes`, padded to ~12k fields | Keep it for tests; set `synthetic_padding` to your real payload size |
| `tools/toolbox.py` `get_claim_outcome` | `rejectCodes`, `status`, `planId`, `dos`, `adjudicatedAt`; derived `daysSinceEnrollment` and `daysSinceLastFill`; price tokens from `pricing.copay`, `coinsurance` and `deductibleApplied` | Your outcome fields, the derived fields your rules need, and the `_PRICE_FIELDS` map (component to record field) |
| `tools/toolbox.py` `rules_date` | Adjudication date by default, or DOS | Your decision date field |
| `knowledge.py` `describe_token` | "reject 75 (meaning)", "copay amount" | Wording for your token kinds |
| `agents/language.py` `_PATTERNS` | Words such as copay, deductible, "if approved" | Your vocabulary for cost, what-if, compare and definition questions |
| `agents/planner.py` `understand` | rejected → explain outcome; paid → explain cost | Your status values and defaults |
| `agents/planner.py` `_pricing_only` | Cost-share agent detected by `price:` notations | Usually unchanged |
| `agents/synthesis.py` wording | "The member paid ...", "Primary reason for reject ..." | Your answer wording; keep citations, gaps and the rules context |
| `tools/mcp_server.py` instructions | Claim wording | Your tool descriptions |

Keep stable: `models.py` (wire contracts), `rules/` (store, graph, evaluator, validate), `memory/`, `agents/domain.py`, `agents/registry.py`, `adk/`. Change them only to fix a general issue, and add a test when you do.

## Worked mapping: medical claim denials (X12 835)

| Pharmacy reference | Medical equivalent |
|---|---|
| Reject code (NCPDP 511-FB) | CARC (group + code) and RARC on the 835 remittance, as tokens such as `denial:CO-197` and `remark:<RARC>`; take meanings from the published code lists |
| Plan setup API | Benefit configuration and fee schedule API, versioned by date of service |
| Agents: eligibility, coverage, UM, DUR, network, COB, cost share | Eligibility, coverage, authorization and medical necessity, coding edits (NCCI/MUE), provider network, COB, member cost share |
| `price:copay`, `coinsurance`, `deductible` | Patient responsibility CARCs (PR-1 deductible, PR-2 coinsurance, PR-3 copay) as price tokens |
| Rule domains | Authorization, bundling, frequency limits, place of service, timely filing, ... |

Steps: write the taxonomy with CARC notations, the ontology aligned to X12 835/837 and FHIR ExplanationOfBenefit, catalog paths for claim lines (the walk supports nested record JSON; add line-level derived fields in `get_claim_outcome` or treat each line as a record), then rule cards from payer policies and goldens per CARC.

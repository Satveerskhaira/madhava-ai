# madhava

Design for a pharmacy claim explainability layer: an LLM answers "why did this claim behave this way?" using evidence from claim data, plan setup data (resolved as of date of service), and SME-approved, effective-dated business rules, exposed through an MCP server.

## Contents

- [`docs/claim-explainability-design.md`](docs/claim-explainability-design.md): initial design (v0.1)
  - Rule graph: claim fields, rules, plan fields
  - LLM-assisted rule card generation from existing docs
  - MongoDB data model with Atlas Vector Search
  - Git-first rule lifecycle, with a UI option later
  - Historical rule sets and rule-mode evaluation
  - MCP tools, guardrails, rollout, open questions

## Status

Draft. Open questions are listed at the end of the design doc.

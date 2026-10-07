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
- [`docs/madhava-architecture.html`](docs/madhava-architecture.html): interactive architecture page (open in a browser)
  - How it explains: layers, domain agents over A2A, rule cards linked to plan data, trust controls, scaling
  - End-to-end flow: step-through diagrams for a member question, a rule change and a batch audit
  - Backend technology: Python and Google ADK for the orchestrator and agents, Java and Spring Boot for MCP tool servers, where model calls happen

## Status

Draft. Open questions are listed at the end of the design doc.

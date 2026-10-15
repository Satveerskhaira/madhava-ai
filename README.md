# madhava

A pharmacy claim explainability layer: agents answer "why did this claim behave this way?" using evidence from claim data, plan setup data (resolved as of date of service), and SME-approved, effective-dated business rules.

This repository holds the **core agent**. Since v0.2 the system is split three ways:

| Part | Where | What it is |
|---|---|---|
| Kernel (`rulegraph`) | [`kernel/`](kernel/) (moves to its own repo later) | SDK-neutral planner, investigators, memory, rule graph, CEL evaluator, toolbox; ADK and MCP adapters as extras; `rulegraph new` scaffolding |
| Pharmacy base (`pharmacy-base`) | [`base/`](base/) (moves later) | Base pack shared by every domain (ontology, reject codes, top concepts with adjudication order, shared fields, procedures), claim adapter, toolbox, fixtures, `pharmacy-domains` CLI |
| Domain agents (`pharmacy-agents`) | [madhava-ai-subagent](https://github.com/Satveerskhaira/madhava-ai-subagent) | Seven fragments, one per domain, each with its own entry point, goldens and tests |
| Core agent (`madhava`) | this repo, `src/madhava/` | Core planner procedures, cross-domain goldens, external interfaces (A2A, MCP, ADK workflow) |

Each domain agent ships as a knowledge fragment that extends the base and registers itself under the `rulegraph.domains` entry point; the core composes the base with every installed domain package. The core plans across domain agents, which run in process and share its context and memory (no A2A between core and domain agents). Each domain agent plans within its taxonomy branch, can answer directly, and reports findings and gaps to the core. The core checks every observed outcome is explained, runs one gap round if not, and answers with rule citations, stating any gap instead of guessing. Only the core is exposed over A2A.

## Quick start

```bash
pip install -r requirements-dev.txt   # kernel from ./kernel, domain agents from their repo, then this package
madhava validate
madhava --today 2026-10-14 eval       # 13/13 golden claims
madhava ask RX-1001 "Why was this rejected, and what would it cost if approved?"
madhava ask RX-1007 "Why did this reject?" --trace      # domain gap -> core gap round -> network agent
madhava ask RX-1001 "Why was this rejected?" --workflow # core as an ADK workflow graph
madhava serve --port 8100             # core over A2A
madhava mcp                           # toolbox + core over MCP (stdio)
pytest                                # core 24 tests; kernel tests: pytest kernel/tests
```

Ask a single domain agent: `pharmacy-domains ask utilization_management RX-1001 "Why was this rejected?"`.

## Layout

| Path | What it is |
|---|---|
| `src/madhava/system.py` | Builds the core planner over the in-process pharmacy domain agents |
| `src/madhava/procedures/planner.yaml` | Core planner procedures (procedural memory) |
| `src/madhava/cli.py` | `ask`, `eval`, `validate`, `cards`, `serve` (A2A), `mcp`, `gaps` |
| `goldens/goldens.yaml` | Cross-domain golden claims |
| `kernel/` | The `rulegraph` kernel package (see its README) |
| `base/` | The `pharmacy-base` package (see its README) |
| `.claude/skills/rule-graph-agent-system/` | Claude skill for building core and domain agent projects on the kernel |

## Docs

- [`docs/agent-system.md`](docs/agent-system.md): where the code lives, the three planner levels, knowledge model, memory types, graph search, nested loops with real traces, standards, scaling
- [`docs/claim-explainability-design.md`](docs/claim-explainability-design.md): initial design (v0.1)
- [`docs/madhava-architecture.html`](docs/madhava-architecture.html): interactive architecture page (open in a browser): layers, step-through flows, backend technology and agent layers. Domain agents run in process inside the core agent; only the core is exposed over A2A.

## Status

Reference implementation over synthetic data. Real claims and benefit setup APIs plug in through `PHARMACY_CLAIMS_URL`, `PHARMACY_BENEFITS_URL` and `PHARMACY_API_TOKEN` (base package). Set `MADHAVA_EPISODIC_DIR` to keep episodic memory and the gap log on disk.

# madhava

A pharmacy claim explainability layer: agents answer "why did this claim behave this way?" using evidence from claim data, plan setup data (resolved as of date of service), and SME-approved, effective-dated business rules.

This repository holds the **core agent**. Since v0.2 the system is split three ways:

| Part | Where | What it is |
|---|---|---|
| Kernel (`rulegraph`) | [`kernel/`](kernel/) (moves to its own repo later) | SDK-neutral planner, investigators, memory, rule graph, CEL evaluator, toolbox; ADK and MCP adapters as extras; `rulegraph new` scaffolding |
| Domain agents (`pharmacy-agents`) | [madhava-ai-subagent](https://github.com/Satveerskhaira/madhava-ai-subagent) | Pharmacy knowledge pack, claim record adapter, fixtures; seven domain agents, each a planner over its rule-domain investigators |
| Core agent (`madhava`) | this repo, `src/madhava/` | Core planner procedures, cross-domain goldens, external interfaces (A2A, MCP, ADK workflow) |

The core plans across domain agents, which are imported in process (no A2A between core and domain agents). Each domain agent plans within its taxonomy branch, can answer directly, and reports findings and gaps to the core. The core checks every observed outcome is explained, runs one gap round if not, and answers with rule citations, stating any gap instead of guessing. Only the core is exposed over A2A.

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

Ask a single domain agent: `pharmacy-agents ask utilization_management RX-1001 "Why was this rejected?"`.

## Layout

| Path | What it is |
|---|---|
| `src/madhava/system.py` | Builds the core planner over the in-process pharmacy domain agents |
| `src/madhava/procedures/planner.yaml` | Core planner procedures (procedural memory) |
| `src/madhava/cli.py` | `ask`, `eval`, `validate`, `cards`, `serve` (A2A), `mcp`, `gaps` |
| `goldens/goldens.yaml` | Cross-domain golden claims |
| `kernel/` | The `rulegraph` kernel package (see its README) |
| `.claude/skills/rule-graph-agent-system/` | Claude skill for building core and domain agent projects on the kernel |

## Docs

- [`docs/agent-system.md`](docs/agent-system.md): layers, knowledge model, memory types, graph search, loops, standards, scaling (see its v0.2 note)
- [`docs/claim-explainability-design.md`](docs/claim-explainability-design.md): initial design (v0.1)
- [`docs/madhava-architecture.html`](docs/madhava-architecture.html): architecture page. It predates v0.2 and still shows domain agents over A2A; now only the core is served over A2A.

## Status

Reference implementation over synthetic data. Real claims and benefit setup APIs plug in through `PHARMACY_CLAIMS_URL`, `PHARMACY_BENEFITS_URL` and `PHARMACY_API_TOKEN` (domain agents package). Set `MADHAVA_EPISODIC_DIR` to keep episodic memory and the gap log on disk.

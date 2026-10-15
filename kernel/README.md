# rulegraph

An SDK-neutral kernel for agents that explain why a record was decided the way it was (claims, orders, payments, credit, eligibility, pricing), from versioned rule cards, a rules/policy graph and source data, with cited evidence and no guessing.

- **Planner**: understand -> plan -> act (concurrent fan-out) -> verify (coverage check) -> bounded gap round -> synthesize -> remember. The same class is the core agent (executors = domain agents) and each domain agent (executors = rule-domain investigators, scoped to one taxonomy branch). A planner is also an executor.
- **Investigator**: fixed playbook, no model: recall rules from the policy graph, read only the needed fields, evaluate CEL, apply overrides, widen once, phrase.
- **Knowledge pack** (YAML): SKOS taxonomy, ontology, field catalog with JSON Pointers, codes, effective-dated rule domains and rule cards, procedures, wording.
- **Memory**: working (blackboard), episodic (sessions, gap log), semantic, procedural, taxonomic.
- **Models are optional**: deterministic understanding by default; `ModelLanguage` takes any `async complete(system, user) -> str`.
- **Adapters** (extras only): `rulegraph[adk]` (Google ADK workflow graph, A2A server, ADK model completion), `rulegraph[mcp]` (MCP server). No SDK is imported by the core modules.

```bash
pip install "./kernel[adk,mcp,dev]"            # or a git URL with #subdirectory=kernel
rulegraph new subagents --dest ../my-agents --package my_agents
rulegraph new core --dest ../my-core --package my_core --agents-package my_agents
rulegraph validate path/to/pack
rulegraph export taxonomy path/to/pack           # SKOS JSON-LD (or: ontology -> OWL JSON-LD)
pytest kernel/tests
```

```python
from rulegraph import Knowledge, Settings, Toolbox, domain_agents, core_planner

kn = Knowledge.load(pack_dir, planner_procedures=core_procedures_yaml)
toolbox = Toolbox(kn, record_api, reference_api, record_adapter, Settings())
agents = domain_agents(kn, toolbox)                 # {name: Planner}
core = core_planner(kn, toolbox, agents)
exp = await core.ask("Why was this rejected?", "REC-1")
```

The Claude skill `rule-graph-agent-system` (in `.claude/skills/`) walks through building core and domain agent projects on this kernel, and adapters for other agent SDKs.

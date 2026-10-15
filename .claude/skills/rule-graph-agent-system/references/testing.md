# Testing

## Goldens

Domain agents repo `goldens/domain_goldens.yaml` (each entry has `agent`); core repo `goldens/goldens.yaml`. Replayed by `<dist> eval` and by pytest via `rulegraph.evaluation.check(explanation, expect)`.

`expect` keys:
- `primary`: rule id the answer leads with (`null` when nothing explains it)
- `confidence`: confirmed | likely | what_if | supporting | unexplained
- `contains`: phrases the answer must include (amounts, versions)
- `what_if`: rule id expected in what-if findings
- `overridden`: rule ids that must be reported as overridden
- `agents_in_round_2`: agents the core's gap round must add
- `gap`: token that must be reported unexplained
- `out_of_scope`: token a domain agent must report as outside its scope
- `compare_differs`, `current_primary`: for compare-current questions

## Tests every project keeps

Domain agents repo:
- pack validates (`validate(knowledge) == []`)
- every domain golden
- a delegated task (`agent.handle(DomainTask(...))`) returns the expected finding and no gaps
- a delegated token outside scope comes back as a gap, not a guess
- reference payload slicing (catalog reads a few leaves of a large payload)
- if an SDK adapter is used, one test through it

Core repo:
- every core golden
- domain agents are in-process planners (no transport between core and domain agents)
- a gap round that follows a domain agent's own gap (check the trace)
- an unexplained outcome is logged to episodic memory, not guessed
- current rules mode, session follow-up reuse, a slow agent becoming an error report
- the exposed interface (A2A card and message, MCP tools, workflow graph routes)

Pin `Settings(today=...)` in tests so current-rules answers do not drift.

# Architecture

## Levels

| Level | What it is | Executors | Scope | Procedures file |
|---|---|---|---|---|
| Core | `core_planner(kn, toolbox, agents)` | domain agents | whole taxonomy | core repo `procedures/planner.yaml` |
| Domain agent | `domain_agent(kn, toolbox, name)` | investigators, one per rule domain in its branch | one top concept and its descendants | pack `procedures/domain_planner.yaml` |
| Investigator | `Investigator(rule_domain, ...)` | none (fixed playbook) | concepts of one rule domain | pack `procedures/investigator.yaml` |

All planners share one class. Differences come from data: executors, `unit_for` (core: concept -> agent; domain: concept -> rule domain), `scope`, procedures and the synthesis label.

`Executor` protocol: `name: str` and `async handle(task: DomainTask) -> DomainReport`. Planners and investigators both implement it, so any executor can be swapped for a remote client, a mock, or an SDK-wrapped agent.

## The planner loop

```
observe     fetch record outcome once (status, outcome tokens, amount tokens, dates, reference id)
understand  intents + concepts from the question (DeterministicLanguage regex/labels, or ModelLanguage)
plan        pick procedures by intent; route seeds (outcome tokens, amount tokens, question concepts,
            task tokens/concepts/hints) to units; add speculative units; one task per unit x mode x rules_mode
act         fan out tasks concurrently with a per-task deadline (Settings.agent_deadline_s); late = error report
verify      coverage check: every must_explain token needs a fired explaining finding -> "done" or "gap"
reflect     (gap) expand strategies in order, bounded by loop.max_rounds:
              policy_graph      graph search from the outcome token to rules, hint the owning unit
              related_concepts  follow skos:related from the token's concept
              sweep_scope       (domain agents) run every investigator in scope
synthesize  precedence-ordered primary reason, supporting facts, overrides, what-if, compare, gaps,
            rules and reference context, citations; wording from pack phrases
remember    episodic: episode + unexplained gaps (expert backlog)
```

Routing a token: taxonomy `classify` first (notation match); then, within the planner's scope, the policy graph. Hints from the core (rule ids found by the core's graph search) route straight to the unit owning that rule. A domain agent that receives a token outside its scope reports it as `out_of_scope` instead of guessing.

A domain agent's report to the core carries findings, `gaps` (tokens it was asked to explain and could not), `rounds` and its own trace. The core treats gaps as unexplained and runs its own gap round, so the two loops nest and each stays bounded.

## Memory types

| Memory | Kernel module | Holds |
|---|---|---|
| Working | `memory/working.py` Blackboard | one question: plan, tasks, reports, findings (deduplicated by rule key, mode, rules mode) |
| Episodic | `memory/episodic.py` | past episodes (session follow-ups reuse reports), unexplained gaps (JSONL when `episodic_dir` set) |
| Semantic | `memory/semantic.py` | definitions, codes, fields, rules as documents; lexical + taxonomy synonym expansion; optional `embed` function |
| Procedural | `memory/procedural.py` | planner procedures, domain planner procedures, investigator playbook, intents |
| Taxonomic | `memory/taxonomic.py` | SKOS concepts: agents, precedence, rule domains, notations, labels, related |

## Policy graph

Nodes: rules, outcomes, concepts, fields, policies. Edges (ontology-checked): `explains`, `in_domain`, `reads`, `overridden_by`, `derived_from`, `broader`, `related`, `classifies`, `describes`. Search is a bounded weighted walk; high-reader fields are damped so shared inputs do not swamp results. Each hit keeps its path for the trace.

## Rules and time

Rule sets are effective-dated per rule domain. `rules_mode`: `as_decided` (rules in force at the decision or service date, set by `Settings.rule_resolution`), `current` (`Settings.today`), `as_of` (a given date). `compare_current` runs both and reports what differs and which release caused it. Reference data is resolved as of the service date.

## Data access

Field catalog paths: `<record root>.*` read from the record; `<reference root>.*` need a JSON Pointer, with `{param}` filled from other catalog paths. Investigators request only declared inputs, so large reference payloads never reach an agent or a model.

## Scaling

- One knowledge pack and one toolbox shared by core and domain agents: a record is fetched once per question.
- Domain agents scale out by wrapping a remote client as an `Executor` (any transport); nothing else changes.
- Add domain agents by adding top concepts; add rule domains by adding leaves and folders.
- Semantic memory can take an embedding function when the corpus grows.

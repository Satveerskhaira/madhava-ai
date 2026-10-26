"""The planner: one class for every level of the agent hierarchy.

A planner plans over executors. The core planner's executors are domain
agents; a domain agent is itself a planner whose executors are rule-domain
investigators. Because a planner is also an executor (`handle(task)`), the
hierarchy can nest as deep as needed, and every level has the same
capabilities: understand, plan, act in parallel, verify, loop (bounded),
synthesize and remember.

    observe -> understand -> plan -> act -> verify --gap--> act (bounded)
                                               \\--done--> synthesize -> remember

- observe:    snapshot the record once; outcome and amount tokens; reference pinned by service date
- understand: intents and taxonomy concepts (deterministic, or a guarded model);
              a delegated task already says what to explain
- plan:       pick procedures; route tokens, concepts and hints to executors in scope;
              speculative executors; sweep the whole scope when nothing routes
- act:        send all pending tasks in parallel, each under a deadline
- verify:     every outcome the procedure must explain has a fired rule; if not, and
              rounds remain, reflect (policy graph, related concepts, sweep) and loop once
- synthesize: precedence, primary reason, gaps, rules context, comparison
- remember:   episodic memory and the expert gap backlog (top-level questions only)
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from datetime import date
from typing import Callable, Protocol

from ..knowledge import Knowledge
from ..memory.episodic import EpisodicMemory
from ..memory.procedural import PlannerProcedure, ProceduralMemory
from ..memory.working import Blackboard
from ..models import Ask, ContextRef, DomainReport, DomainTask, Explanation, RoundRecord
from ..settings import Settings
from ..tools.toolbox import Toolbox, as_date
from . import synthesis
from .language import DeterministicLanguage, Language


class Executor(Protocol):
    name: str

    async def handle(self, task: DomainTask) -> DomainReport: ...


class Planner:
    def __init__(self, name: str, knowledge: Knowledge, toolbox: Toolbox, executors: dict[str, Executor],
                 procedures: dict[str, PlannerProcedure], unit_for: Callable[[str], str | None],
                 scope: set[str] | None = None, language: Language | None = None,
                 episodic: EpisodicMemory | None = None, settings: Settings | None = None,
                 label: str | None = None, description: str = ""):
        self.name = name
        self.kn = knowledge
        self.tools = toolbox
        self.executors = executors
        self.procedures = procedures
        self.unit_for_concept = unit_for
        self.scope = scope
        self.language = language or DeterministicLanguage(knowledge.taxonomy, knowledge.procedures.intents)
        self.settings = settings or toolbox.settings
        self.episodic = episodic or EpisodicMemory(self.settings.episodic_dir)
        self.label = label
        self.description = description

    # ------------------------------------------------------------- entries
    async def ask(self, question: str, record_id: str | None = None, session_id: str | None = None,
                  rules_mode: str = "as_decided", as_of: date | None = None) -> Explanation:
        """Answer a question directly (core agent, or a domain agent asked on its own)."""
        bb = Blackboard(agent=self.name, question=question, record_id=record_id, session_id=session_id,
                        requested_rules_mode=rules_mode, as_of=as_of)
        await self.run(bb)
        assert bb.explanation is not None
        return bb.explanation

    async def handle(self, task: DomainTask) -> DomainReport:
        """Work on a task delegated by a parent planner and report findings plus this level's answer."""
        start = time.perf_counter()
        bb = Blackboard(agent=self.name, question=task.question, record_id=task.context.record_id,
                        requested_rules_mode=task.context.rules_mode, task=task)
        try:
            await self.run(bb)
        except Exception as exc:  # noqa: BLE001
            return DomainReport(task_id=task.task_id, agent=self.name, mode=task.mode,
                                rules_mode=task.context.rules_mode, error=f"{type(exc).__name__}: {exc}",
                                steps=[f"{self.name}: {line}" for line in bb.trace])
        exp = bb.explanation
        assert exp is not None
        steps = [f"{self.name}: {line}" for line in bb.trace] + [s for r in bb.reports for s in r.steps]
        return DomainReport(
            task_id=task.task_id, agent=self.name, mode=task.mode, rules_mode=task.context.rules_mode,
            findings=bb.findings(), rule_sets=sorted({rs for r in bb.reports for rs in r.rule_sets}),
            steps=steps, widened=any(r.widened for r in bb.reports), answer=exp.answer, gaps=exp.gaps,
            confidence=exp.confidence, rounds=bb.rounds, elapsed_ms=round((time.perf_counter() - start) * 1000, 1))

    async def run(self, bb: Blackboard) -> None:
        await self.observe(bb)
        await self.understand(bb)
        await self.plan(bb)
        while True:
            await self.act(bb)
            if await self.verify(bb) == "done":
                break
        await self.synthesize(bb)
        await self.remember(bb)

    # ------------------------------------------------------------- routing
    def in_scope(self, concept: str) -> bool:
        return self.scope is None or concept in self.scope

    def unit_for(self, concept: str | None) -> str | None:
        if not concept or not self.in_scope(concept):
            return None
        unit = self.unit_for_concept(concept)
        return unit if unit in self.executors else None

    def route_token(self, token: str, rules_date: date, question: str = "") -> list[tuple[str, list[str], list[str]]]:
        """(executor, concepts, hints) for a token: taxonomy first, then the policy graph inside the scope."""
        concept = self.kn.taxonomy.classify(token)
        unit = self.unit_for(concept)
        if unit:
            return [(unit, [concept], [])]  # type: ignore[list-item]
        out = []
        for h in self.tools.search_rules(rules_date, outcome_tokens=[token], text=question, scope=self.scope, k=10):
            card = self.kn.rules.latest(h.rule_id)
            u = self.unit_for(card.concept)
            if card.outcome.token == token and u:
                out.append((u, [card.concept], [h.rule_id]))
        return out

    # ------------------------------------------------------------- nodes
    async def observe(self, bb: Blackboard) -> None:
        if not bb.record_id:
            bb.log("observe: no record, knowledge question")
            return
        snap = await asyncio.to_thread(self.tools.get_record_outcome, bb.record_id)
        bb.record = {k: snap[k] for k in ("status", "adverse", "codes", "service_date", "decision_date",
                                         "reference", "summary")}
        bb.outcome_tokens = list(snap["outcome_tokens"])
        bb.amount_tokens = list(snap["amount_tokens"])
        bb.log(f"observe: {bb.record_id} {snap['status']} tokens={bb.outcome_tokens + bb.amount_tokens} "
               f"reference={snap['reference']['id']}@{snap['reference']['version']}")

    async def understand(self, bb: Blackboard) -> None:
        if bb.task is not None:
            bb.ask = Ask(question=bb.question, record_id=bb.record_id, intents=[f"delegated_{bb.task.mode}"],
                         concepts=list(bb.task.concepts), source="delegated")
            bb.log(f"understand (delegated): mode={bb.task.mode} tokens={bb.task.outcome_tokens} "
                   f"concepts={bb.task.concepts} hints={bb.task.hints}")
            return
        ask = await self.language.understand(bb.question, bb.record_id)
        intents = list(ask.intents)
        adverse = bool(bb.record.get("adverse"))
        if bb.record_id:
            if adverse and "explain_cost" in intents:
                intents = [("what_if_approved" if i == "explain_cost" else i) for i in intents]
            if not adverse and "explain_cost" in intents and "explain_outcome" in intents:
                intents.remove("explain_outcome")
            if not intents:
                intents = ["explain_cost"] if not adverse and bb.amount_tokens else ["explain_outcome"]
            if adverse and "explain_outcome" not in intents and "compare_current" not in intents:
                intents.insert(0, "explain_outcome")
        elif not intents:
            intents = ["define_term"]
        ask.intents = list(dict.fromkeys(intents))
        bb.ask = ask
        bb.log(f"understand ({ask.source}): intents={ask.intents} concepts={ask.concepts}")

    async def plan(self, bb: Blackboard) -> None:
        assert bb.ask is not None
        procs = ProceduralMemory.for_intents(self.procedures, bb.ask.intents)
        bb.procedures = [p.name for p in procs]
        if not bb.record_id:
            bb.log(f"plan: {bb.procedures} (no executors needed)")
            return
        snap = self.tools.get_record_outcome(bb.record_id)
        if bb.task is not None:
            bb.rules_modes = [bb.task.context.rules_mode]
            bb.contexts[bb.task.context.rules_mode] = bb.task.context
        else:
            modes = [bb.requested_rules_mode]
            if any("current" in p.rules_modes for p in procs) and "current" not in modes:
                modes.append("current")
            bb.rules_modes = modes
            for m in modes:
                bb.contexts[m] = ContextRef(record_id=bb.record_id, reference_id=snap["reference"]["id"],
                                            service_date=as_date(snap["service_date"]), rules_mode=m,
                                            rules_date=self.tools.rules_date(snap, m, bb.as_of))
        bb.max_rounds = max([p.max_rounds for p in procs] + [1])
        bb.expand = list(dict.fromkeys(s for p in procs for s in p.expand))
        must: list[str] = []
        tasks: dict[tuple[str, str, str], DomainTask] = {}
        task_tokens = list(bb.task.outcome_tokens) if bb.task else []

        def task_for(unit: str, mode: str, rules_mode: str) -> DomainTask:
            key = (unit, mode, rules_mode)
            if key not in tasks:
                tasks[key] = DomainTask(task_id=uuid.uuid4().hex[:12], agent=unit, mode=mode,  # type: ignore[arg-type]
                                        context=bb.contexts[rules_mode], question=bb.question, round=1)
            return tasks[key]

        def add(t: DomainTask, tokens: list[str] = (), concepts: list[str] = (), hints: list[str] = ()) -> None:
            t.outcome_tokens = list(dict.fromkeys([*t.outcome_tokens, *tokens]))
            t.concepts = list(dict.fromkeys([*t.concepts, *concepts]))
            t.hints = list(dict.fromkeys([*t.hints, *hints]))

        for p in procs:
            if p.task_mode == "none":
                continue
            tokens: list[str] = []
            if "outcome_tokens" in p.route_from:
                tokens += bb.outcome_tokens
            if "amount_tokens" in p.route_from:
                tokens += bb.amount_tokens
            if "task_tokens" in p.route_from:
                tokens += task_tokens
            if p.must_explain == "outcome_tokens":
                must += bb.outcome_tokens
            elif p.must_explain == "amount_tokens":
                must += bb.amount_tokens
            elif p.must_explain == "task_tokens":
                must += task_tokens
            concepts: list[str] = []
            if "question_concepts" in p.route_from:
                concepts += bb.ask.concepts
            if "task_concepts" in p.route_from and bb.task:
                concepts += bb.task.concepts
            hints = list(bb.task.hints) if bb.task and "task_hints" in p.route_from else []
            for rules_mode in (p.rules_modes or [bb.primary_rules_mode]):
                if rules_mode not in bb.contexts:
                    continue
                day = bb.contexts[rules_mode].rules_date
                routed = False
                for tok in tokens:
                    for unit, cs, hs in self.route_token(tok, day, bb.question):
                        add(task_for(unit, p.task_mode, rules_mode), [tok], cs, hs)
                        routed = True
                for c in concepts:
                    unit = self.unit_for(c)
                    if unit and p.task_mode == "explain" and adverse_cost_concept(self, bb, unit):
                        continue
                    if unit:
                        add(task_for(unit, p.task_mode, rules_mode), concepts=[c])
                        routed = True
                for h in hints:
                    card = self.kn.rules.latest(h) if h in self.kn.rules.rule_ids() else None
                    unit = self.unit_for(card.concept) if card else None
                    if unit:
                        add(task_for(unit, p.task_mode, rules_mode), concepts=[card.concept], hints=[h])  # type: ignore[union-attr]
                        routed = True
                spec = list(self.executors) if "*" in p.speculative else [s for s in p.speculative if s in self.executors]
                for unit in spec:
                    task_for(unit, p.task_mode, rules_mode)
                    routed = True
                if p.sweep_if_unrouted and not routed:
                    for unit in self.executors:
                        task_for(unit, p.task_mode, rules_mode)
                    bb.log(f"plan: nothing routed for {p.name}, sweeping {list(self.executors)}")

        must = list(dict.fromkeys(must))
        if self.scope is not None and bb.task is None:
            # A domain agent asked directly explains only outcomes in its scope; the rest is reported as such.
            day = bb.contexts[bb.primary_rules_mode].rules_date
            in_scope = [t for t in must if self.route_token(t, day)]
            bb.out_of_scope = [t for t in must if t not in in_scope]
            must = in_scope
        bb.must_explain = must
        if bb.task is None:
            previous = self.episodic.last_for(bb.session_id, bb.record_id, self.name)
            if previous:
                reuse = [DomainReport.model_validate(r) for r in previous.get("reports", [])]
                have = {(r.agent, r.mode, r.rules_mode) for r in reuse}
                for key in list(tasks):
                    if key in have:
                        del tasks[key]
                bb.reports.extend(r for r in reuse if r.rules_mode in bb.contexts)
                bb.reused_from_session = bool(reuse)
                bb.log(f"plan: reused {len(reuse)} reports from session {bb.session_id}")
        bb.pending = list(tasks.values())
        for t in bb.pending:
            bb.asked.extend(f"{t.agent}|{tok}|{t.context.rules_mode}|{t.mode}" for tok in t.outcome_tokens or ["*"])
        bb.log(f"plan: procedures={bb.procedures} must_explain={bb.must_explain} rules_modes={bb.rules_modes} "
               f"tasks={[f'{t.agent}:{t.mode}:{t.context.rules_mode}' for t in bb.pending]}")

    async def act(self, bb: Blackboard) -> None:
        bb.round += 1
        tasks, bb.pending = bb.pending, []
        if not tasks:
            bb.rounds.append(RoundRecord(round=bb.round, agents=[], reason="nothing to ask"))
            return
        start = time.perf_counter()

        async def one(task: DomainTask) -> DomainReport:
            try:
                wire = DomainTask.model_validate_json(task.model_dump_json())   # same contract in and out of process
                report = await asyncio.wait_for(self.executors[task.agent].handle(wire),
                                                timeout=self.settings.agent_deadline_s)
                return DomainReport.model_validate_json(report.model_dump_json())
            except Exception as exc:  # noqa: BLE001 - late or failed executors become error reports
                return DomainReport(task_id=task.task_id, agent=task.agent, mode=task.mode,
                                    rules_mode=task.context.rules_mode, error=f"{type(exc).__name__}: {exc}")

        reports = await asyncio.gather(*(one(t) for t in tasks))
        bb.reports.extend(reports)
        elapsed = round((time.perf_counter() - start) * 1000, 1)
        bb.rounds.append(RoundRecord(round=bb.round, agents=[f"{t.agent}:{t.mode}:{t.context.rules_mode}" for t in tasks],
                                     reason="initial plan" if bb.round == 1 else "gap round", elapsed_ms=elapsed))
        for r in reports:
            fired = [f.rule_id for f in r.findings if f.status in ("fired", "overridden")]
            extra = f" gaps={r.gaps}" if r.gaps else ""
            bb.log(f"act r{bb.round}: {r.agent}/{r.mode}/{r.rules_mode} fired={fired}{extra}"
                   f"{' error=' + r.error if r.error else ''}")

    def _unexplained(self, bb: Blackboard) -> list[str]:
        mode = bb.primary_rules_mode
        explained = {f.outcome_token for f in bb.findings(mode="explain", rules_mode=mode) if f.explains}
        return [t for t in bb.must_explain if t not in explained]

    async def verify(self, bb: Blackboard) -> str:
        gaps = self._unexplained(bb)
        bb.gaps = gaps
        if not gaps:
            bb.log(f"verify r{bb.round}: all outcomes explained")
            return "done"
        if bb.round >= bb.max_rounds:
            bb.log(f"verify r{bb.round}: unexplained {gaps}, no rounds left")
            return "done"
        new = self.reflect(bb, gaps)
        if not new:
            bb.log(f"verify r{bb.round}: unexplained {gaps}, nothing new to try")
            return "done"
        bb.pending = new
        bb.log(f"verify r{bb.round}: unexplained {gaps}, gap round with {[t.agent for t in new]}")
        return "gap"

    def reflect(self, bb: Blackboard, gaps: list[str]) -> list[DomainTask]:
        """Plan the gap round from the policy graph, related concepts, or a sweep of the scope."""
        mode = bb.primary_rules_mode
        ctx = bb.contexts[mode]
        tax = self.kn.taxonomy
        task_mode = bb.task.mode if bb.task else "explain"
        new: dict[str, DomainTask] = {}
        asked_units = {k.split("|")[0] for k in bb.asked if k.split("|")[2] == mode}

        def add(unit: str, token: str, concepts: list[str], hints: list[str], why: str) -> None:
            if f"{unit}|{token}|{mode}|{task_mode}" in bb.asked and unit not in new:
                return
            task = new.setdefault(unit, DomainTask(task_id=uuid.uuid4().hex[:12], agent=unit, mode=task_mode,  # type: ignore[arg-type]
                                                   context=ctx, question=bb.question, round=bb.round + 1))
            task.outcome_tokens = list(dict.fromkeys([*task.outcome_tokens, token]))
            task.concepts = list(dict.fromkeys([*task.concepts, *concepts]))
            task.hints = list(dict.fromkeys([*task.hints, *hints]))
            bb.log(f"reflect: {token} -> {unit} via {why}")

        for token in gaps:
            for strategy in bb.expand:
                if strategy == "policy_graph":
                    for h in self.tools.search_rules(ctx.rules_date, outcome_tokens=[token], text=bb.question,
                                                     scope=self.scope, k=10):
                        card = self.kn.rules.latest(h.rule_id)
                        unit = self.unit_for(card.concept)
                        if card.outcome.token == token and unit:
                            add(unit, token, [card.concept], [h.rule_id], f"policy graph {' '.join(h.path)}")
                elif strategy == "related_concepts":
                    concept = tax.classify(token)
                    for rel in tax.get(concept).related if concept else []:
                        unit = self.unit_for(rel)
                        if unit:
                            add(unit, token, [rel], [], f"related concept {concept} ~ {rel}")
                elif strategy == "sweep_scope":
                    for unit in self.executors:
                        if unit not in asked_units:
                            add(unit, token, [], [], "sweep of remaining scope")
        for t in new.values():
            bb.asked.extend(f"{t.agent}|{tok}|{mode}|{task_mode}" for tok in t.outcome_tokens)
        return list(new.values())

    async def synthesize(self, bb: Blackboard) -> None:
        if not bb.record_id:
            bb.explanation = self._define(bb)
            return
        explanation, bundle = synthesis.build(bb, self.kn, self.settings, self.label)
        if bb.task is None:
            explanation.answer = await self.language.narrate(bundle, explanation.answer)
        cited = [f.rule_id for f in ([explanation.primary] if explanation.primary else [])
                 + explanation.supporting + explanation.overridden + explanation.what_if]
        explanation.sources = self.kn.sources_for(dict.fromkeys(cited))
        bb.explanation = explanation

    async def remember(self, bb: Blackboard) -> None:
        if not bb.explanation or bb.task is not None:
            return
        exp = bb.explanation
        rule_sets = sorted(set(exp.rules_context.get("rule_sets", {}).values())) if exp.rules_context else []
        self.episodic.record_episode({
            "agent": self.name, "session_id": bb.session_id, "record_id": bb.record_id, "question": bb.question,
            "intents": exp.intents, "confidence": exp.confidence,
            "primary": exp.primary.key if exp.primary else None, "gaps": exp.gaps,
            "rule_sets": rule_sets, "reference": exp.reference_context,
            "reports": [r.model_dump(mode="json") for r in bb.reports],
        })
        for token in exp.gaps:
            self.episodic.record_gap(token, bb.record_id, bb.question, rule_sets)

    # ---------------------------------------------------- knowledge answers
    def _define(self, bb: Blackboard) -> Explanation:
        hits = self.kn.semantic.search(bb.question, kinds={"term", "concept", "field", "code"}, k=3)
        code = re.search(r"\bcode\s+([0-9A-Z][0-9A-Z-]{1,})\b", bb.question, re.I)
        if code and code.group(1).upper() in self.kn.codes:
            hits = [h for h in hits if h.doc.id == f"code:{code.group(1).upper()}"] or hits
        else:
            exact = [t for t in (self.kn.glossary.lookup(bb.question) if self.kn.glossary else []) if t.kind == "term"]
            if exact:      # the question names a glossary term or abbreviation: answer with exactly that
                ids = {f"term:{t.id}" for t in exact}
                hits = [h for h in self.kn.semantic.search(bb.question, kinds={"term"}, k=10) if h.doc.id in ids] or hits
            elif hits:
                hits = [h for h in hits if h.score >= 0.6 * hits[0].score]
        intents = bb.ask.intents if bb.ask else []
        if not hits:
            return Explanation(agent=self.name, record_id=None, question=bb.question, confidence="unexplained",
                               answer="That term is not in the glossary, taxonomy, field catalog or code table.",
                               intents=intents, trace=bb.trace)
        lines = []
        for h in hits:
            m = h.doc.meta
            if h.doc.kind == "term":
                abbr = f" ({', '.join(m['abbreviations'])})" if m.get("abbreviations") else ""
                about = f" [taxonomy: {', '.join(m['concepts'])}]" if m.get("concepts") else ""
                lines.append(f"{m['label']}{abbr}: {m['definition']}{about}")
            elif h.doc.kind == "concept":
                lines.append(f"{m['label']}: {m['definition']} (taxonomy concept {m['concept']})")
            elif h.doc.kind == "field":
                std = ", ".join(f"{k} {v}" for k, v in m.items()
                                if k not in {"path", "concept", "description"} and isinstance(v, str))
                lines.append(f"{m['path']}: {m['description']}" + (f" ({std})" if std else ""))
            else:
                lines.append(f"{m['kind'].capitalize()} code {m['code']}: {m['meaning']}")
        return Explanation(agent=self.name, record_id=None, question=bb.question, answer="\n".join(lines),
                           confidence="supporting", intents=intents, trace=bb.trace)


def adverse_cost_concept(planner: Planner, bb: Blackboard, unit: str) -> bool:
    """Cost words on an adverse record are a what-if, handled by that procedure, not an explanation task."""
    if not bb.record.get("adverse") or bb.task is not None:
        return False
    tax = planner.kn.taxonomy
    concepts = tax.branch(unit) if unit in tax.agents else set(tax.concept_for_rule_domain(unit))
    notations = [n for c in concepts for n in tax.get(c).notations]
    return bool(notations) and all(n.startswith("price:") for n in notations)

"""The core planner agent.

Graph of steps (the ADK workflow in madhava.adk.workflow wires the same
methods as graph nodes):

    observe -> understand -> plan -> act -> verify --gap--> act (bounded)
                                               \\--done--> synthesize -> remember

- observe:    snapshot the claim once; outcome and price tokens; plan pinned by DOS
- understand: intents and taxonomy concepts (model optional, guarded)
- plan:       pick procedures from procedural memory; route tokens and concepts
              through the taxonomy to domain agents; one task per agent and mode
- act:        send all pending tasks in parallel over the transport (A2A or in process)
- verify:     coverage check: every outcome the procedure must explain has a
              fired rule. If not, and rounds remain, reflect: search the policy
              graph and related concepts for agents not yet asked, and loop once
- synthesize: precedence, primary reason, gaps, rules context, comparison
- remember:   episodic memory and the SME gap backlog
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from datetime import date

from ..knowledge import Knowledge
from ..memory.episodic import EpisodicMemory
from ..memory.working import Blackboard
from ..models import ContextRef, DomainReport, DomainTask, Explanation, RoundRecord
from ..settings import Settings
from ..tools.toolbox import Toolbox
from . import synthesis
from .language import DeterministicLanguage, Language
from .registry import AgentRegistry, InProcessTransport, Transport


class Planner:
    def __init__(self, knowledge: Knowledge, toolbox: Toolbox, registry: AgentRegistry,
                 transport: Transport | None = None, language: Language | None = None,
                 episodic: EpisodicMemory | None = None, settings: Settings | None = None):
        self.kn = knowledge
        self.tools = toolbox
        self.registry = registry
        self.transport = transport or InProcessTransport(registry)
        self.language = language or DeterministicLanguage(knowledge.taxonomy)
        self.settings = settings or toolbox.settings
        self.episodic = episodic or EpisodicMemory(self.settings.episodic_dir)

    # ------------------------------------------------------------- entry
    async def ask(self, question: str, claim_id: str | None = None, session_id: str | None = None,
                  rules_mode: str = "as_adjudicated", as_of: date | None = None) -> Explanation:
        bb = Blackboard(question=question, claim_id=claim_id, session_id=session_id,
                        requested_rules_mode=rules_mode, as_of=as_of)
        await self.observe(bb)
        await self.understand(bb)
        await self.plan(bb)
        while True:
            await self.act(bb)
            if await self.verify(bb) == "done":
                break
        await self.synthesize(bb)
        await self.remember(bb)
        assert bb.explanation is not None
        return bb.explanation

    # ------------------------------------------------------------- nodes
    async def observe(self, bb: Blackboard) -> None:
        if not bb.claim_id:
            bb.log("observe: no claim, knowledge question")
            return
        snap = await asyncio.to_thread(self.tools.get_claim_outcome, bb.claim_id)
        bb.claim = {k: snap[k] for k in ("status", "rejects", "dos", "adjudicated_at", "plan", "drug_name")}
        bb.outcome_tokens = list(snap["outcome_tokens"])
        bb.price_tokens = list(snap["price_tokens"])
        bb.log(f"observe: {bb.claim_id} {snap['status']} tokens={bb.outcome_tokens + bb.price_tokens} "
               f"plan={snap['plan']['plan_id']}@{snap['plan']['version']}")

    async def understand(self, bb: Blackboard) -> None:
        ask = await self.language.understand(bb.question, bb.claim_id)
        intents = list(ask.intents)
        status = bb.claim.get("status")
        if bb.claim_id:
            if status == "rejected" and "explain_cost" in intents:
                intents = [("what_if_approved" if i == "explain_cost" else i) for i in intents]
            if status == "paid" and "explain_cost" in intents and "explain_outcome" in intents:
                intents.remove("explain_outcome")
            if not intents:
                intents = ["explain_cost"] if status == "paid" and bb.price_tokens else ["explain_outcome"]
            if status == "rejected" and "explain_outcome" not in intents and "compare_current" not in intents:
                intents.insert(0, "explain_outcome")
        elif not intents:
            intents = ["define_term"]
        ask.intents = list(dict.fromkeys(intents))
        bb.ask = ask
        bb.log(f"understand ({ask.source}): intents={ask.intents} concepts={ask.concepts}")

    async def plan(self, bb: Blackboard) -> None:
        assert bb.ask is not None
        procs = self.kn.procedures.for_intents(bb.ask.intents)
        bb.procedures = [p.name for p in procs]
        if not bb.claim_id:
            bb.log(f"plan: {bb.procedures} (no agents needed)")
            return
        modes = [bb.requested_rules_mode]
        if any("current" in p.rules_modes for p in procs) and "current" not in modes:
            modes.append("current")
        bb.rules_modes = modes
        snap = self.tools.get_claim_outcome(bb.claim_id)
        for m in modes:
            bb.contexts[m] = ContextRef(claim_id=bb.claim_id, plan_id=snap["plan"]["plan_id"],
                                        dos=date.fromisoformat(snap["dos"]), rules_mode=m,
                                        rules_date=self.tools.rules_date(snap, m, bb.as_of))
        bb.max_rounds = max([p.max_rounds for p in procs] + [1])
        bb.expand = list(dict.fromkeys(s for p in procs for s in p.expand))
        must: list[str] = []
        tax = self.kn.taxonomy
        tasks: dict[tuple[str, str, str], DomainTask] = {}

        def task_for(agent: str, mode: str, rules_mode: str) -> DomainTask:
            key = (agent, mode, rules_mode)
            if key not in tasks:
                tasks[key] = DomainTask(task_id=uuid.uuid4().hex[:12], agent=agent, mode=mode,  # type: ignore[arg-type]
                                        context=bb.contexts[rules_mode], question=bb.question, round=1)
            return tasks[key]

        for p in procs:
            if p.task_mode == "none":
                continue
            tokens: list[str] = []
            if "outcome_tokens" in p.route_from:
                tokens += bb.outcome_tokens
            if "price_tokens" in p.route_from:
                tokens += bb.price_tokens
            if p.must_explain == "outcome_tokens":
                must += bb.outcome_tokens
            elif p.must_explain == "price_tokens":
                must += bb.price_tokens
            for rules_mode in (p.rules_modes or [bb.primary_rules_mode]):
                if rules_mode not in bb.contexts:
                    continue
                for t in tokens:
                    concept = tax.classify(t)
                    agent = tax.agent_for(concept) if concept else None
                    if agent:
                        task = task_for(agent, p.task_mode, rules_mode)
                        task.outcome_tokens = list(dict.fromkeys([*task.outcome_tokens, t]))
                        task.concepts = list(dict.fromkeys([*task.concepts, concept]))  # type: ignore[list-item]
                if "question_concepts" in p.route_from:
                    for c in bb.ask.concepts:
                        agent = tax.agent_for(c)
                        if agent and bb.claim.get("status") == "rejected" and self._pricing_only(agent):
                            continue   # cost words on a rejected claim are a what-if, handled by that procedure
                        if agent:
                            task = task_for(agent, p.task_mode, rules_mode)
                            task.concepts = list(dict.fromkeys([*task.concepts, c]))
                for agent in p.speculative_agents:
                    task_for(agent, p.task_mode, rules_mode)

        bb.must_explain = list(dict.fromkeys(must))
        # Follow-up in the same session: reuse reports already gathered for this claim.
        previous = self.episodic.last_for(bb.session_id, bb.claim_id)
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
            bb.asked.extend(f"{t.agent}|{tok}|{t.context.rules_mode}" for tok in t.outcome_tokens or ["*"])
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
                return await asyncio.wait_for(self.transport.send(task), timeout=self.settings.agent_deadline_s)
            except Exception as exc:  # noqa: BLE001 - late or failed agents become pending findings
                return DomainReport(task_id=task.task_id, agent=task.agent, mode=task.mode,
                                    rules_mode=task.context.rules_mode, error=f"{type(exc).__name__}: {exc}")

        reports = await asyncio.gather(*(one(t) for t in tasks))
        bb.reports.extend(reports)
        elapsed = round((time.perf_counter() - start) * 1000, 1)
        reason = "initial plan" if bb.round == 1 else "gap round"
        bb.rounds.append(RoundRecord(round=bb.round, agents=[f"{t.agent}:{t.mode}:{t.context.rules_mode}" for t in tasks],
                                     reason=reason, elapsed_ms=elapsed))
        for r in reports:
            fired = [f.rule_id for f in r.findings if f.status in ("fired", "overridden")]
            bb.log(f"act r{bb.round}: {r.agent}/{r.mode}/{r.rules_mode} fired={fired} "
                   f"{'error=' + r.error if r.error else ''}".rstrip())

    def _pricing_only(self, agent: str) -> bool:
        tax = self.kn.taxonomy
        notations = [n for c in tax.branch(agent) for n in tax.get(c).notations]
        return bool(notations) and all(n.startswith("price:") for n in notations)

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
        """Plan the gap round from the policy graph and related concepts."""
        mode = bb.primary_rules_mode
        ctx = bb.contexts[mode]
        tax = self.kn.taxonomy
        new: dict[str, DomainTask] = {}

        def add(agent: str, token: str, concepts: list[str], hints: list[str], why: str) -> None:
            key = f"{agent}|{token}|{mode}"
            if key in bb.asked and agent not in new:
                return
            task = new.setdefault(agent, DomainTask(task_id=uuid.uuid4().hex[:12], agent=agent, mode="explain",
                                                    context=ctx, question=bb.question, round=bb.round + 1))
            task.outcome_tokens = list(dict.fromkeys([*task.outcome_tokens, token]))
            task.concepts = list(dict.fromkeys([*task.concepts, *concepts]))
            task.hints = list(dict.fromkeys([*task.hints, *hints]))
            bb.log(f"reflect: {token} -> {agent} via {why}")

        for token in gaps:
            for strategy in bb.expand:
                if strategy == "policy_graph":
                    hits = self.tools.search_rules(ctx.rules_date, outcome_tokens=[token], text=bb.question, k=10)
                    for h in hits:
                        card = self.kn.rules.latest(h.rule_id)
                        if card.outcome.token != token:
                            continue
                        agent = tax.agent_for(card.concept)
                        if agent:
                            add(agent, token, [card.concept], [h.rule_id], f"policy graph {' '.join(h.path)}")
                elif strategy == "related_concepts":
                    concept = tax.classify(token)
                    for rel in tax.get(concept).related if concept else []:
                        agent = tax.agent_for(rel)
                        if agent:
                            add(agent, token, [rel], [], f"related concept {concept} ~ {rel}")
        for t in new.values():
            bb.asked.extend(f"{t.agent}|{tok}|{mode}" for tok in t.outcome_tokens)
        return list(new.values())

    async def synthesize(self, bb: Blackboard) -> None:
        if not bb.claim_id:
            bb.explanation = self._define(bb)
            return
        explanation, bundle = synthesis.build(bb, self.kn, self.settings)
        explanation.answer = await self.language.narrate(bundle, explanation.answer)
        bb.explanation = explanation

    async def remember(self, bb: Blackboard) -> None:
        if not bb.explanation:
            return
        exp = bb.explanation
        rule_sets = sorted(set(exp.rules_context.get("rule_sets", {}).values())) if exp.rules_context else []
        self.episodic.record_episode({
            "session_id": bb.session_id, "claim_id": bb.claim_id, "question": bb.question,
            "intents": exp.intents, "confidence": exp.confidence,
            "primary": exp.primary.key if exp.primary else None, "gaps": exp.gaps,
            "rule_sets": rule_sets, "plan": exp.plan_context,
            "reports": [r.model_dump(mode="json") for r in bb.reports],
        })
        for token in exp.gaps:
            self.episodic.record_gap(token, bb.claim_id, bb.question, rule_sets)

    # ---------------------------------------------------- knowledge answers
    def _define(self, bb: Blackboard) -> Explanation:
        hits = self.kn.semantic.search(bb.question, kinds={"concept", "field", "code"}, k=3)
        code = re.search(r"\bcode\s+([0-9A-Z]{2,3})\b", bb.question, re.I)
        if code and code.group(1).upper() in self.kn.reject_codes:
            hits = [h for h in hits if h.doc.id == f"code:{code.group(1).upper()}"] or hits
        elif hits:
            hits = [h for h in hits if h.score >= 0.6 * hits[0].score]
        if not hits:
            answer = "I could not find that term in the taxonomy, field catalog or reject code table."
            return Explanation(claim_id=None, question=bb.question, answer=answer, confidence="unexplained",
                               intents=bb.ask.intents if bb.ask else [], trace=bb.trace)
        lines = []
        for h in hits:
            m = h.doc.meta
            if h.doc.kind == "concept":
                lines.append(f"{m['label']}: {m['definition']} (taxonomy concept {m['concept']})")
            elif h.doc.kind == "field":
                ncpdp = f", NCPDP {m['ncpdp']}" if m.get("ncpdp") else ""
                lines.append(f"{m['path']}: {m['description']}{ncpdp}")
            else:
                lines.append(f"Reject code {m['code']}: {m['meaning']}")
        return Explanation(claim_id=None, question=bb.question, answer="\n".join(lines), confidence="supporting",
                           intents=bb.ask.intents if bb.ask else [], trace=bb.trace)

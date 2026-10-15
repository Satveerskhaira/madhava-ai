"""Turn findings into one answer: pick the primary reason, state gaps, add context.

The primary reason is the fired rule that explains an observed outcome and
comes first in decision order (taxonomy precedence, then rule order). Nothing
unexplained is filled in; gaps are stated as gaps. Wording comes from the
pack's phrases, so each domain can speak its own language.
"""

from __future__ import annotations

from typing import Any

from ..knowledge import Knowledge
from ..memory.working import Blackboard
from ..models import Explanation, Finding
from ..settings import Settings


def _cite(f: Finding) -> str:
    return f"[{f.rule_id} v{f.version}, {f.confidence}]"


def _money(v: float | None) -> str:
    return "unknown" if v is None else f"${v:,.2f}"


def build(bb: Blackboard, kn: Knowledge, settings: Settings, label: str | None = None) -> tuple[Explanation, dict[str, Any]]:
    ph = kn.pack.phrases
    mode = bb.primary_rules_mode
    observed = set(bb.outcome_tokens) | set(bb.amount_tokens)
    explain = bb.findings(mode="explain", rules_mode=mode)
    explaining = sorted((f for f in explain if f.explains and f.outcome_token in observed),
                        key=lambda f: (f.precedence, f.order))
    primary = explaining[0] if explaining else None
    supporting = list(explaining[1:])
    supporting += [f for f in explain if f.status == "fired" and f.confidence == "supporting" and f.outcome_type == "info"]
    overridden = [f for f in explain if f.status == "overridden"]
    conflicts = [f for f in explain if f.status == "fired" and f.outcome_type == "reject" and f.outcome_token not in observed]
    unverified = [f for f in explain if f.status == "unverified"]
    what_if = [f for f in bb.findings(mode="what_if") if f.status == "fired"]
    checked = [f for f in explain if f.status == "not_fired"]
    gaps = list(bb.gaps)

    rules_context = _rules_context(bb, kn, settings, explain)
    ref = bb.record.get("reference", {})
    reference_context = {**ref, "service_date": bb.record.get("service_date")}
    compare = _compare(bb, kn, settings) if "current" in bb.rules_modes and mode != "current" else None

    if primary is None:
        if gaps:
            confidence = "unexplained"
        elif compare:
            confidence = "likely"          # a projection under today's rules, not a decision
        elif what_if:
            confidence = "what_if"
        elif bb.delegated or bb.out_of_scope:
            confidence = "supporting"      # nothing in this agent's scope needed explaining
        else:
            confidence = "unexplained"
    elif gaps:
        confidence = "likely"
    else:
        confidence = primary.confidence or "likely"

    lines: list[str] = []
    if label:
        lines.append(ph["agent_header"].format(label=label))
    if compare:
        lines.append(compare["summary"])
    if primary:
        if primary.outcome_type == "pricing":
            lines.append(ph["amount_paid"].format(actual=_money(primary.actual), narrative=primary.narrative,
                                                  cite=_cite(primary)))
        else:
            lines.append(ph["primary_outcome"].format(token=kn.describe_token(primary.outcome_token or ""),
                                                      narrative=primary.narrative, cite=_cite(primary)))
        if primary.note:
            lines.append(f"Note: {primary.note}.")
    for f in supporting:
        lines.append(ph["supporting"].format(narrative=f.narrative, cite=_cite(f)))
    for f in overridden:
        by = next((o for o in explain if o.key == f.overridden_by), None)
        reason = f" It was overridden by {by.rule_id}: {by.intent}." if by else f" It was overridden by {f.overridden_by}."
        lines.append(ph["overridden"].format(intent=f.intent, rule=f"{f.rule_id} v{f.version}", reason=reason))
    for f in what_if:
        lines.append(ph["what_if"].format(narrative=f.narrative, rule=f"{f.rule_id} v{f.version}"))
    for f in conflicts:
        lines.append(ph["conflict"].format(intent=f.intent, token=f.outcome_token, rule=f"{f.rule_id} v{f.version}"))
    for f in unverified:
        lines.append(ph["unverified"].format(rule=f.rule_id, error=f.error))
    clear_agents = sorted({f.agent for f in checked} - {f.agent for f in explain if f.status in ("fired", "overridden")})
    if clear_agents:
        agents = kn.taxonomy.agents
        names = ", ".join(kn.taxonomy.get(agents[a]).prefLabel.lower() if a in agents else a for a in clear_agents)
        when = f" under the rules {kn.pack.rules_modes.get('as_decided', 'as decided')}" if compare else ""
        lines.append(ph["checked"].format(when=when, names=names))
    for t in gaps:
        lines.append(ph["gap_scoped" if label else "gap"].format(token=kn.describe_token(t)))
    if bb.out_of_scope:
        lines.append(ph["out_of_scope"].format(tokens=", ".join(kn.describe_token(t) for t in bb.out_of_scope)))
    if rules_context.get("summary"):
        lines.append(rules_context["summary"])
    if reference_context.get("id"):
        lines.append(ph["reference_line"].format(**{k: reference_context.get(k) for k in
                                                    ("id", "version", "effective_from", "service_date")}))

    explanation = Explanation(
        agent=bb.agent, record_id=bb.record_id, question=bb.question, answer="\n".join(lines), confidence=confidence,
        intents=bb.ask.intents if bb.ask else [], primary=primary, supporting=supporting, what_if=what_if,
        overridden=overridden, checked=checked, gaps=gaps, out_of_scope=list(bb.out_of_scope),
        rules_context=rules_context, reference_context=reference_context, compare=compare,
        rounds=bb.rounds, trace=bb.trace,
    )
    bundle = {
        "agent": bb.agent,
        "question": bb.question,
        "record": {k: bb.record.get(k) for k in ("status", "codes", "service_date", "reference")},
        "primary_rule": primary.rule_id if primary else None,
        "rule_ids": sorted({f.rule_id for f in [*explain, *what_if]}),
        "primary": primary.model_dump(include={"rule_id", "version", "narrative", "confidence", "amount", "actual"}) if primary else None,
        "supporting": [f.model_dump(include={"rule_id", "version", "narrative"}) for f in supporting],
        "overridden": [f.model_dump(include={"rule_id", "version", "intent", "overridden_by"}) for f in overridden],
        "what_if": [f.model_dump(include={"rule_id", "version", "narrative", "amount"}) for f in what_if],
        "gaps": [kn.describe_token(t) for t in gaps],
        "rules_context": rules_context,
        "compare": compare,
    }
    return explanation, bundle


def _rules_context(bb: Blackboard, kn: Knowledge, settings: Settings, explain: list[Finding]) -> dict[str, Any]:
    ctx = bb.contexts.get(bb.primary_rules_mode)
    if ctx is None:
        return {}
    used: dict[str, str] = {}
    for f in explain:
        if f.rule_set:
            used[f.domain] = f.rule_set
    changes = []
    for domain in sorted(used):
        for rs in kn.rules.changes_after(domain, ctx.rules_date, settings.today):
            changes.append({"domain": domain, "rule_set": rs.id, "effective_from": rs.effective_from.isoformat(),
                            "reason": rs.reason})
    label = kn.pack.rules_modes.get(ctx.rules_mode, ctx.rules_mode)
    summary = f"Rules used: {label} on {ctx.rules_date.isoformat()} ({', '.join(sorted(set(used.values())))})."
    if changes and ctx.rules_mode != "current":
        what = "; ".join(f"{c['domain']} changed {c['effective_from']} ({c['rule_set']}: {c['reason']})" for c in changes)
        summary += f" Note: the rules have changed since this {kn.pack.record_noun}: {what}."
        if "current" not in bb.rules_modes:
            summary += " Ask whether it would happen today to compare."
    return {"mode": ctx.rules_mode, "resolution_date": ctx.rules_date.isoformat(), "rule_sets": used,
            "changed_since_record": bool(changes), "changes": changes, "summary": summary}


def _compare(bb: Blackboard, kn: Knowledge, settings: Settings) -> dict[str, Any]:
    then = [f for f in bb.findings(mode="explain", rules_mode="as_decided")
            if f.status == "fired" and f.outcome_type == "reject"]
    now = [f for f in bb.findings(mode="explain", rules_mode="current")
           if f.status == "fired" and f.outcome_type == "reject"]
    then_tokens = {f.outcome_token for f in then}
    now_tokens = {f.outcome_token for f in now}
    differs = then_tokens != now_tokens
    now_primary = min(now, key=lambda f: (f.precedence, f.order)) if now else None
    then_date = bb.contexts["as_decided"].rules_date
    changed_domains = sorted({f.domain for f in [*then, *now]
                              if (f.outcome_token in then_tokens) != (f.outcome_token in now_tokens)})
    changes = {d: kn.rules.diff(d, then_date, settings.today) for d in changed_domains}
    releases = [rs for d in changed_domains for rs in kn.rules.changes_after(d, then_date, settings.today)]
    status = bb.record.get("status")
    if not differs:
        summary = (f"Under today's rules ({settings.today.isoformat()}) the outcome would be the same as on "
                   f"{then_date.isoformat()}.")
    elif now_primary:
        cause = "; ".join(f"{rs.id} effective {rs.effective_from.isoformat()}: {rs.reason}" for rs in releases)
        noun = kn.pack.record_noun
        summary = (f"What happened: the {noun} was {status} on {then_date.isoformat()}. Under today's rules it would "
                   f"get {kn.describe_token(now_primary.outcome_token or '')}: {now_primary.narrative} "
                   f"[{now_primary.rule_id} v{now_primary.version}]. Rule change responsible: {cause}.")
    else:
        summary = (f"What happened: the {kn.pack.record_noun} was {status} on {then_date.isoformat()}. Under today's rules no "
                   f"adverse rule would fire for {', '.join(sorted(t or '' for t in then_tokens))}.")
    return {
        "differs": differs,
        "as_decided": {"date": then_date.isoformat(), "outcomes": sorted(t or "" for t in then_tokens),
                       "rules": [f.key for f in then]},
        "current": {"date": settings.today.isoformat(), "outcomes": sorted(t or "" for t in now_tokens),
                    "rules": [f.key for f in now], "primary": now_primary.rule_id if now_primary else None},
        "changed_rules": changes,
        "releases": [{"id": rs.id, "effective_from": rs.effective_from.isoformat(), "reason": rs.reason} for rs in releases],
        "summary": summary,
    }

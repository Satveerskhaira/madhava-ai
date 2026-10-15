"""Deterministic rule evaluation with CEL (Common Expression Language).

A rule's condition (and, for pricing rules, its amount) is evaluated against
`claim` and `plan` maps built only from the paths the rule declares. Missing
values are left out of the maps, so conditions use `has()` for optional
inputs; a condition that needs a missing value fails to evaluate and the
finding is reported as unverified rather than guessed.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any

import celpy
from celpy.evaluation import CELEvalError

from ..models import Evidence, RuleCard

_PLACEHOLDER = re.compile(r"\{\{\s*([\w.]+)(?:\|(\w+))?\s*\}\}")


@dataclass
class Evaluation:
    fired: bool | None              # None when the condition could not be evaluated
    amount: float | None = None
    actual: float | None = None
    error: str | None = None


class CelEvaluator:
    def __init__(self) -> None:
        self._env = celpy.Environment()
        self._programs: dict[str, Any] = {}
        self._lock = threading.Lock()

    def compile(self, expr: str) -> Any:
        with self._lock:
            prog = self._programs.get(expr)
            if prog is None:
                prog = self._env.program(self._env.compile(expr))
                self._programs[expr] = prog
            return prog

    @staticmethod
    def activation(inputs: list[str], values: dict[str, Evidence]) -> dict[str, Any]:
        roots: dict[str, dict[str, Any]] = {"claim": {}, "plan": {}}
        for path in inputs:
            parts = path.split(".")
            node = roots[parts[0]]
            for part in parts[1:-1]:
                node = node.setdefault(part, {})
            ev = values.get(path)
            if ev is not None and ev.present and ev.value is not None:
                node[parts[-1]] = ev.value
        return {name: celpy.json_to_cel(tree) for name, tree in roots.items()}

    def _run(self, expr: str, act: dict[str, Any]) -> Any:
        result = self.compile(expr).evaluate(act)
        if isinstance(result, CELEvalError):
            raise result
        return result

    def evaluate(self, card: RuleCard, values: dict[str, Evidence]) -> Evaluation:
        act = self.activation(card.inputs, values)
        try:
            fired = bool(self._run(card.condition, act))
        except (CELEvalError, TypeError, ValueError) as exc:
            missing = [p for p in card.inputs if p not in values or not values[p].present]
            detail = f"missing inputs {missing}" if missing else str(exc)
            return Evaluation(fired=None, error=f"condition could not be evaluated: {detail}")
        result = Evaluation(fired=fired)
        if fired and card.outcome.type == "pricing" and card.outcome.amount:
            try:
                result.amount = round(float(self._run(card.outcome.amount, act)), 2)
            except (CELEvalError, TypeError, ValueError) as exc:
                result.error = f"amount could not be computed: {exc}"
        if card.outcome.actual:
            ev = values.get(card.outcome.actual)
            if ev is not None and ev.present and ev.value is not None:
                result.actual = round(float(ev.value), 2)
        return result


def _fmt(value: Any, filt: str | None) -> str:
    if value is None:
        return "unknown"
    if filt == "money":
        return f"{float(value):,.2f}"
    if filt == "list" or isinstance(value, (list, tuple)):
        return ", ".join(_fmt(v, None) for v in value) or "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def render(template: str, values: dict[str, Evidence], amount: float | None = None) -> str:
    def sub(m: re.Match[str]) -> str:
        key, filt = m.group(1), m.group(2)
        if key == "amount":
            return _fmt(amount, filt)
        ev = values.get(key)
        return _fmt(ev.value if ev and ev.present else None, filt)

    return " ".join(_PLACEHOLDER.sub(sub, template).split())


def referenced_paths(expr: str) -> set[str]:
    """claim.* / plan.* paths an expression reads, without trailing method calls."""
    out: set[str] = set()
    for m in re.finditer(r"\b((?:claim|plan)(?:\.[A-Za-z_]\w*)+)(\s*\()?", expr):
        path, call = m.group(1), m.group(2)
        if call:                       # e.g. plan.x.exists( -> plan.x
            path = path.rsplit(".", 1)[0]
        out.add(path)
    return out

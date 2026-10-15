"""The AI layer: understanding questions and writing answers, with no SDK dependency.

`DeterministicLanguage` needs no model: intents come from the pack's patterns
(procedures/intents.yaml), concepts from the taxonomy, answers from rule
templates. `ModelLanguage` takes any async completion function
`complete(system, user) -> str`, so it works with any model client or agent
SDK (see rulegraph.adapters for wrappers). Model output is never trusted blindly:

- understanding: only known intents and taxonomy concepts are kept, merged with
  the deterministic reading
- narration: the text must cite the primary rule and only rules present in the
  findings, otherwise the template answer is used
"""

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable, Protocol

from ..memory.taxonomic import Taxonomy
from ..models import Ask

INTENTS = ["explain_outcome", "explain_cost", "what_if_approved", "compare_current", "define_term"]
_RULE_ID = re.compile(r"\bR-[A-Z0-9]+(?:-[A-Z0-9]+)*\b")

Completion = Callable[[str, str], Awaitable[str]]


class Language(Protocol):
    async def understand(self, question: str, record_id: str | None) -> Ask: ...
    async def narrate(self, bundle: dict[str, Any], template_answer: str) -> str: ...


class DeterministicLanguage:
    name = "deterministic"

    def __init__(self, taxonomy: Taxonomy, intents: dict[str, Any] | None = None):
        self.taxonomy = taxonomy
        spec = intents or {}
        self.patterns: dict[str, str] = spec.get("patterns", {})
        self.cost_words: str = spec.get("cost_words", r"cost|price|amount|\$")

    async def understand(self, question: str, record_id: str | None) -> Ask:
        q = question.lower()
        intents = [name for name, pat in self.patterns.items() if re.search(pat, q)]
        if record_id and "define_term" in intents:
            intents.remove("define_term")
        if "what_if_approved" in intents and "explain_cost" in intents:
            intents.remove("explain_cost")
        if "compare_current" in intents and "explain_cost" in intents and not re.search(self.cost_words, q):
            intents.remove("explain_cost")   # "would it still pay today?" is about the outcome, not the amount
        concepts = self.taxonomy.match_text(question)
        return Ask(question=question, record_id=record_id, intents=intents, concepts=concepts,
                   terms=re.findall(r"[A-Za-z][\w-]+", question), source=self.name)

    async def narrate(self, bundle: dict[str, Any], template_answer: str) -> str:
        return template_answer


class ModelLanguage(DeterministicLanguage):
    """Uses any completion function for understanding and narration, with guardrails."""

    name = "model"

    def __init__(self, taxonomy: Taxonomy, intents: dict[str, Any] | None, complete: Completion):
        super().__init__(taxonomy, intents)
        self.complete = complete

    async def understand(self, question: str, record_id: str | None) -> Ask:
        base = await super().understand(question, record_id)
        system = (
            "Classify a question about a decision record. Reply with JSON only: "
            '{"intents": [...], "concepts": [...]}. '
            f"Allowed intents: {INTENTS}. Allowed concepts: {sorted(self.taxonomy.concepts)}."
        )
        try:
            data = json.loads(_strip_fence(await self.complete(system, question)))
        except Exception:  # noqa: BLE001 - any model failure falls back to the deterministic reading
            return base
        intents = [i for i in data.get("intents", []) if i in INTENTS]
        concepts = [c for c in data.get("concepts", []) if c in self.taxonomy.concepts]
        base.intents = list(dict.fromkeys([*base.intents, *intents]))
        base.concepts = list(dict.fromkeys([*base.concepts, *concepts]))
        base.source = "model+deterministic"
        return base

    async def narrate(self, bundle: dict[str, Any], template_answer: str) -> str:
        system = (
            "You explain decision outcomes. Use only the findings provided. Cite every rule you rely on "
            "as [RULE-ID vN]. Lead with the primary reason. If something is not explained, say so "
            "plainly. Never guess, never add rules or values not given."
        )
        try:
            text = await self.complete(system, json.dumps(bundle, default=str))
        except Exception:  # noqa: BLE001
            return template_answer
        allowed = set(bundle.get("rule_ids", []))
        cited = set(_RULE_ID.findall(text))
        primary = bundle.get("primary_rule")
        if not text or not cited <= allowed or (primary and primary not in cited):
            return template_answer
        return text


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return text

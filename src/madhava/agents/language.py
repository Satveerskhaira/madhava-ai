"""The AI layer: understanding questions and writing answers.

`DeterministicLanguage` needs no model: intents come from patterns, concepts
from the taxonomy, and answers from rule templates. `ModelLanguage` uses any
ADK model (Gemini by name, or LiteLLM for other providers such as Claude
through your gateway). Model output is never trusted blindly:

- understanding: only known intents and taxonomy concepts are kept, merged
  with the deterministic reading
- narration: the text must cite the primary rule and only rules present in
  the findings, otherwise the template answer is used
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from ..memory.taxonomic import Taxonomy
from ..models import Ask

INTENTS = ["explain_outcome", "explain_cost", "what_if_approved", "compare_current", "define_term"]

_PATTERNS = {
    "what_if_approved": r"\bif (it|this|they|the claim)?\s*(were|was|is|gets?|had been)?\s*approved\b|\bonce approved\b|\bafter approval\b|\bif (pa|prior auth\w*) (is|were|was) (approved|granted)",
    "compare_current": r"\b(today|right now|currently|current rules|still|anymore|any ?more|now)\b|\bwhat changed\b|\bwould (it|this) (still )?(pay|reject)",
    "explain_cost": r"\b(pay|paid|cost|costs|copay|co-pay|coinsurance|deductible|price|priced|charged|owe|\$\s?\d)",
    "explain_outcome": r"\b(why|reason|explain|reject\w*|denied|deny|declined|not covered|isn't covered|wasn't covered)\b",
    "define_term": r"^\s*(what (is|does|are)|define|meaning of)\b",
}
_RULE_ID = re.compile(r"\bR-[A-Z]+(?:-[A-Z0-9]+)*-\d{3}\b")


class Language(Protocol):
    async def understand(self, question: str, claim_id: str | None) -> Ask: ...
    async def narrate(self, bundle: dict[str, Any], template_answer: str) -> str: ...


class DeterministicLanguage:
    name = "deterministic"

    def __init__(self, taxonomy: Taxonomy):
        self.taxonomy = taxonomy

    async def understand(self, question: str, claim_id: str | None) -> Ask:
        q = question.lower()
        intents = [name for name, pat in _PATTERNS.items() if re.search(pat, q)]
        if claim_id and "define_term" in intents:
            intents.remove("define_term")
        if "what_if_approved" in intents and "explain_cost" in intents:
            intents.remove("explain_cost")
        if ("compare_current" in intents and "explain_cost" in intents
                and not re.search(r"copay|co-pay|coinsurance|deductible|cost|price|owe|\$", q)):
            intents.remove("explain_cost")   # "would it still pay today?" is about the outcome, not cost
        concepts = self.taxonomy.match_text(question)
        return Ask(question=question, claim_id=claim_id, intents=intents, concepts=concepts,
                   terms=re.findall(r"[A-Za-z][\w-]+", question), source=self.name)

    async def narrate(self, bundle: dict[str, Any], template_answer: str) -> str:
        return template_answer


class ModelLanguage(DeterministicLanguage):
    """Uses an ADK model for understanding and narration, with guardrails."""

    name = "model"

    def __init__(self, taxonomy: Taxonomy, model: Any):
        super().__init__(taxonomy)
        from google.adk.models import BaseLlm, LLMRegistry
        self.model: BaseLlm = model if isinstance(model, BaseLlm) else LLMRegistry.new_llm(model)

    async def _complete(self, system: str, user: str) -> str:
        from google.adk.models import LlmRequest
        from google.genai import types

        request = LlmRequest(
            model=getattr(self.model, "model", None),
            contents=[types.Content(role="user", parts=[types.Part(text=user)])],
            config=types.GenerateContentConfig(system_instruction=system, temperature=0),
        )
        text = ""
        async for response in self.model.generate_content_async(request):
            if response.content and response.content.parts:
                text += "".join(p.text or "" for p in response.content.parts)
        return text.strip()

    async def understand(self, question: str, claim_id: str | None) -> Ask:
        base = await super().understand(question, claim_id)
        system = (
            "Classify a question about a pharmacy claim. Reply with JSON only: "
            '{"intents": [...], "concepts": [...]}. '
            f"Allowed intents: {INTENTS}. Allowed concepts: {sorted(self.taxonomy.concepts)}."
        )
        try:
            data = json.loads(_strip_fence(await self._complete(system, question)))
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
            "You explain pharmacy claim outcomes to a care team. Use only the findings provided. "
            "Cite every rule you rely on as [RULE-ID vN]. Lead with the primary reason. If something "
            "is not explained, say so plainly. Never guess, never add rules or values not given."
        )
        try:
            text = await self._complete(system, json.dumps(bundle, default=str))
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

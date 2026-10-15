"""Semantic memory: searchable knowledge about rules, fields, concepts and codes.

Lexical BM25 with taxonomy synonym expansion works offline and is the default.
Pass an `embed` function to add vector similarity (hybrid scoring); in
production the same documents live in MongoDB with Atlas Vector Search.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .taxonomic import Taxonomy

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "of", "to", "and", "or", "is", "was", "be", "for", "on", "in", "this", "that",
    "it", "with", "when", "why", "what", "did", "does", "do", "my", "i", "claim", "drug", "plan",
    "not", "no", "are", "as", "at", "by", "has", "have", "if", "its", "from", "would", "could",
}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


@dataclass
class Document:
    id: str
    kind: str                 # rule | field | concept | code | policy
    text: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Hit:
    doc: Document
    score: float


class SemanticMemory:
    def __init__(self, taxonomy: Taxonomy, embed: Callable[[str], list[float]] | None = None):
        self.taxonomy = taxonomy
        self.embed = embed
        self.docs: list[Document] = []
        self._tf: list[Counter[str]] = []
        self._df: Counter[str] = Counter()
        self._vectors: list[list[float]] = []

    def add(self, docs: Iterable[Document]) -> None:
        for d in docs:
            tokens = tokenize(d.text)
            self.docs.append(d)
            self._tf.append(Counter(tokens))
            self._df.update(set(tokens))
            if self.embed:
                self._vectors.append(self.embed(d.text))

    def _expand(self, query: str) -> list[str]:
        terms = tokenize(query)
        for cid in self.taxonomy.match_text(query):
            for syn in self.taxonomy.synonyms(cid):
                terms.extend(tokenize(syn))
        return terms

    def search(self, query: str, kinds: set[str] | None = None,
               where: Callable[[Document], bool] | None = None, k: int = 10) -> list[Hit]:
        if not self.docs:
            return []
        terms = self._expand(query)
        n = len(self.docs)
        avg_len = sum(sum(tf.values()) for tf in self._tf) / n
        k1, b = 1.4, 0.75
        qvec = self.embed(query) if self.embed else None
        hits: list[Hit] = []
        for i, d in enumerate(self.docs):
            if kinds and d.kind not in kinds:
                continue
            if where and not where(d):
                continue
            tf = self._tf[i]
            length = sum(tf.values()) or 1
            score = 0.0
            for t in set(terms):
                if t not in tf:
                    continue
                idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                f = tf[t]
                score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * length / avg_len))
            if qvec is not None:
                score = 0.5 * score + 0.5 * _cosine(qvec, self._vectors[i]) * 5
            if score > 0:
                hits.append(Hit(d, score))
        hits.sort(key=lambda h: -h.score)
        return hits[:k]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)

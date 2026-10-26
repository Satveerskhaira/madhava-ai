"""The rules/policy graph and its search.

Nodes: taxonomy concepts, outcomes, rules, catalog fields and policy documents.
Edges come from the taxonomy (broader, related, classifies), rule cards
(explains, in_domain, reads, overridden_by, derived_from) and the field
catalog (describes). Every edge is checked against the ontology when built.

Search is a best-first walk from weighted seeds (outcome tokens, concepts in
the question, rules suggested by text search). A rule's score is the sum over
seeds of the best path product, so a rule reached from several independent
seeds ranks higher. Each hit keeps the path that reached it, which is how an
answer can say why a rule was considered.
"""

from __future__ import annotations

import heapq
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from ..memory.taxonomic import Taxonomy
from ..ontology import Ontology
from .store import RuleStore

NODE_CLASS = {"concept": "Concept", "outcome": "Outcome", "rule": "Rule", "field": "Field", "policy": "Policy",
              "ruleset": "RuleSet", "term": "Term"}

# Edges kept for the knowledge graph (impact analysis, export, browsing) but not followed by rule search,
# so adding them never changes which rules an agent finds: they have no walk weight.
STRUCTURAL = ("depends_on", "member_of", "supersedes", "about")

# Walk weights per (relation, direction). "out" follows the relation as declared.
WEIGHTS: dict[tuple[str, str], float] = {
    ("explains", "in"): 1.0,         # outcome -> rules that explain it
    ("classifies", "in"): 1.0,       # outcome -> concept that classifies it
    ("classifies", "out"): 0.6,      # concept -> outcomes it classifies
    ("explains", "out"): 0.4,        # rule -> outcome
    ("in_domain", "in"): 0.9,        # concept -> its rules
    ("in_domain", "out"): 0.5,       # rule -> its concept
    ("broader", "in"): 0.85,         # concept -> narrower concepts
    ("broader", "out"): 0.5,         # concept -> broader concept
    ("related", "out"): 0.6,
    ("related", "in"): 0.6,
    ("overridden_by", "out"): 0.7,   # rule -> its overrides
    ("overridden_by", "in"): 0.5,    # override -> the rules it overrides
    ("reads", "out"): 0.3,
    ("reads", "in"): 0.6,            # field -> rules that read it
    ("describes", "out"): 0.6,       # field -> concept
    ("describes", "in"): 0.3,
    ("derived_from", "out"): 0.3,
    ("derived_from", "in"): 0.6,     # policy document -> rules derived from it
}
MAX_DEPTH = 4
MIN_SCORE = 0.05


@dataclass
class Seed:
    node: str
    weight: float
    reason: str


@dataclass
class RuleHit:
    rule_id: str
    score: float
    path: list[str] = field(default_factory=list)
    seeds: list[str] = field(default_factory=list)


class PolicyGraph:
    def __init__(self, ontology: Ontology):
        self.ontology = ontology
        self.adj: dict[str, list[tuple[str, str, str]]] = defaultdict(list)   # node -> (relation, direction, other)
        self.nodes: set[str] = set()
        self.edge_count = 0

    @staticmethod
    def node_class(node: str) -> str:
        return NODE_CLASS[node.split(":", 1)[0]]

    def add_edge(self, src: str, relation: str, dst: str) -> None:
        self.ontology.check(relation, self.node_class(src), self.node_class(dst))
        if (relation, "out", dst) in self.adj[src]:
            return
        self.adj[src].append((relation, "out", dst))
        self.adj[dst].append((relation, "in", src))
        self.nodes.update((src, dst))
        self.edge_count += 1

    @classmethod
    def build(cls, ontology: Ontology, taxonomy: Taxonomy, store: RuleStore,
              fields: Iterable[tuple[str, str]], *, field_params: dict[str, list[str]] | None = None,
              policies: Iterable[str] = (), terms: Iterable[tuple[str, list[str]]] = ()) -> "PolicyGraph":
        """Build from the taxonomy, rule cards and catalog; structural edges are added when the ontology
        declares their relation (depends_on, member_of, supersedes, about)."""
        g = cls(ontology)
        declared = set(ontology.relations)
        for c in taxonomy.concepts.values():
            if c.broader:
                g.add_edge(f"concept:{c.id}", "broader", f"concept:{c.broader}")
            for r in c.related:
                if c.id < r:
                    g.add_edge(f"concept:{c.id}", "related", f"concept:{r}")
            for n in c.notations:
                g.add_edge(f"concept:{c.id}", "classifies", f"outcome:{n}")
        for path, concept in fields:
            g.add_edge(f"field:{path}", "describes", f"concept:{concept}")
        # Edges are the union over rule versions; version filtering happens at search time.
        for card in store.rules.values():
            rule = f"rule:{card.id}"
            g.add_edge(rule, "in_domain", f"concept:{card.concept}")
            if card.outcome.token:
                g.add_edge(rule, "explains", f"outcome:{card.outcome.token}")
            for p in card.inputs:
                g.add_edge(rule, "reads", f"field:{p}")
            for o in card.overridden_by:
                g.add_edge(rule, "overridden_by", f"rule:{o}")
            for ref in card.source_refs:
                doc = ref.doc + (f" / {ref.section}" if ref.section else "")
                g.add_edge(rule, "derived_from", f"policy:{doc}")
        for key in policies:                                   # sections no rule cites yet still appear
            g.nodes.add(f"policy:{key}")
        if "depends_on" in declared:
            for path, sources in (field_params or {}).items():
                for src in sources:
                    g.add_edge(f"field:{path}", "depends_on", f"field:{src}")
        if "member_of" in declared:
            for info in store.domains.values():
                for rs in info.releases:
                    for key in rs.rules:
                        g.add_edge(f"rule:{key.split('@')[0]}", "member_of", f"ruleset:{rs.id}")
                    if rs.previous and "supersedes" in declared:
                        g.add_edge(f"ruleset:{rs.id}", "supersedes", f"ruleset:{rs.previous}")
        if "about" in declared:
            for term_id, concepts in terms:
                for c in concepts:
                    if c in taxonomy.concepts:
                        g.add_edge(f"term:{term_id}", "about", f"concept:{c}")
        return g

    def edges(self) -> list[tuple[str, str, str]]:
        """Every edge once, as (source, relation, target)."""
        return [(src, rel, dst) for src, outs in self.adj.items() for rel, d, dst in outs if d == "out"]

    # --------------------------------------------------------------- search
    def _readers(self, field_node: str) -> int:
        return sum(1 for rel, direction, _ in self.adj.get(field_node, []) if rel == "reads" and direction == "in")

    def _walk(self, seed: Seed) -> dict[str, tuple[float, list[str]]]:
        best: dict[str, tuple[float, list[str]]] = {seed.node: (seed.weight, [seed.node])}
        heap: list[tuple[float, int, str, list[str]]] = [(-seed.weight, 0, seed.node, [seed.node])]
        while heap:
            neg, depth, node, path = heapq.heappop(heap)
            score = -neg
            if score < best.get(node, (0.0, []))[0] or depth >= MAX_DEPTH:
                continue
            for relation, direction, other in self.adj.get(node, []):
                w = WEIGHTS.get((relation, direction), 0.0)
                if (relation, direction) == ("reads", "in"):
                    # A field read by many rules (drug name, DOS) says little about any one rule.
                    w /= max(1, self._readers(node)) ** 0.5
                s = score * w
                if s < MIN_SCORE or s <= best.get(other, (0.0, []))[0]:
                    continue
                arrow = f"-{relation}->" if direction == "out" else f"<-{relation}-"
                new_path = [*path, arrow, other]
                best[other] = (s, new_path)
                heapq.heappush(heap, (-s, depth + 1, other, new_path))
        return best

    def search(self, seeds: list[Seed], allowed_rules: set[str] | None = None,
               scope_concepts: set[str] | None = None, rule_concept: dict[str, str] | None = None,
               k: int = 10) -> list[RuleHit]:
        totals: dict[str, RuleHit] = {}
        best_part: dict[str, float] = {}
        for seed in seeds:
            if seed.node not in self.nodes:
                continue
            for node, (score, path) in self._walk(seed).items():
                if not node.startswith("rule:"):
                    continue
                rid = node.split(":", 1)[1]
                if allowed_rules is not None and rid not in allowed_rules:
                    continue
                if scope_concepts is not None and rule_concept and rule_concept.get(rid) not in scope_concepts:
                    continue
                hit = totals.setdefault(rid, RuleHit(rid, 0.0))
                hit.score += score
                hit.seeds.append(seed.reason)
                if score > best_part.get(rid, 0.0):
                    best_part[rid] = score
                    hit.path = path
        ranked = sorted(totals.values(), key=lambda h: -h.score)
        return ranked[:k]

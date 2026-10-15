# Adapting a domain (replacing the example pack)

Work one domain agent at a time; keep `validate`, `eval` and tests green between steps.

1. **pack.yaml**: name, `agent_prefix`, `roots`, `nouns`, `codes_kind`, `rules_modes` labels, `token_labels`, phrases in the domain's words.
2. **Ontology**: rename the record and reference classes; keep the graph classes and relations.
3. **Codes**: the official outcome code list (quote numeric codes).
4. **Taxonomy**: one top concept per domain agent with `precedence` in engine order; leaves per rule domain with `notations` and `altLabels`; `related` where codes overlap.
5. **Fixtures**: a few records per agent in the record API's shape (reasons, amounts, dates), and reference data with at least two effective-dated versions to exercise rule history.
6. **Record adapter** (`record.py`): status, `adverse`, outcome tokens, amount fields, reference id, dates, derived fields. Declare derived fields in the catalog.
7. **Catalog**: every field a rule reads, with ontology entity and taxonomy concept; JSON Pointers and params for reference fields.
8. **Rule domains and cards**: start with the rules behind the most frequent outcome codes. Each card cites its source document. Add overrides as their own cards.
9. **Procedures**: per-agent investigator notes; adjust domain planner loops only if goldens need it.
10. **Domain goldens**: per agent, its own outcome (confirmed), an amount, an out-of-scope token, and a case it must leave as a gap.
11. **Remove the example**: delete leftover example concepts, rules, fixtures and goldens; run `validate`.
12. **Core**: update `planner.yaml` speculative agents (the agent owning amounts for `what_if_approved`), core goldens across domains, and the exposure adapter.
13. **Real APIs**: switch the toolbox to `HttpRecordApi` / `HttpReferenceApi` (path templates, token from environment) or implement `get_record`, `get_as_of`, `list_versions`.

Ask the domain experts to review taxonomy labels, decision order, every rule card's intent and template, and the golden answers. Their sign-off is what makes the goldens golden.

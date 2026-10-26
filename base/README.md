# pharmacy-base

The shared base for the pharmacy claim domain agents. It holds what every domain shares; each domain agent ships in its own package as a **knowledge fragment** that extends it.

| In the base | In each domain package |
|---|---|
| Manifest: roots (`claim`, `plan`), nouns, answer wording | Taxonomy concepts under its top concept |
| Ontology, reject code table | Catalog fields only its rules read |
| Top-level taxonomy: one concept per domain agent, with adjudication order | Rule domains, releases and rule cards |
| Shared catalog fields (read by several domains) | Investigator notes for its agent |
| Domain planner procedures, investigator playbook, intent patterns | Domain goldens, tests, CI |
| Glossary scheme and shared terms; shared policy sections | Its own glossary terms and the policy sections its rules cite |
| Claim adapter, claims and plan setup API toolbox, sample fixtures | Entry point in `rulegraph.domains` |

`build()` composes the base with every installed domain package (`rulegraph.discover`), or with the ones you pass, and refuses conflicts (the same concept, field, notation or rule domain defined twice). Knowledge loads through sources, so the base and any fragment can later come from MongoDB instead of files without changing the agents.

```bash
pharmacy-domains agents                                   # installed domain agents
pharmacy-domains validate                                 # composed base + installed fragments
pharmacy-domains eval goldens/domain_goldens.yaml         # a domain repo's goldens
pharmacy-domains ask utilization_management RX-1001 "Why was this rejected?" --trace
pharmacy-domains view                                     # offline browser of the installed knowledge
pharmacy-domains export graph --format ttl                # standard RDF export
```

Lives in the madhava-ai repository for now (`base/`), installed with `pip install "pharmacy-base @ git+https://github.com/Satveerskhaira/madhava-ai.git@<commit>#subdirectory=base"`.

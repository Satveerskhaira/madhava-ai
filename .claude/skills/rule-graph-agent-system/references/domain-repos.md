# One repository per domain

Use when domains have different owners or release cadences. Each domain agent ships as its own package, with its own goldens and CI; the core discovers whichever domain packages are installed.

## What goes where

| Base package (shared, one per record API) | Domain package (one per domain agent) |
|---|---|
| `pack.yaml` with `kind: base`: roots, nouns, wording, rules-mode labels | `pack.yaml` with `kind: fragment`, `extends: <base name>`, `agents: [<agent>]` |
| Ontology, code table | Taxonomy concepts under its top concept (leaves with `rule_domain`, `notations`) |
| Taxonomy scheme + top concepts (one per agent, with `agent` and `precedence`) | Catalog fields only its rules read |
| Catalog fields read by several domains, or by none; their `concept` is a top concept | Rule domains (`domain.yaml`, releases, rule cards) |
| Domain planner procedures, investigator playbook (`default`), intents | Investigator notes for its agent (`agents:` only) |
| Record adapter, toolbox (record and reference APIs), fixtures, CLI | Domain goldens, tests, CI, README |

Decision order stays central (top concepts in the base) because precedence across domains decides the primary reason. The base must validate on its own (`Knowledge.compose([base])`), so nothing in it may point at a fragment's concepts.

A fragment may keep `related` links to concepts in other domains (for example quantity limits ~ specialty channel). When that domain is not installed, the link is kept aside (`taxonomy.unresolved_related`) and followed once it is.

## Composition rules (`rulegraph.compose`)

Exactly one base; every fragment must `extends` it. Composition fails, listing every conflict, when two packs define the same concept, rule domain or notation, the same field differently, or the same code with different meanings; or when a fragment sets base-only parts (taxonomy scheme, domain planner procedures, intents, the investigator `default`). Identical duplicate fields are allowed.

## Domain package layout

```
<domain-repo>/
  pyproject.toml            deps: rulegraph, <base>; entry point below; package-data pack/**/*.yaml
  requirements-dev.txt      kernel and base pinned (git URL with #subdirectory=..., or internal index)
  src/<pkg>/__init__.py     pack_dir(), package = DomainPackage(...), build()
  src/<pkg>/pack/           pack.yaml, taxonomy/concepts.yaml, catalog/fields.yaml, domains/, procedures/investigator.yaml
  goldens/domain_goldens.yaml
  tests/                    composes with the base alone and validates; goldens; delegated task; gap round; card
  .github/workflows/ci.yml  <base CLI> validate, eval goldens, pytest
```

```toml
[project.entry-points."rulegraph.domains"]
utilization_management = "pharmacy_um:package"
```

```python
from rulegraph import DirectorySource, DomainPackage

package = DomainPackage(name="pharmacy-um", source=DirectorySource(pack_dir()),
                        agents=["utilization_management"], version=__version__)

def build(settings=None, **kwargs):          # this domain alone on the base
    from pharmacy_base import build as base_build
    return base_build(settings, fragments=[package], **kwargs)
```

The base's `build(fragments=None)` composes the base with `rulegraph.discover(extends=<base name>)` (every installed domain package) or with the fragments given; `domain_agents` builds an agent only for domains whose rules are loaded.

## Core

```python
packages = discover(extends=BASE_NAME)                       # installed domain packages
kn = Knowledge.compose([base_source(), *[p.source for p in packages]], planner_procedures=CORE_PROCEDURES)
domain = base_build(settings, fragments=packages, knowledge=kn, episodic=episodic)
core = core_planner(kn, domain.toolbox, domain.agents, episodic=episodic)
```

Adding a domain to the core is installing its package and adding cross-domain goldens; no core code changes. Pin every domain package in the core's requirements so a release is reproducible.

## Knowledge sources: files now, a database later

Packs load through `KnowledgeSource.load() -> PackDocuments` (plain dicts: manifest, scheme, concepts, ontology, fields, codes, domains with their rules, procedures, intents). `DirectorySource` reads the Git layout; `DocumentSource` takes documents already loaded. A database-backed source (for example MongoDB collections for concepts, fields, codes, rule domains, rule cards and procedures, filtered by pack name) only has to return the same `PackDocuments`; composition, validation and the agents do not change. Keep Git as the authoring and review path and publish to the database, so CI still validates every change.

## Splitting a single pack

1. List each rule's inputs (`record_inputs`, `reference_inputs`, pricing `actual`) per domain agent.
2. A field read only by one agent, whose concept is in that agent's branch and whose `params` sources are not owned by another domain, goes to that domain; every other field goes to the base, with its `concept` set to its top concept.
3. Top concepts and the scheme to the base; every other concept to its agent's fragment.
4. Rule domain folders to their agent's fragment; investigator `default` to the base, `agents.<name>` notes to each fragment.
5. Domain goldens to the domain that owns each agent; cross-domain goldens stay with the core.
6. Check: base alone validates; each fragment composes with the base alone and validates; all together compose with no conflicts and give the same graph size and golden results as before.

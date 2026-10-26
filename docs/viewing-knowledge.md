# Viewing and exporting the knowledge

Everything the agents know is browsable and exportable on your own machine: the knowledge graph, the taxonomy, the ontology, the glossary, rules, fields, policy text and outcomes. Nothing needs a server or an internet connection.

## 1. The knowledge browser (start here)

```bash
pip install -r requirements-dev.txt     # once
madhava view                            # writes knowledge.html and opens it in your browser
madhava view --out ~/Desktop/pharmacy-knowledge.html --no-open
```

One self-contained HTML file (no CDN, no server), so it works offline and can be emailed or attached to a ticket. The tabs:

| Tab | What you see |
|---|---|
| **Overview** | Counts, decision order (domain agents by precedence with their rule domains and outcomes), edges by relation, rule set changes |
| **Graph** | The whole knowledge graph, one sector per domain agent and shared knowledge in the middle. Filter by node kind or relation, drag to pan, scroll to zoom, click a node to highlight its neighbours |
| **Taxonomy** | The SKOS tree: top concepts (domain agents) in decision order, leaves with rule domains, outcome codes and rules |
| **Ontology** | The schema diagram (knowledge-graph classes on top, the claim data model below) and the class and relation tables |
| **Glossary** | Terms and abbreviations with definitions and the concepts they are about; optionally the code meanings |
| **Rules** | Rule cards by agent and rule domain. Open one for every version, its CEL condition, the fields it reads, overrides, rule sets, wording and the policy text it comes from |
| **Fields** | The field catalog: record paths, JSON Pointers into the plan setup payload, parameters, and which rules read each field |
| **Policies** | Source policy sections with their text and the rules derived from them (sample text in the reference implementation) |
| **Outcomes** | Every reject code and price component, the concept that classifies it and the rules that explain it; outcomes with no rule are visible gaps |

Every coloured name opens a detail panel with links to what it connects to. The search box finds terms, abbreviations, concepts, rules, fields and policy text.

A snapshot of the page is committed at [`docs/knowledge.html`](knowledge.html): download it and open it in any browser. It reflects the knowledge when it was last regenerated; refresh it with `madhava view --no-open --title "Pharmacy claims knowledge" --out docs/knowledge.html` when rules change.

Other ways to open it:

```bash
pharmacy-domains view                    # only the domain packages installed (e.g. inside one domain's repo)
rulegraph --pack path/to/base --fragment path/to/fragment view    # any pack on disk, no Python package needed
```

### Live from MongoDB

```bash
madhava publish --uri mongodb://127.0.0.1:27017 --release current
madhava knowledge-service --mongo mongodb://127.0.0.1:27017 --open     # http://127.0.0.1:8300/
```

The same page, reading the knowledge service API instead of embedded data, with a release picker. `scripts/run-local.sh` sets all of this up (MongoDB in Docker, two releases, the service). The API, the stored formats and how each tab is rendered are in [knowledge-application.md](knowledge-application.md).

## 2. Standard exports, for other tools

```bash
madhava export graph --format ttl --out knowledge.ttl      # whole knowledge graph as RDF Turtle
madhava export graph --out knowledge.jsonld                 # the same as JSON-LD
madhava export taxonomy --out taxonomy.jsonld               # SKOS concept scheme
madhava export glossary --out glossary.jsonld               # SKOS glossary scheme
madhava export ontology --out ontology.jsonld               # OWL classes and properties
madhava export shapes --out shapes.ttl                      # SHACL shapes the graph must satisfy
madhava export graphml --out knowledge.graphml              # graph for yEd, Gephi or Cytoscape
madhava shacl                                               # validate the graph against the shapes
```

| File | Open it with | Good for |
|---|---|---|
| `knowledge.ttl` | **Protégé** (free, desktop): File → Open. Or any RDF store (GraphDB Free, Apache Jena Fuseki) for SPARQL queries | Browsing classes and individuals, SPARQL questions such as "which rules read a field whose concept is cost share?" |
| `taxonomy.jsonld`, `glossary.jsonld` | Protégé, or a SKOS editor such as VocBench | Reviewing and editing vocabularies with SMEs |
| `knowledge.graphml` | **yEd** (free, desktop): Layout → Organic; or **Gephi**; or **Cytoscape** | Large printable diagrams, custom layouts and filters |
| `shapes.ttl` | Any SHACL engine (`pyshacl`, TopBraid, GraphDB) | Checking the graph in tools outside this repo |
| `knowledge.jsonld` | Any JSON viewer or editor | Reading the raw graph |

The exported graph uses the pack ontology's namespace (`https://example.org/pharmacy/ontology#`) for classes and properties, SKOS for taxonomy and glossary, and PROV-O for sources (`prov:wasDerivedFrom` from each rule version to its policy section, `prov:wasRevisionOf` between versions).

## 3. The knowledge files in your editor

The YAML files are the source. JSON Schemas for every file type give you completion and error checking while you type:

```bash
rulegraph schemas --out .schemas         # pack, concept, term, ontology, field, rule-domain, rule-card, policy, ...
```

In VS Code with the YAML extension, map them in `.vscode/settings.json`:

```json
{
  "yaml.schemas": {
    ".schemas/rule-card.schema.json": "**/domains/*/rules/*.yaml",
    ".schemas/rule-domain.schema.json": "**/domains/*/domain.yaml",
    ".schemas/pack.schema.json": "**/pack.yaml"
  }
}
```

`madhava validate` (and `pharmacy-domains validate` in a domain repo) runs the same schemas plus the cross-file checks: every rule input in the catalog, CEL compiles, releases do not overlap, glossary terms point at real concepts, every cited policy section has text.

## 4. MongoDB

When the knowledge moves to MongoDB, publish and browse it there:

```bash
madhava publish --uri mongodb://localhost:27017 --db knowledge --release current
```

Open the database in **MongoDB Compass**: collections `packs`, `concepts` (taxonomy and glossary), `fields`, `rule_domains`, `rules` (one document per rule version), `policies`, and `releases` (which pack versions the application loads). The application reads a release with `rulegraph.adapters.mongodb.mongo_sources(db, "current")`; the agents and the browser work the same either way.

## Where each piece lives

| Knowledge | File |
|---|---|
| Ontology | `base/src/pharmacy_base/pack/ontology/pharmacy.ontology.yaml` |
| Taxonomy top concepts, decision order | `base/src/pharmacy_base/pack/taxonomy/adjudication.taxonomy.yaml` |
| Taxonomy leaves per domain | madhava-ai-subagent `src/pharmacy_agents/packs/<agent>/taxonomy/concepts.yaml` |
| Glossary: shared terms | `base/src/pharmacy_base/pack/glossary/glossary.yaml` |
| Glossary: domain terms | madhava-ai-subagent `packs/<agent>/glossary/terms.yaml` |
| Policy text | `base/.../pack/policies/policies.yaml` and `packs/<agent>/policies/policies.yaml` |
| Rules and rule sets | `packs/<agent>/domains/<rule_domain>/` |
| Field catalog | `base/.../pack/catalog/fields.yaml` (shared) and `packs/<agent>/catalog/fields.yaml` |
| Reject codes | `base/.../pack/catalog/reject_codes.yaml` |

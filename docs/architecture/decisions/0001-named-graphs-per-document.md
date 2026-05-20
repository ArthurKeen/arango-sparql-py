# ADR-0001: Named graphs encoded as a per-document `_graph` attribute

- **Status:** Accepted
- **Date:** 2026-05-20
- **Owner:** arango-sparql-py
- **Related code:** `arango_sparql/translate/visitor.py::visit_Graph`,
  `arango_sparql/translate/resolver.py::SchemaResolver.graph_field`
- **Supersedes:** —
- **Superseded by:** —

## Context

SPARQL 1.1 datasets are *quads* — `(subject, predicate, object, graph)` — and
the `GRAPH <iri> { … }` and `GRAPH ?g { … }` constructs let queries
scope triple patterns to (or surface) the named-graph component.

The legacy Foxx service `arango-sparql` has **zero** named-graph support
(no `GRAPH` handling in `pgt-translator.js` or `rpt-translator.js`).
There is no porting recipe to consult; this is a net-new design
decision that the modern Python service must own.

ArangoDB does not have a native "named graph per document" notion —
"named graphs" in ArangoDB refer to *topology graphs* over edge
collections, not to RDF named graphs. So we have to pick how to encode
the graph dimension ourselves.

The choice has to work across all three storage layouts the project
already supports or plans to support:

- **PG (Property Graph)** — one collection per RDF class; attributes
  are object values; subjects are documents.
- **LPG (Labeled Property Graph)** — same as PG, plus explicit edge
  collections for object-properties. Topology is first-class.
- **RPT (RDF Predicate Translator)** — one edge collection per RDF
  predicate; triples are first-class edge documents.

## Decision

Encode RDF named graphs as a **per-document `_graph` attribute** on
every document in every collection that participates in SPARQL
translation.

- `_graph: <iri>` (a string) on a document means "this triple/document
  is in the named graph identified by `<iri>`".
- `_graph: null` (or attribute absent) means "this document is in the
  *default graph* of the dataset".
- The attribute name is configurable via
  `SchemaResolver.graph_field` (default `"_graph"`) so deployments
  that already use that name can override.

The `visit_Graph` visitor method is **layout-agnostic**: it pushes a
graph scope onto `_BindingState`, and every triple emission consults
the active scope to decide whether to add `FILTER doc._graph == @g`
(constant graph IRI) or `LET ?g = doc._graph` (variable graph IRI)
to the FOR clause. Whether `doc` came from a PG class collection, an
LPG edge collection, or an RPT predicate collection is irrelevant —
the resolver has already done that work upstream.

## Considered Alternatives

### Alternative B — Per-collection graph membership

Each collection is tagged (in a side registry) with a `graph_iri`.
`GRAPH <iri>` restricts pattern matching to collections whose
`graph_iri` matches; `GRAPH ?g` requires a UNION over every
registered graph.

**Rejected because:**

1. It only works for PG. For LPG, edge collections are typed by
   *topology* (`friend`, `employee`) and naturally hold edges from
   multiple graphs — splitting by graph would shatter the model.
   For RPT, predicate collections are shared across graphs by
   construction — splitting them would explode the predicate
   registry quadratically.
2. `GRAPH ?g { ?s ?p ?o }` would compile to a UNION over every
   registered graph, with each arm constanting the graph IRI as a
   literal. This is O(N graphs) arms per such query — pathological
   for any dataset with more than a handful of named graphs.
3. Real-world ETL pipelines routinely ingest data destined for
   multiple graphs (production, staging, QA, per-tenant, per-source)
   into the same collection. Per-collection mapping forecloses this
   pattern.
4. **Asymmetric reversibility (the decisive factor):**
   Strategy A leaves Strategy B accessible as a future *resolver-side
   optimisation* — when `GRAPH <X>` targets a collection that the
   resolver knows is wholly within graph `<X>`, the per-doc filter
   can be elided. Going the other direction (B → A) requires
   migrating every document. We pick the choice that doesn't
   foreclose its alternative.

### Alternative C — Stub `visit_Graph` and defer

Raise a structured `UnsupportedSparqlError("named graphs require a
storage model")` and ship the design doc only.

**Rejected because:**

- Zero W3C coverage bump.
- The 11 affected W3C tests include `GRAPH ?g { … }` cases that
  cascade into the `subquery`, `property-path`, and `exists`
  groups; deferring blocks parallel improvements in those areas.
- The decision is reversible at low cost (per the reversibility
  argument above); there is no benefit to deferring.

## Consequences

### Positive

- **Layout-uniform translation.** One `visit_Graph` serves PG, LPG,
  and RPT. New layouts (e.g. RDF-star quoted triples) inherit
  named-graph support for free.
- **Quad-semantic alignment.** `(S, P, O, G)` maps 1:1 to
  `(doc.subject_field, predicate-from-context, doc.object_field, doc._graph)`,
  which is how commercial quad-stores physically encode quads.
- **W3C unlock.** 11 currently-XFAIL tests across `subquery`,
  `property-path`, and `exists` become reachable; expected
  +4.4 pp coverage bump.
- **Mixable graphs per collection.** A single physical collection
  can host docs from any number of named graphs, plus the default
  graph, without restructuring.
- **Indexable.** `_graph` is a plain string attribute; standard
  ArangoDB persistent indexes on it work without modification.

### Negative

- **Per-FOR filter cost.** Every triple emission inside a `GRAPH`
  scope adds one `FILTER doc._graph == @g` predicate. Mitigation:
  index `_graph` on every collection that participates in
  named-graph queries.
- **Default-graph semantics edge case.** SPARQL says the default
  graph is "the unnamed graph in the dataset", but the dataset
  declaration determines whether default-graph queries see *only*
  `_graph IS NULL` docs (strict) or *all* docs (lax). Both modes
  are spec-conformant — the spec defers the choice to the dataset.
  For v0.9 we default to **lax**
  (`default_graph_includes_named=True`) so that:
  1. Existing translation goldens (which have no `_graph` filter)
     do not churn.
  2. Legacy data ingested before this attribute existed remains
     queryable without migration.
  3. Deployments that want strict isolation (e.g. a tenant whose
     "default graph" is genuinely empty until populated) can flip
     the knob.
  A future slice may flip the default to strict once the
  live-execution harness lands and we can co-update all goldens
  in one mechanical pass.
- **Schema drift.** Any ingestion path that doesn't write `_graph`
  produces default-graph docs. This is intentional (it's how
  ingestion stays backward-compatible) but operators need to be
  aware.

### Neutral

- The legacy Foxx service had no named-graph support, so there is
  no behavioural divergence to migrate away from.
- W3C live-execution tests will require fixtures that populate
  `_graph` on the documents in named graphs. The translation-only
  harness ignores this — translation produces valid AQL regardless
  of whether the data exists.

## Implementation notes

- `SchemaResolver` gains a `graph_field: str = "_graph"` attribute
  and a `default_graph_includes_named: bool = False` flag.
- `_BindingState` gains a `graph_scope: list[Variable | URIRef]`
  stack — `visit_Graph` pushes on entry, pops on exit.
- Each FOR-emitting visitor (`visit_BGP`, `visit_Path`,
  `emit_path_triple`, `emit_variable_predicate_triple`) consults
  the active graph scope and emits the appropriate filter / LET.
- A future resolver method `should_filter_graph(collection: str,
  graph_iri: str | None) -> bool` will be the seam through which
  Strategy B (per-collection graph optimisation) can layer on.

## References

- SPARQL 1.1 §8.3 "Querying the Dataset" (named-graph semantics)
- W3C DAWG cases impacted: `subquery/sq01-sq05`, `subquery/sq07`,
  `property-path/pp06`, `pp07`, `pp34`, `pp35`,
  `exists/exists02` (per `tests/w3c/COVERAGE_REPORT.md`)
- PRD §6.6 — storage-model decisions row

# Decisions (ADR intel)

Extracted from classified ADRs. Both ADR source files are redirect stubs; the
authoritative decision content lives in `docs/architecture/PRD.md` Appendix B
(the PRD is the declared single source of truth). Precedence: ADR > SPEC > PRD >
DOC. Neither ADR is `locked` — statuses are "Accepted" / "Partially resolved",
not an explicit lock, so neither can produce a LOCKED-vs-LOCKED blocker.

---

## DEC-0001 — Named graphs encoded as a per-document `_graph` attribute

- source: /Users/plosiewicz/dev/arango-sparql-py/docs/architecture/decisions/0001-named-graphs-per-document.md (redirect stub)
- authoritative-content: /Users/plosiewicz/dev/arango-sparql-py/docs/architecture/PRD.md (Appendix B.1)
- status: Accepted (2026-05-20) — not locked
- scope: named graphs, `_graph` attribute, document model, RDF/SPARQL storage
- related-code: `arango_sparql/translate/visitor.py::visit_Graph`, `arango_sparql/translate/resolver.py::SchemaResolver.graph_field`

Decision: Encode RDF named graphs (the SPARQL quad `GRAPH` dimension) as a
per-document `_graph` attribute on every document in every collection that
participates in translation. `_graph: <iri>` = triple is in named graph `<iri>`;
`_graph: null`/absent = default graph. Attribute name configurable via
`SchemaResolver.graph_field` (default `"_graph"`). `visit_Graph` is
layout-agnostic (PG/LPG/RPT): it pushes a graph scope onto `_BindingState` and
each triple emission adds `FILTER doc._graph == @g` (constant IRI) or binds
`?g = doc._graph` (variable IRI).

Rejected alternatives: (B) per-collection graph membership — PG-only, O(N)
UNION explosion, and A→B stays possible later while B→A needs data migration;
(C) stub-and-defer — no coverage gain and blocks cascading work.

Consequences: one `visit_Graph` serves all three layouts; ~11 W3C XFAILs become
reachable (~+4.4pp); default-graph strict-vs-lax defaults to **lax**
(`default_graph_includes_named=True`) with a knob to flip to strict.

Distinct from ArangoDB topology named graphs used for schema down-select (PRD
§6.8) — the two are orthogonal.

---

## DEC-0002 — Cross-subject `OPTIONAL` (LeftJoin) emitter

- source: /Users/plosiewicz/dev/arango-sparql-py/docs/architecture/decisions/0002-cross-subject-optional-leftjoin.md (redirect stub)
- authoritative-content: /Users/plosiewicz/dev/arango-sparql-py/docs/architecture/PRD.md (Appendix B.2)
- status: Partially resolved (2026-05-28; resolutions 2026-06-02) — not locked
- scope: cross-subject OPTIONAL, LeftJoin emitter, MINUS/OPTIONAL interaction, SPARQL query translation
- related-code: `visitor.py::visit_LeftJoin`, `translate/optional_crosssubject.py`, `translate/variable_predicates.py`, `resolver.py::SchemaResolver`

One `visit_LeftJoin` visitor spanning two distinct problems:

- **Problem 1 — cross-subject OPTIONAL** (`?s ?p ?o OPTIONAL {?o ?p2 ?o2}`).
  Difficulty is storage-model-dependent: trivial & spec-correct on RPT (plain
  left-join scan over the triples table), genuinely ambiguous on PG/LPG
  (`_uri → collection` resolution + variable-predicate carve-out).
  - **Option A — RPT-native left-join (SHIPPED 2026-06-02).** Standard
    left-join-via-subquery with `[null]`-pad; spec-correct incl. variable
    predicate; golden-pinned + pyoxigraph cross-validated. Moves the W3C harness
    number by **0** (harness runs Document/PG, not RPT) — a pure correctness
    investment.
  - **Option B — Document/single-collection emulation.** Closes the two W3C
    harness cases (+0.8pp) but inherits the variable-predicate carve-out
    (moves the gap rather than closing it). **Deferred to post-v1.0** (travels
    with the federation slice).
  - **Option C — full multi-model with `_uri → collection` resolution.**
    Correct across all models but disproportionate scope for two tests.
    **Deferred.**

- **Problem 2 — OPTIONAL re-binds an already-bound variable inside MINUS
  (RESOLVED 2026-06-02).** Model-independent §18.2.5.2 conditional-add (compat
  FILTER, not a fresh binding) + §8.3.4 disjoint-domain overlap guard.
  Golden + pyoxigraph parity. Moved W3C query-eval coverage 95.7% → 96.4%.

Net: remaining `visit_LeftJoin` branches keep raising structured
`UnsupportedSparqlError` rather than emitting silently-wrong AQL.

# Product Requirements Document — `arango-sparql-py` v1

**Status**: Draft v1 (supersedes the original v0 transpiler-design memo, which
now lives in [`vision.md`](vision.md) as the inception narrative.)

**Owner**: Arthur Keen

**Audience**: maintainers, contributors, downstream integrators (the ArangoDB
Platform team that consumes this as a BYOC service), and AI agents working on
the repo.

---

## 1. Mission

Replace the legacy JavaScript Foxx [`arango-sparql`](https://github.com/ArthurKeen/arango-sparql)
service with a standalone Python microservice that:

1. **Translates SPARQL 1.1 to ArangoDB AQL** with W3C-grounded correctness.
2. **Exposes a SPARQL endpoint** that conforms to the W3C SPARQL 1.1 Protocol,
   so any standard SPARQL client (Apache Jena, RDFLib, Oxigraph CLI, the
   browser-based YASGUI editor, etc.) can talk to ArangoDB without knowing it
   is a triplestore impostor.
3. **Adapts to the user's physical ArangoDB schema** — including PG
   (one collection per class), LPG (multi-class collections with a
   discriminator field), RPT (RDF triple-store layout, e.g. the legacy
   Foxx `_triples` collection), and **hybrid combinations of all three
   in one database** — via either a hand-authored OWL ontology or a
   `MappingBundle` acquired from
   [`arangodb-schema-analyzer`](https://github.com/ArthurKeen/arango-schema-mapper)
   (with this project's RPT-detection extension layered on top).
4. **Supports natural-language entry** via an `nl2sparql` pipeline analogous
   to the sister project's `nl2cypher`.
5. **Mirrors the architecture of [`arango-cypher-py`](https://github.com/ArthurKeen/arango-cypher-py)**
   so the two services are operationally and developmentally interchangeable.

## 2. Non-goals (v1)

- **SPARQL 1.1 Update** — `INSERT DATA`, `DELETE DATA`, `LOAD`, `CLEAR`,
  `CREATE`, `DROP`, `COPY`, `MOVE`, `ADD` are out of scope. Writes go through
  AQL (or python-arango) directly.
- **Federated query (`SERVICE` keyword)** — out of scope for v1. The W3C
  Service Description response will advertise `sd:Feature` without
  `sd:BasicFederatedQuery`. Cross-Arango-database federation may land in v2
  if there is demand.
- **Inferencing / reasoning** — `arango-sparql-py` does not perform RDFS or
  OWL entailment over the loaded ontology. The ontology is mapping metadata,
  not a reasoning surface. Customers needing OWL reasoning should pre-
  materialise inferred triples in their data layer.
- **Multi-tenancy across separate processes** — sessions are per-process
  in-memory; running multiple workers requires a sticky-session load
  balancer. Cross-process session sharing (Redis, etc.) is a v2 concern.
- **Replacing AQL as the database's query language** — this is a transpiler,
  not a competing query engine. AQL remains canonical; SPARQL is an
  alternate front-end with mapped semantics.

## 3. Success criteria (v1.0 acceptance)

1. **W3C DAWG query-evaluation coverage ≥ 25 %** (the source-of-truth
   counter is [`tests/w3c/COVERAGE_REPORT.md`](../../tests/w3c/COVERAGE_REPORT.md);
   today's number is 15.0 %). Of that 25 %, no single XFAIL bucket may
   exceed 30 % of remaining failures (i.e. no single missing visitor can
   block more than a quarter of the gap).
2. **Conformant SPARQL Protocol endpoint** — `GET/POST /sparql` returns
   `application/sparql-results+json`, `application/sparql-results+xml`,
   `text/csv`, and `text/tab-separated-values` per the request's `Accept`
   header. `GET /sparql` (no query) returns the Service Description as
   `text/turtle`.
3. **Native physical-model coverage** — the translator emits correct AQL
   against every supported physical layout in §6.1 (RPT triple-store,
   PG dedicated collections, LPG type-discriminated collections, plain
   document collections), with a fixture corpus under `tests/translate/`
   that exercises each combination.
4. **Hybrid translation in a single BGP** — a SPARQL query whose triples
   touch two or more different physical models (e.g. one BGP that joins
   an RPT-style class to a PG-style class via a shared subject) is
   translated to a single AQL query rather than rejected. Acceptance
   covered by `tests/translate/hybrid.yml` + cross-validation cases.
5. **Schema detection** — both the algorithmic detector
   (`arango_sparql.schema.detect.classify_schema`) and the
   analyzer-backed detector (via `arangodb-schema-analyzer ≥ 0.6.1`)
   ship, with the analyzer-backed path winning on `auto`. Detection
   correctly classifies the sister project's full mapping-fixture corpus
   (PG, LPG, hybrid, plus the new RPT fixtures introduced by this
   project) with zero false-negatives.
6. **Schema HTTP surface parity with `arango-cypher-py`** — `/schema/introspect`,
   `/schema/properties`, `/schema/summary`, `/schema/statistics`,
   `/schema/status`, `/schema/invalidate-cache`,
   `/schema/force-reacquire`, `/mapping/import-owl`,
   `/mapping/export-owl`. See §6.4.
7. **Hybrid-schema parity with the legacy Foxx service** — every
   `references/arango-sparql/tests/` fixture that the legacy
   `aql-translator.js` could handle has a corresponding golden case under
   `tests/translate/` that emits semantically equivalent AQL. Includes
   the legacy's RPT (`_triples` collection, subject/predicate/object/object_uri/object_value
   columns, `STARTS_WITH(_, '_:')` blank-node heuristic) and PGT (per-class
   collections, `_uri` subject convention) translation paths.
8. **Operational parity with `arango-cypher-py`** — same connection /
   session model, same public-mode CORS posture, same observability log
   shape, same rate-limit buckets, same SSRF guard, same
   `_require_analyzer_unless_opted_out()` startup guard with
   `ARANGO_SPARQL_ALLOW_HEURISTIC=1` opt-out.
9. **Public release readiness** — repo published, CI green on every
   supported Python version, MIT LICENSE, CONTRIBUTING + SECURITY
   documents, repeatable Docker-Compose dev loop.

---

## 4. Architecture overview

```
                 ┌────────────────────────────────────────────────┐
   SPARQL ───►  │  rdflib parser → algebra translateQuery        │
                 │            │                                   │
                 │            ▼                                   │
                 │  AlgebraVisitor (one visit_<Node> per op)      │
                 │            │                                   │
                 │            ▼                                   │
                 │  AqlQueryBuilder (parameterised, bind-only)    │ ◄── SchemaResolver
                 │            │                                          (consumes ┐
                 │            ▼                                           Mapping ─┼──┐
   AQL   ◄────  │  TranslateResult{aql, bind_vars, warnings,             Bundle  ┘  │
                 │                  schema_warnings}                   + OWL  Turtle)│
                 └────────────────────────────────────────────────┘                  │
                                  ▲                                                  │
                                  │                                                  ▼
                ┌─────────────────┴─────────────────┐         ┌──────────────────────────────────┐
                │                                   │         │  Schema acquisition pipeline      │
   FastAPI service                      NL2SPARQL pipeline    │  (arango_sparql.schema.*):        │
   (RPC + /sparql Protocol +            (LLM + repair loop)   │   • classify_schema (heuristic)   │
    /schema/* + /mapping/*)             (conceptual schema    │   • acquire_mapping_bundle (uses  │
                │                        only — no physics)   │       arangodb-schema-analyzer)   │
                │                                   │         │   • detect_rpt_pattern (RDF       │
                │                                   │         │       triple-store extension)     │
                │                                   │         │   • fingerprint shape / counts    │
                │                                   │         │   • ArangoSchemaCache (persistent)│
                │                                   │         └──────────────────────────────────┘
                │                                   │                    │
                └─────────► python-arango ◄─────────┴────────────────────┘
                                  │
                                  ▼
                            ArangoDB (3.11+)
```

Source-of-truth modules:

| Concern | Module |
| --- | --- |
| Public translate API | `arango_sparql/api.py` |
| SPARQL parsing | `arango_sparql/translate/parser.py` |
| Algebra walker | `arango_sparql/translate/visitor.py` |
| AQL builder | `arango_sparql/translate/builder.py` |
| Schema mapping (consumes Mapping Bundle / OWL) | `arango_sparql/translate/resolver.py` |
| Schema detection (heuristic + analyzer + RPT extension) | `arango_sparql/schema/detect.py`, `arango_sparql/schema/acquire.py`, `arango_sparql/schema/rpt.py` |
| Persistent schema cache (in ArangoDB) | `arango_sparql/schema/cache.py` |
| Typed errors | `arango_sparql/errors.py` |
| FastAPI app + middleware + analyzer-required guard | `arango_sparql/service/app.py` |
| Pydantic models | `arango_sparql/service/models.py` |
| Sessions, rate-limit, SSRF, redaction | `arango_sparql/service/security.py` |
| Routes | `arango_sparql/service/routes/{health,connect,sparql,nl,schema,mapping,protocol}.py` |
| NL pipeline | `arango_sparql/nl2sparql/pipeline.py` |

---

## 5. HTTP surface

### 5.1 RPC routes (current, stable)

These are the service's native, JSON-only contract. They are not the W3C
SPARQL Protocol — they are richer (they return AQL, bind vars, warnings,
explain plans, profile traces) and are tailored to the UI and to integrators
that already speak our shape.

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET`  | `/health` | none | Liveness — returns `{status, version}` |
| `POST` | `/connect` | open or session | Open an ArangoDB session (URL+credentials → session token); SSRF-guarded |
| `POST` | `/disconnect` | session | Close the session |
| `GET`  | `/connect/defaults` | none in dev / session in public mode | Return non-secret env-var defaults the connect dialog should pre-fill |
| `POST` | `/translate` | rate-limited | SPARQL → AQL only (no DB access) |
| `POST` | `/validate` | rate-limited | SPARQL parse-only validation |
| `POST` | `/execute` | session + rate-limited | SPARQL → AQL → ArangoDB → bindings |
| `POST` | `/execute-aql` | session + rate-limited | Pass-through AQL (the UI's "rerun without re-translating") |
| `POST` | `/explain` | session + rate-limited | SPARQL → AQL → `db.aql.explain` |
| `POST` | `/profile` | session + rate-limited | SPARQL → AQL → `db.aql.execute(profile=2)` |
| `POST` | `/nl-translate` | session-optional + NL rate-limited | NL question → SPARQL → AQL |
| `POST` | `/nl-explain` | session-optional + NL rate-limited | NL question → SPARQL → AQL → human-readable explanation |
| `POST` | `/nl-execute` | session + NL rate-limited + compute rate-limited | NL question → SPARQL → AQL → bindings |
| `GET`  | `/schema/introspect` | session + rate-limited | Live schema acquisition (analyzer or heuristic) — see §6.4 |
| `GET`  | `/schema/properties` | session + rate-limited | Per-collection inferred property catalog |
| `GET`  | `/schema/summary` | rate-limited | Conceptual summary derived from a client-supplied mapping body (no DB access) |
| `GET`  | `/schema/statistics` | session + rate-limited | Cardinality statistics block from `arangodb-schema-analyzer` |
| `GET`  | `/schema/status` | session + rate-limited | Schema-drift report (shape vs counts fingerprints) |
| `POST` | `/schema/invalidate-cache` | session + rate-limited | Drop the per-database mapping cache entry |
| `POST` | `/schema/force-reacquire` | session + rate-limited | Re-run analyzer (bypass cache); 503 if analyzer not installed and `ARANGO_SPARQL_ALLOW_HEURISTIC` not set |
| `POST` | `/mapping/import-owl` | session + rate-limited | Replace the active mapping with one parsed from a posted OWL/Turtle ontology |
| `POST` | `/mapping/export-owl` | session + rate-limited | Render the active mapping as `text/turtle` |

Every error response is a 422 with `{"error": "...", "code": "E_..."}`. Error
codes are stable strings from `arango_sparql.errors`:

| Code | Class | Meaning |
| --- | --- | --- |
| `E_SPARQL_PARSE` | `SparqlParseError` | rdflib rejected the query string |
| `E_SPARQL_UNSUPPORTED` | `UnsupportedSparqlError` | Translator reached an Algebra node it doesn't yet emit AQL for |
| `E_SCHEMA_RESOLVE` | `SchemaResolutionError` | An IRI couldn't be mapped to a physical collection / property |
| `E_AQL_EMIT` | `AqlEmitError` | Builder produced no FOR clause, or a structurally invalid plan |
| `E_SPARQL` | `SparqlError` | Catch-all base; should never appear unaltered in production |

### 5.2 W3C SPARQL 1.1 Protocol endpoint (planned for v1.0)

A new route module (`arango_sparql/service/routes/protocol.py`) will expose:

| Method | Path | Body / Params | Behavior |
| --- | --- | --- | --- |
| `GET`  | `/sparql` | `?query=…` (URL-encoded) | Translate + execute; respond per `Accept` |
| `GET`  | `/sparql` | (no query) | Service Description as `text/turtle` |
| `POST` | `/sparql` | body: `application/sparql-query` | Translate + execute; respond per `Accept` |
| `POST` | `/sparql` | body: `application/x-www-form-urlencoded` with `query=` | Same as above |

**Result-format negotiation** must support, in this priority order:
`application/sparql-results+json`, `application/sparql-results+xml`,
`text/csv`, `text/tab-separated-values`. For `ASK` queries, the same media
types apply but the body shape is the W3C SPARQL Results "boolean" form. For
`CONSTRUCT` / `DESCRIBE` queries (when those visitors land), the response is
RDF and negotiated against `text/turtle`, `application/n-triples`,
`application/rdf+xml`, `application/ld+json`.

**Query timeouts and result caps**: re-use `_MAX_RESULT_DOCS` from the
existing routes. On overflow the response surfaces a `W_RESULT_TRUNCATED`
warning header (same code the RPC routes use). Hard timeout default 60 s,
overridable via `SPARQL_PROTOCOL_TIMEOUT_SECONDS`.

**Session binding**: `GET /sparql` accepts `?session=<token>` (or the
existing `X-Arango-Session` / `Authorization: Bearer …` headers); `POST` uses
the same headers. In default (non-public) mode, an unbound `/sparql` request
falls back to the env-default connection so a developer's `curl /sparql` Just
Works against `localhost:8529`.

**Service Description content** must declare:
- the supported result formats listed above,
- the `sd:availableGraphs` set sourced from the loaded ontology
  (one named graph per declared `phys:collectionName` plus the default graph),
- the explicit list of supported features
  (`sd:UnionDefaultGraph`, `sd:DereferencesURIs`, the empty set for
  `sd:BasicFederatedQuery`, etc.),
- a link to this PRD as `sd:endpoint`'s `dcterms:description`.

### 5.3 Out-of-scope endpoints (v1)

- SPARQL Update Protocol (`POST /update`)
- Graph Store HTTP Protocol (`/graph?graph=…`)
- Federated `SERVICE` round-trips
- WebSocket / Server-Sent-Events streaming variants

---

## 6. Schema model & physical layouts

The product's hardest correctness problem is *not* SPARQL parsing or AQL
emission — those are well-defined. It is the gap between a SPARQL query
written against a *conceptual* RDF/OWL graph and the wide range of
*physical* layouts that ArangoDB customers actually use to store the same
data. This section formalises that gap.

### 6.1 Physical schema model taxonomy

`arango-sparql-py` recognises four primitive physical models. A real
ArangoDB database typically uses one or, increasingly, a mix of several
("hybrid").

| Style ID (in OWL `phys:mappingStyle` and JSON `physicalMapping.entities[*].style`) | Friendly name | Stored as | Read pattern |
| --- | --- | --- | --- |
| `COLLECTION` | **PG** — Property Graph | One ArangoDB document collection per OWL class | `FOR doc IN @@coll` |
| `LABEL` | **LPG** — Labeled Property Graph | One shared document collection holding multiple OWL classes, discriminated by a `typeField` (e.g. `type`, `_type`, `entityType`, `label`) | `FOR doc IN @@coll FILTER doc.<typeField> == @typeValue` |
| `RPT` | **RDF / Resource-style triples** | A triple-row collection (the legacy Foxx default was `_triples`) with `subject_uri`, `predicate`, `object_uri`, `object_value` columns and `_:` prefix for blank nodes | `FOR t IN @@triples FILTER t.predicate == @p AND t.subject_uri == @s` plus `COALESCE(t.object_uri, t.object_value)` to bind the object |
| `DOCUMENT` | Plain document | Document collection with no class-discriminator field; OWL class derived from collection name only | Same as `COLLECTION` |

Relationship styles, attached to OWL `owl:ObjectProperty`:

| Style ID | Friendly name | Stored as | Read pattern |
| --- | --- | --- | --- |
| `DEDICATED_COLLECTION` | **PG-typed edge** | One edge collection per relationship type (the collection name *is* the relationship name) | `FOR v, e IN OUTBOUND doc @@edgeColl` |
| `GENERIC_WITH_TYPE` | **LPG-typed edge** | One shared edge collection holding multiple relationship types, discriminated by a `typeField` | `FOR v, e IN OUTBOUND doc @@edgeColl FILTER e.<typeField> == @typeValue` |
| `RPT_EDGE` | **RDF object property** | An object-property triple in the `_triples` collection (`object_uri` populated, `object_value` null) | Same as `RPT` entity read; the predicate IRI carries the relationship semantics |

**Hybrid** is not a fifth style; it is the *case where two or more of the
above coexist in one database*. Concretely, a single SPARQL query can
have one BGP triple resolve to `COLLECTION` (read a PG collection
directly), the next to `RPT` (look the same subject up in `_triples`),
and the third to `LABEL` (filter a shared collection by `typeField`) —
joined on a shared subject URI. The translator must emit one AQL query
that does all three and joins them with `FILTER` clauses on `_uri` (or
the equivalent triple-store subject column). Acceptance for this is
criterion §3.4.

> **Why RPT matters even when nobody is starting greenfield with it.**
> The legacy Foxx `arango-sparql` service shipped an **`rpt-translator.js`**
> that read `_triples` rows directly. Customers that adopted the legacy
> service for a SPARQL workload still have those collections live. v1's
> Foxx-parity criterion (§3.7) requires translating against them. The
> *new* contribution of `arango-sparql-py` is letting customers
> mix RPT-resident classes with PG/LPG-resident classes in one query —
> something the legacy could not do (its `processSparqlQuery` picked one
> `model` per request).

### 6.2 OWL contract (the physical-mapping vocabulary)

The translator never invents collection names. Every concrete
SPARQL→AQL translation requires an OWL/Turtle ontology produced by
[`arangodb-schema-analyzer`](https://github.com/ArthurKeen/arango-schema-mapper)
(or hand-authored to match its annotation vocabulary). Annotations live
under either of the two `phys:` namespaces the analyzer has shipped
historically; both are accepted (see `resolver.py`):

| Annotation IRI (relative to the `phys:` prefix) | Attaches to | Carries | Read by |
| --- | --- | --- | --- |
| `mappingStyle` | `owl:Class` (entity styles) or `owl:ObjectProperty` (relationship styles) | One of the style IDs from §6.1 | `SchemaResolver.resolve_class` / `resolve_property` to dispatch the read pattern |
| `collectionName` | `owl:Class` | Document-collection name | All four entity styles (`COLLECTION`, `LABEL`, `RPT` reads from a *triples* collection still need a name, `DOCUMENT`) |
| `edgeCollectionName` | `owl:ObjectProperty` | Edge-collection name | `DEDICATED_COLLECTION`, `GENERIC_WITH_TYPE` |
| `typeField` | `owl:Class` *or* `owl:ObjectProperty` | Discriminator field name (e.g. `type`) | `LABEL`, `GENERIC_WITH_TYPE` |
| `typeValue` | `owl:Class` *or* `owl:ObjectProperty` | Discriminator value (e.g. `Person`) | `LABEL`, `GENERIC_WITH_TYPE` |
| `triplesCollection` | `owl:Class` | Name of the RPT-style collection holding this class's triples (defaults to `_triples`) | `RPT`, `RPT_EDGE` |
| `subjectColumn`, `predicateColumn`, `objectUriColumn`, `objectValueColumn` | `owl:Class` | Override the legacy Foxx column names if a customer renamed them | `RPT`, `RPT_EDGE` |
| `tenantField`, `tenantEntity` | `owl:Class` | Multi-tenancy scope (see §6.5) | All entity styles |

`SchemaResolver` is the only module that reads any of these. Visitors
call `resolve_class(iri)` and `resolve_property(iri)`; the resolver
returns a tagged dataclass (`ResolvedClass.style ∈ {COLLECTION, LABEL,
RPT, DOCUMENT}`) and the visitor's `_emit_triple` dispatches on `style`.

> **Status note.** The current resolver reads `phys:collectionName`,
> `phys:edgeCollectionName`, `phys:typeField`, and `phys:typeValue`.
> Adding `phys:mappingStyle` (with the explicit style enum) and the
> `phys:triplesCollection` / `phys:*Column` family is a v1.0 deliverable
> (criterion §3.3 + §3.7).

### 6.3 Schema-detection pipeline

A customer rarely hands us an OWL ontology pre-authored. The mapping is
*acquired* — usually once, then cached — by introspecting their live
database. Acquisition is two-tier:

#### 6.3.1 Algorithmic detector (heuristic, no external dependency)

Module: `arango_sparql.schema.detect`

```python
def classify_schema(db: StandardDatabase) -> Literal["pg", "lpg", "rpt", "hybrid", "unknown"]: ...
def detect_rpt_pattern(db: StandardDatabase, *, sample_size: int = 20) -> RptDetectionResult: ...
def build_heuristic_mapping(db: StandardDatabase, *, schema_type: str) -> MappingBundle: ...
```

Heuristics, in order:

1. **Per-collection sampling** — at most `sample_size = 20` documents
   per collection (cap on cost; `LIMIT @n`).
2. **RPT pattern detection** (the new layer this project adds on top
   of the analyzer's PG/LPG vocabulary): a collection looks RPT-shaped
   if ≥80 % of sampled documents carry all three of `subject_uri` /
   `predicate` / (`object_uri` ∨ `object_value`), or matches
   `_triples`-style structural fingerprints (legacy Foxx column
   conventions). Returns the inferred column overrides.
3. **PG vs LPG discriminator** — drawn from a tiered candidate set
   (tier 1: `type`, `_type`, `entityType`; tier 2: `label`, `labels`,
   `kind`). Tier 1 fields qualify on the 80 %-coverage rule alone.
   Tier 2 fields additionally require ≤32 distinct values, a
   low-cardinality ratio, and class-like value strings (`[A-Za-z0-9_-]+`)
   to avoid mis-classifying free-text columns.
4. **Edge classification** — typed (`GENERIC_WITH_TYPE`) vs dedicated
   (`DEDICATED_COLLECTION`) using the same discriminator rules against
   `{type, relation, relType, _type}`.
5. **Aggregate** — per-collection signals are tallied; all-PG ⇒ `pg`,
   all-LPG ⇒ `lpg`, all-RPT ⇒ `rpt`, mixed ⇒ `hybrid`.

The heuristic detector emits a `MappingBundle` (the same shape the
analyzer produces — see §6.3.2) so downstream code does not branch on
"who built this mapping". `metadata.confidence` is fixed at `0.1` and
`metadata.reviewRequired = true` (mirrors the sister project's
heuristic-path conventions); `metadata.detectedPatterns` lists string
tags `PG_ENTITY_COLLECTION`, `LPG_LABEL`, `RPT_TRIPLES`,
`PG_DEDICATED_EDGE`, `LPG_GENERIC_EDGE`, `RPT_OBJECT_PROPERTY`.

#### 6.3.2 Analyzer-backed acquisition (preferred)

Module: `arango_sparql.schema.acquire`

```python
def acquire_mapping_bundle(
    db: StandardDatabase,
    *,
    include_owl: bool = False,
    strategy: Literal["auto", "analyzer", "heuristic"] = "auto",
    force_refresh: bool = False,
) -> MappingBundle: ...
```

Wraps `arangodb-schema-analyzer ≥ 0.6.1`'s `AgenticSchemaAnalyzer.analyze_physical_schema`,
then post-processes:

1. Normalise the analyzer's `conceptualSchema + physicalMapping +
   metadata` into our `MappingBundle` (re-using
   `arango_query_core.mapping.mapping_from_wire_dict` if/when that
   shared package lands).
2. Run the algorithmic RPT detector on top of the analyzer's snapshot
   to **add `RPT` entries the analyzer would not detect on its own**
   (the analyzer only knows PG/LPG today). RPT entries are merged into
   `physicalMapping.entities` with `style = "RPT"`.
3. Optionally export the conceptual half as OWL/Turtle via
   `export_conceptual_model_as_owl_turtle` and attach to the bundle.

**Resolution priority on `strategy="auto"`** (matches the sister project):
analyzer wins when installed; on `ImportError` we fall back to heuristic
and attach `metadata.warnings = [{"code": "ANALYZER_NOT_INSTALLED"}]`.
Explicit `strategy="heuristic"` skips analyzer; explicit
`strategy="analyzer"` raises if missing.

#### 6.3.3 Caching and drift detection

Module: `arango_sparql.schema.cache`

Two-tier persistence:

| Layer | Where | Keyed by | Lifetime |
| --- | --- | --- | --- |
| In-process LRU | `_mapping_cache` dict | `db.name` | TTL 300 s (`SCHEMA_MAPPING_CACHE_TTL_SECONDS`) |
| Persistent | `arango_sparql_schema_cache` collection in the customer's own DB | `(db.name, key="mapping")` | Until invalidated |

Refresh policy uses the analyzer's two cheap fingerprints:

- `fingerprint_physical_shape(db)` — SHA-256 of collection list +
  doc/edge kind + index digests. **Fires when topology changes.**
- `fingerprint_physical_counts(db)` — shape fingerprint + per-collection
  `count()`. **Fires when row volume drifts** (used by `/schema/status`).

When `/schema/status` reports `stats_changed` but `shape_unchanged`, the
mapping itself is reused but the `metadata.statistics` block is
re-derived. When shape changes, the full mapping is re-acquired.

#### 6.3.4 Required-analyzer guard at startup

Mirrors `arango-cypher-py`. `arango_sparql/service/app.py` calls
`_require_analyzer_unless_opted_out()` at import time. If
`arangodb-schema-analyzer` is not importable and
`ARANGO_SPARQL_ALLOW_HEURISTIC` is unset, the service refuses to
start. The opt-out is *deliberately verbose* so a heuristic-only
deployment is a conscious operator decision, not a silent default.

### 6.4 Schema HTTP surface

Mirrors the sister project's `arango_cypher.service.routes.schema` and
`arango_cypher.service.routes.owl`. Live in
`arango_sparql/service/routes/{schema,mapping}.py`.

| Method | Path | Request | Response | Notes |
| --- | --- | --- | --- | --- |
| `GET` | `/schema/introspect` | query: `?force=<bool>`, `?strategy=<auto|analyzer|heuristic>` | `MappingBundle` JSON + summary block | Live acquisition; respects cache unless `force=true` |
| `GET` | `/schema/properties` | — | `{<collection>: {<attr>: {type, sample}}}` | Per-collection inferred property catalog (samples 20 docs) |
| `GET` | `/schema/summary` | body: `{mapping}` (no DB access) | conceptual summary | Used by the UI when it already has a mapping in hand |
| `GET` | `/schema/statistics` | — | `metadata.statistics` block | Cardinality, in/out degree, selectivity per relationship |
| `GET` | `/schema/status` | — | `{stats_changed, shape_unchanged, fingerprints, last_acquired_at}` | Schema-drift report; cheap |
| `POST` | `/schema/invalidate-cache` | — | `{invalidated: bool}` | Drops the cache entry for the connected DB |
| `POST` | `/schema/force-reacquire` | — | fresh `MappingBundle` | Re-runs analyzer; `503` if analyzer missing and `ARANGO_SPARQL_ALLOW_HEURISTIC` not set |
| `POST` | `/mapping/import-owl` | body: `text/turtle` | `{accepted: bool, mapping}` | Replaces the active mapping with one parsed from a posted OWL ontology |
| `POST` | `/mapping/export-owl` | — | `text/turtle` | Renders the active mapping as Turtle |

Auth model: every route except `/schema/summary` requires a session
(`X-Arango-Session` or `Authorization: Bearer …`). All routes are subject
to the compute rate-limit bucket.

### 6.5 Multi-tenancy and sharding from analyzer metadata

The analyzer surfaces three blocks the SPARQL planner respects:

- **`metadata.tenantScope`** (per-entity) — when present, every AQL emit
  for that entity adds `FILTER doc.<tenantField> == @<tenantBind>`. The
  bind value is sourced from the session's `X-Tenant-Id` header (fall
  back to the env-default `ARANGO_SPARQL_DEFAULT_TENANT` in dev). Routes
  that touch DB always pass the tenant filter through; `/translate`
  without a session uses the env-default.
- **`metadata.multitenancy`** (database-wide) — names the tenant root
  entity / discriminator strategy. Used by `/schema/introspect` to
  expose the tenant model to the UI.
- **`physicalMapping.shardFamilies`** — when present, AQL emit for a
  cross-shard query inserts a `WITH @@coll1, @@coll2, ...` clause so
  the optimiser can plan the broadcast. This *is* relevant for SPARQL
  because RPT-style queries against `_triples` often need to scan
  multiple shard family members.

### 6.6 Supported physical schema shapes — status table

| Shape | Status | Acceptance test |
| --- | --- | --- |
| **PG `COLLECTION`** — one OWL class ↔ one ArangoDB collection; datatype properties as top-level attributes | ✅ shipped | `tests/translate/bgp_select.yml` (every case but the `hybrid_collection_emits_type_filter` row) |
| **LPG `LABEL`** — multi-class collection with `phys:typeField`/`phys:typeValue` discriminator | ✅ shipped | `tests/translate/bgp_select.yml :: hybrid_collection_emits_type_filter` |
| **PG `DEDICATED_COLLECTION` edges** — OWL `ObjectProperty` resolves to a `phys:edgeCollectionName`; SPARQL traversal lowers to `FOR v, e IN OUTBOUND` | 🟡 v1.0 | Currently raises `UnsupportedSparqlError("Object property … requires edge traversal")`. Top XFAIL bucket. |
| **LPG `GENERIC_WITH_TYPE` edges** — typed-edge traversal with discriminator FILTER | 🟡 v1.0 | depends on the previous row |
| **RPT (`_triples` triple-store)** — read subject/predicate/object rows, `COALESCE(object_uri, object_value)` for objects, `STARTS_WITH(_, "_:")` heuristic for blank nodes | 🟡 v1.0 | Tracked under criterion §3.7 (Foxx parity); fixture corpus to land at `tests/translate/rpt.yml` and `tests/cross/test_rpt_cross.py` |
| **Mixed-model BGP** (RPT + PG, RPT + LPG, PG + LPG, all three) | 🟡 v1.0 | Criterion §3.4; fixture corpus `tests/translate/hybrid.yml` |
| **Property-path expansion** — `MulPath` / `SequencePath` / `AlternativePath` over edge styles | 🔴 v1.1 | Tracked in `COVERAGE_REPORT.md` under the `MulPath` / `SequencePath` XFAIL buckets |
| **Named-graph dispatch** — `GRAPH ?g { … }` resolves to a per-graph collection or to a graph-name attribute | 🔴 v1.2 | Tracked in `COVERAGE_REPORT.md` under the `Graph` XFAIL bucket |
| **Federated `SERVICE`** — out of scope (see §2) | ❌ won't fix in v1 | — |

### 6.7 Schema warnings (non-fatal)

When the resolver can do the right thing but the operator probably wants
to know, it emits a `W_SCHEMA_*` advisory rather than throwing. Surfaced
via `TranslateResponse.schema_warnings` (separate from operational
`warnings`) so the UI can render them in a dedicated "schema-mapping
advisories" panel.

| Code | Trigger |
| --- | --- |
| `W_SCHEMA_UNMAPPED_IRI` | A predicate IRI is not declared in the ontology. Resolver falls back to the IRI's local name as the AQL attribute. |
| `W_SCHEMA_DEFAULT_COLLECTION` | A class is declared `owl:Class` but lacks `phys:collectionName`. Resolver falls back to the IRI's local name as the collection name. |
| `W_SCHEMA_RPT_INFERRED` | The RPT detector flagged a collection as triples-shaped but the OWL ontology did not declare it as such. Resolver treats it as RPT and surfaces this so the operator can either accept the inference or annotate the OWL. |
| `W_SCHEMA_HYBRID_DETECTED` | The mapping contains entities of two or more `style` values (e.g. one `RPT` + one `LABEL`). Informational only; useful for the UI banner. |
| `W_SCHEMA_HEURISTIC_FALLBACK` | The mapping was acquired heuristically because `arangodb-schema-analyzer` was not importable. Pairs with the route-layer `503` when `ARANGO_SPARQL_ALLOW_HEURISTIC` is unset. |
| `W_SCHEMA_LOW_CONFIDENCE` | `metadata.confidence < 0.5`; the operator should review the mapping before relying on it for a production workload. |
| `W_SCHEMA_DRIFT_STATS` | `/schema/status` detected a counts-fingerprint change since last refresh. Cardinality-aware planning may be stale. |
| `W_SCHEMA_DRIFT_SHAPE` | `/schema/status` detected a shape-fingerprint change since last refresh. The mapping itself is stale; a re-acquire is recommended. |

---

## 7. NL → SPARQL pipeline

`arango_sparql/nl2sparql/` mirrors `arango_cypher/nl2cypher/` with these
deliberate differences:

| Concern | Decision |
| --- | --- |
| Output language | SPARQL 1.1 SELECT/ASK (CONSTRUCT/DESCRIBE only when those visitors ship) |
| Schema delivery to the LLM | **Conceptual-only summary** — class IRIs (with `rdfs:label`), object properties (with `domain` / `range`), and datatype properties (with `domain` / `xsd:` datatype). Mirrors `arango-cypher-py`'s `_build_schema_summary`. **Never** sends physical mapping details (collection names, `typeField`/`typeValue`, `triplesCollection`, etc.) — those are physics, not vocabulary. |
| LLM prompt-prefix caching | Schema block placed first in the prompt so OpenAI prefix cache (≥1024 tokens) hits across NL turns; Anthropic prompt is split at `## Examples` for the same reason. `NL2SparqlResult` carries `prompt_tokens` and `cached_tokens` so the UI can surface cache-hit ratio. |
| Repair loop | Up to `max_repairs=2` round-trips. Each repair feeds the LLM the previous SPARQL plus the `SparqlError.code` + sanitised message |
| Provider selection | Env-driven. `NL2SPARQL_PROVIDER` takes precedence over the Cypher-style `LLM_PROVIDER` so a single-shell setup that already configured the Cypher service doesn't need duplicate vars |
| Cost accounting | Per-call USD estimate via a static pricing table (`cost.py`); the response surfaces `cost_usd` so the UI can render running totals |
| Failure-as-outcome | Pipeline returns `PipelineOutcome` with empty `aql` + `W_NL_TRANSLATION_FAILED` warning rather than throwing. The route layer maps empty-AQL outcomes to a 422 with the same provenance fields as success |

NL evaluation lives under `tests/nl2sparql/eval/` and is gated behind
`RUN_EVAL=1`. The eval harness reports per-case correctness against a
baseline JSON; baseline regressions are CI-blocking once the harness lands
in the workflow (post-v1.0).

---

## 8. Multitenancy & security

### 8.1 Sessions

- In-memory dict keyed by an opaque token returned from `POST /connect`.
- TTL: `SESSION_TTL_SECONDS` (default 1800).
- Capacity: `MAX_SESSIONS` (default 100), LRU-evicted.
- Token transport: prefer `X-Arango-Session` (the ArangoDB platform proxy
  rewrites the standard `Authorization` header before forwarding to BYOC
  containers), fall back to `Authorization: Bearer …`.

### 8.2 Public-mode posture

`ARANGO_SPARQL_PUBLIC_MODE=1` flips the service's stance from "trusted
local dev" to "untrusted internet exposure". When set:

- Session auth is mandatory on every DB-bound endpoint (including the new
  Protocol `/sparql` endpoint).
- CORS credentials are forced off if `CORS_ALLOWED_ORIGINS` is `*`.
- The `/connect` SSRF guard additionally rejects RFC1918 / loopback /
  link-local / ULA literal IPs unless explicitly allowlisted via
  `ARANGO_SPARQL_CONNECT_ALLOWED_HOSTS`.
- Pydantic 422 validation errors no longer log a body preview (defence in
  depth against credential-shaped payloads ending up in logs).

### 8.3 Rate limits

Two token buckets, per-client (Authorization header → IP → `"anon"`):

| Bucket | Default | Endpoints |
| --- | --- | --- |
| Compute | 100 req/min | `/translate`, `/validate`, `/execute*`, `/explain`, `/profile`, `/sparql` |
| LLM | 10 req/min | `/nl-translate`, `/nl-explain`, `/nl-execute` |

Both overridable via `COMPUTE_RATE_LIMIT_PER_MINUTE` /
`NL_RATE_LIMIT_PER_MINUTE`.

### 8.4 SSRF guard on `/connect`

- Always rejects literal cloud-metadata hosts/IPs (AWS, Azure, GCP,
  Alibaba, OpenStack, DO).
- In public mode, additionally rejects literal private IPs.
- Allowlisting via `ARANGO_SPARQL_CONNECT_ALLOWED_HOSTS` (comma-separated
  host strings).
- Deliberately does **not** perform DNS resolution (DNS rebinding /
  blocking-IO probe risk).

### 8.5 Error redaction

All error messages reaching the client (and most reaching logs) pass through
`_sanitize_error`, which redacts URLs, IPv4 host:port pairs, key-value
credential forms (`password=…`, `api_key=…`), and `Authorization: …`
headers. Pydantic 422 validation responses use `_sanitize_pydantic_errors`
to additionally redact the echoed `input` field.

---

## 9. Observability

### 9.1 Endpoint timing logs

Every route logs a single structured line at completion via
`log_endpoint_timing(path, elapsed_ms, **kvs)`. Required keys: `path`,
`elapsed_ms`. Recommended keys per route family:

| Family | Extra keys |
| --- | --- |
| `/translate` | `sparql_len`, `aql_len`, `warnings` |
| `/execute*`, `/profile` | `translate_ms`, `exec_ms`, `rows`, `truncated` |
| `/explain` | `translate_ms`, `explain_ms` |
| `/nl-*` | `llm_calls`, `cost_usd`, `repaired`, `provider`, `model` |
| Any error path | `status="error"`, `code=<E_*>` |

### 9.2 LLM call logging

Every NL pipeline LLM call emits a structured log via `log_llm_call(...)`
carrying `provider`, `model`, `prompt_tokens`, `completion_tokens`,
`cost_usd`, `latency_ms`. A future cost-aggregator can derive per-tenant
spend without code changes.

### 9.3 Schema warnings

Translation responses carry a separate `schema_warnings` projection so the
UI's schema-mapping advisory panel can render `W_SCHEMA_*` codes
distinctly from the general `warnings` array (which carries operational
advisories like `W_RESULT_TRUNCATED`).

---

## 10. Conformance & testing

### 10.1 Test categories

| Category | Marker | Typical runtime | Gate |
| --- | --- | --- | --- |
| Translator unit + golden | unmarked | < 5 s | per-PR (CI-blocking) |
| Service routes (FastAPI TestClient) | unmarked | < 5 s | per-PR (CI-blocking) |
| NL2SPARQL pipeline (scripted client) | unmarked | < 5 s | per-PR (CI-blocking) |
| Cross-validation vs `pyoxigraph` | `cross` | < 5 s | per-PR (CI-blocking) |
| Schema-detection unit (heuristic + analyzer mocks + RPT) | unmarked | < 5 s | per-PR (CI-blocking) |
| Schema-fixture corpus (`tests/schema/fixtures/*.export.json`) | unmarked | < 5 s | per-PR (CI-blocking) |
| W3C DAWG translation-only harness | `w3c` | ~ 15 s | nightly (separate workflow, post-v1.0) |
| W3C live-execution harness (Docker) | `w3c` + `integration` | ~ 60 s | nightly + on-demand |
| Legacy Foxx round-trip (Docker, both services live) | `legacy_roundtrip` + `integration` | ~ 90 s | nightly + on-demand |
| Schema-detection live (Docker, against seeded PG/LPG/RPT/hybrid datasets) | `schema_live` + `integration` | ~ 30 s | nightly + on-demand |
| Translator perf benchmark (translation-only timings, gauge regressions) | `bench` | ~ 30 s | per-PR (gauge only — fails only on > 50 % regression) |
| NL eval | `eval` | minutes | gated on `RUN_EVAL=1`; baseline-comparison CI-blocking once it lands |

### 10.2 W3C ground-truth strategy

- **Translation-only harness** (`tests/w3c/test_w3c_query_evaluation.py`)
  parses every DAWG query and asks the visitor to emit AQL. Anything that
  raises `UnsupportedSparqlError` or `SchemaResolutionError` becomes an
  `xfail` with the exception's message as the reason; the
  `tests/w3c/analyze_coverage.py --write` aggregator turns the xfail
  reasons into `COVERAGE_REPORT.md`'s top-N XFAIL table, which is the
  prioritisation source-of-truth for visitor work.
- **Live-execution harness** (`tests/w3c/test_w3c_live_execution.py`,
  Docker-gated) loads the case's RDF data into a fresh per-test collection
  set, executes the translated AQL against ArangoDB, and compares cursor
  bindings against the W3C-expected `.srx` / `.srj` / `.ttl` results. A
  binding mismatch is reported as `xfail` (not `fail`) so the suite stays
  green during translator catch-up; the xfail reason captures the
  divergence so it surfaces in `COVERAGE_REPORT.md`.
- **Cross-validation harness** (`tests/cross/test_bgp_select_cross.py`)
  runs the same SPARQL against `pyoxigraph` (the W3C-conformant Rust
  triplestore via Python bindings) and against a tiny in-memory AQL-subset
  interpreter that consumes our translator output. Bindings must match by
  bag (or by order, for `ORDER BY` cases). This is the fastest way to
  catch a translator bug — every visitor change should land with at least
  one cross case.

### 10.3 Schema-detection corpus

Lives at `tests/schema/fixtures/*.export.json`, mirroring the sister
project's `tests/fixtures/mappings/` layout (same JSON wire shape). The
v1.0 corpus must include at least:

| Fixture name | Style mix | Provenance |
| --- | --- | --- |
| `pg.export.json` | All `COLLECTION` entities, all `DEDICATED_COLLECTION` edges | Carry-over from `arango-cypher-py` |
| `lpg.export.json` | All `LABEL` entities, all `GENERIC_WITH_TYPE` edges | Carry-over from `arango-cypher-py` |
| `hybrid.export.json` | Mixed `COLLECTION` + `LABEL`; mixed edges | Carry-over from `arango-cypher-py` |
| `rpt.export.json` | All `RPT` entities; legacy `_triples` collection | **New for `arango-sparql-py`** — covers the legacy Foxx Foxx layout |
| `rpt_pg_hybrid.export.json` | Some `RPT`, some `COLLECTION` | **New** — exercises §3.4 (mixed-model BGP) |
| `rpt_lpg_hybrid.export.json` | Some `RPT`, some `LABEL` | **New** — exercises §3.4 |
| `rpt_pg_lpg_hybrid.export.json` | All three styles in one mapping | **New** — the full hybrid case |
| `multitenant.export.json` | `metadata.tenantScope` populated; `metadata.multitenancy` populated | Carry-over from `arango-cypher-py` |
| `sharded.export.json` | `physicalMapping.shardFamilies` populated | Carry-over from `arango-cypher-py` |

For each fixture, the harness asserts:

1. The bundle parses through `mapping_from_wire_dict` round-trip.
2. The `SchemaResolver` correctly resolves the IRIs the fixture's
   conceptual half declares (no `MAPPING_NOT_FOUND`).
3. The translator can emit AQL for at least one BGP per entity in the
   fixture without raising.
4. For RPT fixtures, the emitted AQL references the correct triples
   collection name and column names.

### 10.4 Legacy Foxx round-trip regression

Module: `tests/legacy_roundtrip/`. Docker-Compose spins up both the
legacy `arango-sparql` Foxx service and `arango-sparql-py` against the
same ArangoDB. For every SPARQL query under
`references/arango-sparql/tests/fixtures/sparql/`:

1. Send the query to legacy Foxx (`POST /sparql`); record bindings.
2. Send the same query to `arango-sparql-py` (`POST /execute`); record
   bindings.
3. Compare bindings as bags (order-sensitive only for `ORDER BY`).
4. On divergence, record an `xfail` with the fixture name + first
   diverging row, surfacing into a `LEGACY_PARITY_REPORT.md`.

This is the operational form of acceptance criterion §3.7. The Foxx
service is the legacy ground-truth, *not* a competitor — divergence is
either a Foxx bug we can ignore (cite the legacy commit), a known-gap
in v1's translator (xfail with link to issue), or a real regression to
fix before tagging v1.0.

### 10.5 Coverage targets per release

| Release | W3C query-evaluation | Cross cases | Goldens | Schema fixtures | Legacy round-trip parity |
| --- | --- | --- | --- | --- | --- |
| v0.1 (current) | 15.0 % | 39 | 50+ | 0 | 0 % |
| v0.5 | 20 % | 60 | 80 | full PG/LPG/hybrid corpus (no RPT) | 0 % (translator can't read RPT yet) |
| **v1.0 (acceptance)** | **≥ 25 %** | **≥ 80** | **≥ 100** | **full corpus incl. RPT + RPT-hybrid** | **≥ 90 % of legacy SELECT/ASK fixtures** |
| v1.1 | 35 % (after `MulPath` / `SequencePath`) | 100 | 130 | + property-path-aware fixtures | ≥ 95 % |

---

## 11. Release roadmap

### v0.x (current — v1 prep)

- ✅ Visitors: BGP, Filter, Project, Distinct, Slice, OrderBy, AskQuery,
  Extend, LeftJoin, AggregateJoin, Join
- ✅ RPC routes (§5.1)
- ✅ NL2SPARQL pipeline + routes
- ✅ Schema warnings + resolver
- ✅ W3C harness (translation-only + live)
- ✅ Cross-validation harness
- ✅ Public-mode posture, sessions, rate limits, SSRF guard, redaction
- ✅ MIT LICENSE, CONTRIBUTING, SECURITY, CI workflow

### v1.0 (the "complete SPARQL service" milestone)

**Translator + protocol**

- W3C SPARQL 1.1 Protocol endpoint (§5.2)
- Service Description response (`text/turtle`)
- Result-format content negotiation (JSON / XML / CSV / TSV)
- Edge-collection traversal in `visit_BGP` for both `DEDICATED_COLLECTION`
  and `GENERIC_WITH_TYPE` styles (the "object property requires edge
  traversal" XFAIL bucket goes to zero)
- ASK / SELECT response in W3C SPARQL Results shapes
- W3C query-evaluation coverage ≥ 25 %
- Full nightly W3C workflow on `main`

**Physical-model coverage**

- `RPT` style in the resolver, visitor, and AQL builder (read
  `_triples`-style rows; `COALESCE(object_uri, object_value)`;
  blank-node `STARTS_WITH` heuristic)
- Mixed-model BGP support (a single SPARQL BGP whose triples touch two
  or more of `COLLECTION` / `LABEL` / `RPT`, joined on `_uri` /
  `subject_uri`)
- Resolver reads `phys:mappingStyle` and the `phys:triplesCollection` /
  `phys:*Column` family of OWL annotations
- Hybrid-schema parity with the legacy Foxx fixtures (criterion §3.7)

**Schema layer**

- `arango_sparql.schema.detect.classify_schema` (heuristic, returns one
  of `pg | lpg | rpt | hybrid | unknown`)
- `arango_sparql.schema.detect.detect_rpt_pattern` (RPT detector that
  layers on top of the analyzer's PG/LPG output)
- `arango_sparql.schema.acquire.acquire_mapping_bundle` (analyzer-backed
  with heuristic fallback; `strategy ∈ {auto, analyzer, heuristic}`)
- `arango_sparql.schema.cache.ArangoSchemaCache` (persistent in
  `arango_sparql_schema_cache` collection; two-tier with in-process LRU)
- `_require_analyzer_unless_opted_out()` startup guard +
  `ARANGO_SPARQL_ALLOW_HEURISTIC=1` env opt-out
- Schema HTTP surface: `/schema/{introspect, properties, summary,
  statistics, status, invalidate-cache, force-reacquire}` +
  `/mapping/{import-owl, export-owl}` (§6.4)
- Multi-tenancy: `tenantScope` enforcement at AQL emit; `X-Tenant-Id`
  session header
- Sharding: `physicalMapping.shardFamilies` honoured in cross-shard AQL

**Release**

- First public PyPI release
- Schema-detection corpus complete (PG, LPG, RPT, all four hybrid
  permutations, multi-tenant, sharded — §10.3)
- Legacy round-trip parity ≥ 90 % (§10.4)

### v1.1 (depth on translation)

- `visit_Minus` (anti-join)
- `visit_ToMultiSet` (subqueries)
- Property-path expansion (`MulPath`, `SequencePath`, `AlternativePath`)
- `visit_ConstructQuery` (RDF output → `text/turtle` / `application/n-triples`)
- W3C query-evaluation coverage ≥ 35 %

### v1.2 (graph dispatch)

- `visit_Graph` — named-graph routing to per-graph collections or to a
  graph-name attribute discriminator
- `Variable predicates` lowered via multi-collection UNION
- Graph Store HTTP Protocol (`/graph?graph=…`)

### v2 (federation + scaling, only if customer-driven)

- `SERVICE` keyword for cross-Arango-database federation
- Cross-process session backend (Redis)
- Streaming response variants

---

## 12. Glossary

| Term | Definition |
| --- | --- |
| **AQL** | ArangoDB Query Language — the canonical query language for ArangoDB |
| **AgenticSchemaAnalyzer** | The class in `arangodb-schema-analyzer` (PyPI) that introspects an ArangoDB database and emits a `MappingBundle`. The same package was originally repo-named `arango-schema-mapper`. |
| **DAWG** | Data Access Working Group — the W3C group whose SPARQL 1.1 evaluation test suite is the conformance ground-truth |
| **`fingerprint_physical_shape`** | Cheap structural fingerprint (collections + index digests) — invalidates the mapping cache when topology changes |
| **`fingerprint_physical_counts`** | Shape fingerprint extended with per-collection `count()` — distinguishes "same schema, different volume" from "schema unchanged" |
| **Hybrid schema** | An ArangoDB schema that uses two or more of the physical models (`COLLECTION` / `LABEL` / `RPT` / `DOCUMENT`) at the same time. *Not* a fifth model — just the case where the bundle's `physicalMapping.entities[*].style` values are mixed. |
| **LPG** | Labeled Property Graph — physical model where one shared collection holds multiple OWL classes, discriminated by a `typeField`. OWL annotation: `phys:mappingStyle "LABEL"`. |
| **`MappingBundle`** | The wire-format dict returned by both the heuristic detector and the analyzer: `{conceptualSchema, physicalMapping, metadata, owl_turtle?}`. The `arango_sparql.translate.resolver.SchemaResolver` consumes the `physicalMapping` half. |
| **`mapping_from_wire_dict`** | Spelling normaliser (snake_case ↔ camelCase) shared with the sister project. Single entry-point for parsing analyzer output and OWL-derived mappings. |
| **PG** | Property Graph — physical model where each OWL class lives in its own ArangoDB collection. OWL annotation: `phys:mappingStyle "COLLECTION"`. |
| **`pyoxigraph`** | Python bindings for the Rust [Oxigraph](https://github.com/oxigraph/oxigraph) RDF store; used here as the W3C-compliant reference triplestore for cross-validation |
| **RPC routes** | The service's native JSON contract (§5.1), distinguished from the W3C SPARQL Protocol endpoint (§5.2) |
| **RPT** | Resource-style triples / RDF physical layout — a triple-row collection (legacy default name `_triples`) with `subject_uri` / `predicate` / `object_uri` / `object_value` columns. The legacy Foxx `arango-sparql` service's default storage shape. OWL annotation: `phys:mappingStyle "RPT"`. |
| **`SchemaResolver`** | The single module in `arango_sparql.translate.resolver` that reads OWL `phys:*` annotations and dispatches the visitor's read pattern by `style`. |
| **Schema warning** | A non-fatal advisory emitted by `SchemaResolver` or the schema-detection layer when a resolution succeeds via fallback or the operator should review a low-confidence inference; carries a `W_SCHEMA_*` code |
| **Service Description** | The W3C-spec'd Turtle document a SPARQL endpoint returns from `GET /sparql` (no query) advertising its capabilities |
| **`shardFamilies`** | Optional `physicalMapping` block from the analyzer naming the related shards a cross-shard query must broadcast across. The translator emits a `WITH @@coll1, @@coll2, …` clause when present. |
| **`tenantScope`** | Per-entity metadata block from the analyzer naming the tenant discriminator field. The translator inserts `FILTER doc.<tenantField> == @<tenantBind>` for every read of that entity. |
| **TCK** | Test Compatibility Kit — the openCypher equivalent of DAWG, used by the sister project `arango-cypher-py` |

---

*Last updated alongside the PRD-rewrite commit on `main` (post `v0.1.0`
tag). When this document drifts from the code, the code wins — open a PR
to re-sync.*

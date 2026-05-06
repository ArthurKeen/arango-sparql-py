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
3. **Adapts to the user's physical ArangoDB schema** — including hybrid
   document + edge layouts and multi-class collections — via an OWL ontology
   produced by [`arango-schema-mapper`](https://github.com/ArthurKeen/arango-schema-mapper).
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
3. **Hybrid-schema parity with the legacy Foxx service** — every
   `references/arango-sparql/test/` fixture that uses a hybrid (document +
   edge + multi-class) layout has a corresponding golden case under
   `tests/translate/` that emits semantically equivalent AQL.
4. **Operational parity with `arango-cypher-py`** — same connection /
   session model, same public-mode CORS posture, same observability log
   shape, same rate-limit buckets, same SSRF guard.
5. **Public release readiness** — repo published, CI green on every
   supported Python version, MIT LICENSE, CONTRIBUTING + SECURITY
   documents, repeatable Docker-Compose dev loop.

---

## 4. Architecture overview

```
                 ┌──────────────────────────────────────────────┐
   SPARQL  ───►  │  rdflib parser → algebra translateQuery      │
                 │           │                                  │
                 │           ▼                                  │
                 │  AlgebraVisitor (one visit_<Node> per op)    │
                 │           │                                  │
                 │           ▼                                  │
                 │  AqlQueryBuilder (parameterised, bind-only)  │  ◄── SchemaResolver
                 │           │                                            (OWL/Turtle
                 │           ▼                                             from
   AQL    ◄──── │  TranslateResult{aql, bind_vars, warnings,                arango-
                 │                  schema_warnings}                         schema-
                 └──────────────────────────────────────────────┘            mapper)
                                  ▲
                                  │
                ┌─────────────────┴─────────────────┐
                │                                   │
   FastAPI service                       NL2SPARQL pipeline
   (RPC routes + Protocol)               (LLM + repair loop)
                │                                   │
                └─────────► python-arango ◄─────────┘
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
| Schema mapping | `arango_sparql/translate/resolver.py` |
| Typed errors | `arango_sparql/errors.py` |
| FastAPI app + middleware | `arango_sparql/service/app.py` |
| Pydantic models | `arango_sparql/service/models.py` |
| Sessions, rate-limit, SSRF, redaction | `arango_sparql/service/security.py` |
| Routes | `arango_sparql/service/routes/{health,connect,sparql,nl}.py` |
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

## 6. Schema model

### 6.1 OWL contract

The translator does not invent collection names. Every concrete
SPARQL→AQL translation requires an OWL ontology (in Turtle) that the
[`arango-schema-mapper`](https://github.com/ArthurKeen/arango-schema-mapper)
attaches to with three annotation properties under either of the two
historical `phys:` namespaces (both accepted, see `resolver.py`):

| Annotation | On | Meaning |
| --- | --- | --- |
| `phys:collectionName` | `owl:Class` | ArangoDB document collection name |
| `phys:edgeCollectionName` | `owl:ObjectProperty` | ArangoDB edge collection name |
| `phys:typeField` + `phys:typeValue` | `owl:Class` | Multi-class collection discriminator |

The `arango_sparql.translate.resolver.SchemaResolver` is the only reader of
these annotations. Visitors call `resolve_class(iri)` and `resolve_property(iri)`
and never touch the ontology graph directly.

### 6.2 Supported physical schema shapes

| Shape | Status | Acceptance test |
| --- | --- | --- |
| **Document-only** — every OWL class maps 1:1 to its own collection; every datatype property maps to a top-level attribute | ✅ shipped | `tests/translate/bgp_select.yml` (every case but `hybrid_collection_emits_type_filter`) |
| **Hybrid multi-class** — multiple OWL classes share one Arango collection, discriminated by `phys:typeField`/`phys:typeValue` | ✅ shipped | `tests/translate/bgp_select.yml :: hybrid_collection_emits_type_filter` |
| **Edge-collection traversal** — OWL `ObjectProperty` resolves to a `phys:edgeCollectionName` and SPARQL traversal lowers to AQL `FOR v, e IN OUTBOUND` | 🟡 planned for v1.0 | `visit_BGP` currently raises `UnsupportedSparqlError("Object property … requires edge traversal")` — see top XFAIL bucket "object property requires edge traversal" in `COVERAGE_REPORT.md` |
| **Property-path expansion** — `MulPath` / `SequencePath` / `AlternativePath` lowered to AQL traversal with a depth bound | 🔴 deferred to v1.1 | tracked in `tests/w3c/COVERAGE_REPORT.md` under the `MulPath` / `SequencePath` XFAIL buckets |
| **Named-graph dispatch** — `GRAPH ?g { … }` resolves to a per-graph collection or to a graph-name attribute on a shared collection | 🔴 deferred to v1.2 | tracked in `COVERAGE_REPORT.md` under the `Graph` XFAIL bucket |
| **Federated `SERVICE`** — out of scope (see §2) | ❌ won't fix in v1 | — |

### 6.3 Schema warnings (non-fatal)

When the resolver can do the right thing but the operator probably wants to
know, it emits a `W_SCHEMA_*` advisory rather than throwing:

| Code | Trigger |
| --- | --- |
| `W_SCHEMA_UNMAPPED_IRI` | A predicate IRI is not declared in the ontology. The resolver falls back to the IRI's local name as the AQL attribute and surfaces the warning so the operator can update the ontology. |
| `W_SCHEMA_DEFAULT_COLLECTION` | A class is declared `owl:Class` but lacks `phys:collectionName`. The resolver falls back to the IRI's local name as the collection name. |

These appear in the `TranslateResponse.schema_warnings` field (separate from
the general `warnings` list) so the UI can render them in a dedicated
"schema-mapping advisories" panel.

---

## 7. NL → SPARQL pipeline

`arango_sparql/nl2sparql/` mirrors `arango_cypher/nl2cypher/` with these
deliberate differences:

| Concern | Decision |
| --- | --- |
| Output language | SPARQL 1.1 SELECT/ASK (CONSTRUCT/DESCRIBE only when those visitors ship) |
| Schema delivery to the LLM | Turtle serialisation of the loaded OWL ontology (LLMs read Turtle very well); falls back to a JSON-shape mapping when no ontology is supplied |
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
| W3C DAWG translation-only harness | `w3c` | ~ 15 s | nightly (separate workflow, post-v1.0) |
| W3C live-execution harness (Docker) | `w3c` + `integration` | ~ 60 s | nightly + on-demand |
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

### 10.3 Coverage targets per release

| Release | W3C query-evaluation | Cross cases | Goldens |
| --- | --- | --- | --- |
| v0.1 (current) | 15.0 % | 39 | 50+ |
| v0.5 | 20 % | 60 | 80 |
| **v1.0 (acceptance)** | **≥ 25 %** | **≥ 80** | **≥ 100** |
| v1.1 | 35 % (after `MulPath` / `SequencePath`) | 100 | 130 |

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

- W3C SPARQL 1.1 Protocol endpoint (§5.2)
- Service Description response
- Result-format content negotiation (JSON / XML / CSV / TSV)
- Edge-collection traversal in `visit_BGP` (the "object property requires
  edge traversal" XFAIL bucket goes to zero)
- ASK / SELECT response in W3C SPARQL Results shapes
- Hybrid-schema parity with the legacy Foxx fixtures
- W3C query-evaluation coverage ≥ 25 %
- Full nightly W3C workflow on `main`
- First public PyPI release

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
| **DAWG** | Data Access Working Group — the W3C group whose SPARQL 1.1 evaluation test suite is the conformance ground-truth |
| **Hybrid schema** | An ArangoDB schema that mixes document and edge collections, and/or stores multiple OWL classes in one collection with a discriminator field |
| **`pyoxigraph`** | Python bindings for the Rust [Oxigraph](https://github.com/oxigraph/oxigraph) RDF store; used here as the W3C-compliant reference triplestore for cross-validation |
| **RPC routes** | The service's native JSON contract (§5.1), distinguished from the W3C SPARQL Protocol endpoint (§5.2) |
| **Schema warning** | A non-fatal advisory emitted by `SchemaResolver` when a resolution succeeds via fallback; carries a `W_SCHEMA_*` code |
| **Service Description** | The W3C-spec'd Turtle document a SPARQL endpoint returns from `GET /sparql` (no query) advertising its capabilities |
| **TCK** | Test Compatibility Kit — the openCypher equivalent of DAWG, used by the sister project `arango-cypher-py` |

---

*Last updated for translator commit `7169efe`. When this document drifts
from the code, the code wins — open a PR to re-sync.*

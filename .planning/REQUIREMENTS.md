# Requirements: arango-sparql-py

**Defined:** 2026-07-15
**Core Value:** Deterministic W3C-grounded SPARQL→AQL correctness stays sacred (never regress), while NL→SPARQL translation quality becomes measurable and improvable.

> Source of the 16 PRD requirements: `docs/architecture/PRD.md` §3 "Success criteria
> (v1.0 acceptance)" — the declared contract, each criterion independently measurable
> by a named test/artefact. The 4 NL-* requirements are derived here for the active
> NL→SPARQL quality workstream (the eval harness + few-shot are the only real gaps in
> an otherwise-shipped v1).

## v1 Requirements

### Transpiler Core

- [x] **REQ-w3c-coverage** (PRD §3.1): W3C DAWG translation coverage ≥ 25%, no single XFAIL bucket > 30% of remaining failures. *Current: 96.4% query-eval; 30%-clause consciously accepted (dominant bucket = deferred SERVICE).* — acceptance: `tests/w3c/COVERAGE_REPORT.md`, `analyze_coverage.py --write`
- [x] **REQ-physical-model-coverage** (PRD §3.3): Correct AQL against every §6.1 shape — PG (`COLLECTION`), LPG (`LABEL`), RPT (`_triples`), plain `DOCUMENT`, PG+LPG hybrids, both edge styles. — acceptance: `tests/translate/{bgp_select,hybrid,rpt}.yml`, `tests/cross/*`
- [x] **REQ-hybrid-bgp-translation** (PRD §3.4): One BGP touching ≥ 2 physical models → single AQL query joined on shared subject URI. — acceptance: `tests/translate/hybrid.yml`, `tests/cross/test_hybrid_cross.py`
- [x] **REQ-schema-detection** (PRD §3.5): Both detectors ship (heuristic + analyzer-backed); analyzer wins on `strategy="auto"`; zero false negatives on fixture corpus. — acceptance: `tests/schema/test_classify.py`, `test_acquire.py`

### Protocol & HTTP Surface

- [x] **REQ-sparql-protocol-endpoint** (PRD §3.2): Conformant W3C SPARQL 1.1 Protocol endpoint — `GET/POST /sparql` accept negotiation (JSON/XML/CSV/TSV, RFC 9110 q-values); Service Description on empty GET; documented error contract (405/400/422/406/503/504/429/401). — acceptance: `tests/test_sparql_protocol_*.py`
- [x] **REQ-schema-http-parity** (PRD §3.6): All 9 schema/mapping HTTP routes exist with documented shapes, matching `arango-cypher-py`. — acceptance: `tests/test_service_schema_routes.py`

### Operational, Security & Privacy

- [x] **REQ-operational-parity** (PRD §3.8): Operational parity with `arango-cypher-py` — session/connect/public-mode/CORS/rate-limit/SSRF/redaction/startup-guard, one CI test per surface. — acceptance: `tests/parity/test_cypher_py_*.py`
- [x] **REQ-threat-model-mitigations** (PRD §3.13): Every §8.6 STRIDE row has its asserting test (CI-blocking). — acceptance: `tests/security/test_*.py`
- [x] **REQ-privacy-contract** (PRD §3.14): No-bodies-in-logs property test passes; `LOG_FORMAT=json` default emits §9.5 envelope; tenant-label toggles per §17.2. — acceptance: `tests/security/test_no_body_in_logs.py`, `tests/test_log_envelope.py`
- [x] **REQ-config-appendix-normative** (PRD §3.15): Adding a new env var without updating Appendix A fails CI. — acceptance: `tests/test_config_appendix.py`

### Interoperability & Performance

- [ ] **REQ-foxx-parity** (PRD §3.7): Hybrid-schema parity with legacy Foxx `arango-sparql` — ≥ 90% of translatable legacy fixtures have a golden emitting semantically equivalent AQL. — acceptance: `tests/legacy/test_foxx_roundtrip.py` (Docker-gated)
- [ ] **REQ-thirdparty-tool-compat** (PRD §3.10): Every §11.1 verified-compatible tool row has a passing smoke test (≥1 SELECT, 1 ASK, Service Description fetch) — Protégé, YASGUI, SPARQLWrapper, MS Ontology Playground. — acceptance: `tests/integration/test_*_compat.py`
- [ ] **REQ-ontoextract-integration** (PRD §3.11): `arango-ontoextract` can point its Q7 endpoint at us, seed via `/mapping/export-owl`, accept a curated OWL push via `/mapping/import-owl`. — acceptance: `tests/integration/test_aoe_roundtrip.py` (Docker-gated)
- [ ] **REQ-performance-slos** (PRD §3.12): Every §9.4 perf budget row passes within ≤ 25% of stated p95 (CI-blocking on > 25% regression). — acceptance: `tests/perf/test_*.py`

### UI Workbench

- [ ] **REQ-ui-parity** (PRD §3.9): UI feature parity with `arango-cypher-py` workbench — every §10.2/§10.3 capability-table row has a passing Playwright test. *Shell + most rows ship; Playwright/axe/Lighthouse harness deferred; WP-UI-CAT / WP-UI-TENANT / WP-UI-CORR backend-blocked.* — acceptance: `ui/tests/playwright/parity.spec.ts` (CI-blocking)

### NL→SPARQL Quality (ACTIVE workstream)

- [ ] **NL-EVAL-01**: NL→SPARQL eval harness implemented — `tests/nl2sparql/eval/runner.py::run()` + `write_report()` (currently `NotImplementedError`) execute each corpus entry against each configured provider and emit JSON+Markdown reports; eval marker wired into CI. — acceptance: eval marker green in CI with a scripted provider
- [ ] **NL-EVAL-02**: Seed corpus authored — `corpus.yml` + `configs.yml` created, `baseline.json` checked in as the regression gate; **NL→SPARQL pass-rate becomes a tracked metric**. — acceptance: `baseline.json` present; harness reports a numeric pass-rate
- [ ] **NL-FEW-01**: `arango_sparql/nl2sparql/fewshot.py` — BM25 few-shot index over the curated corpus (≤ 3 shots per rule-300) feeding the wired-but-empty `PromptBuilder.few_shot_examples` seam. — acceptance: few-shot examples appear in built prompts; unit tests pass
- [ ] **NL-FEW-02**: Measurable accuracy lift — few-shot run shows a **positive NL→SPARQL pass-rate delta over `baseline.json`** via the Phase 6 harness. — acceptance: eval report delta > 0

### Release

- [ ] **REQ-public-release-readiness** (PRD §3.16): Repo public; CI green on Python 3.11/3.12/3.13 + ArangoDB 3.11/3.12; MIT LICENSE + CONTRIBUTING + SECURITY + operational runbook; repeatable `docker compose up` dev loop; SBOM on the v1.0 release tag. — acceptance: GitHub releases page, CI history, `docker compose up && curl /health/ready`

## v2 Requirements

Deferred to future release. Tracked but not in the current roadmap.

- **SERVICE / federated query** — Service Description would advertise `sd:BasicFederatedQuery`; dominant W3C XFAIL bucket
- **DEC-0002 Option B/C** — Document-emulated cross-subject OPTIONAL (+0.8pp W3C) and full multi-model `_uri→collection` resolution; travel with the federation slice
- **SPARQL 1.1 Update** — write path; currently 405

## Out of Scope

| Feature | Reason |
|---------|--------|
| SPARQL 1.1 Update (INSERT/DELETE/LOAD/…) | Writes go through AQL directly; endpoint returns 405 |
| Federated query (`SERVICE`) | Deferred to possible v2; no `sd:BasicFederatedQuery` |
| RDFS/OWL inferencing/reasoning | Ontology is mapping metadata, not a reasoning surface |
| Cross-process multi-tenancy | Sessions per-process in-memory; needs sticky-session LB |
| Replacing AQL | This is a transpiler, not a competing engine |
| Asking the LLM for AQL directly | Forbidden by rule-300; LLM emits SPARQL only |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| REQ-w3c-coverage | Phase 1 | Complete |
| REQ-physical-model-coverage | Phase 1 | Complete |
| REQ-hybrid-bgp-translation | Phase 1 | Complete |
| REQ-schema-detection | Phase 1 | Complete |
| REQ-sparql-protocol-endpoint | Phase 2 | Complete |
| REQ-schema-http-parity | Phase 2 | Complete |
| REQ-operational-parity | Phase 3 | Complete |
| REQ-threat-model-mitigations | Phase 3 | Complete |
| REQ-privacy-contract | Phase 3 | Complete |
| REQ-config-appendix-normative | Phase 3 | Complete |
| REQ-foxx-parity | Phase 4 | Pending |
| REQ-thirdparty-tool-compat | Phase 4 | Pending |
| REQ-ontoextract-integration | Phase 4 | Pending |
| REQ-performance-slos | Phase 4 | Pending |
| REQ-ui-parity | Phase 5 | Pending |
| NL-EVAL-01 | Phase 6 | Pending (ACTIVE) |
| NL-EVAL-02 | Phase 6 | Pending (ACTIVE) |
| NL-FEW-01 | Phase 7 | Pending |
| NL-FEW-02 | Phase 7 | Pending |
| REQ-public-release-readiness | Phase 8 | Pending |

**Coverage:**
- v1 requirements: 20 total (16 PRD + 4 NL)
- Mapped to phases: 20
- Unmapped: 0 ✓
- Already satisfied (Complete): 10 across Phases 1–3

---
*Requirements defined: 2026-07-15*
*Last updated: 2026-07-15 after new-project-from-ingest bootstrap*

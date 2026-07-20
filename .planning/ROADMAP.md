# Roadmap: arango-sparql-py

## Overview

This is a full v1 roadmap bootstrapped over a **mature repo**: the deterministic
SPARQL 1.1 → AQL transpiler, the W3C SPARQL 1.1 Protocol FastAPI service, and the UI
shell already ship (W3C DAWG query-eval coverage at 96.4%). Phases 1–3 are therefore
marked **Complete** (shipped pre-GSD) and are held as a **no-regression gate** rather
than re-planned. The active journey targets the user's actual goal — making the
NL→SPARQL layer's quality **measurable then improvable**: Phase 6 stands up the eval
harness + seed corpus + checked-in `baseline.json`, Phase 7 adds the BM25 few-shot
index and proves an accuracy lift against that baseline. Phases 4, 5, and 8 close out
interop/perf verification, UI parity, and public release. Throughout, W3C query-eval
coverage must never drop below 96.4%.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Deterministic transpiler core** - SPARQL 1.1 → AQL across all physical models; W3C 96.4% (COMPLETE, pre-GSD)
- [x] **Phase 2: SPARQL 1.1 Protocol service + schema HTTP surface** - Conformant `/sparql` endpoint + 9 schema routes (COMPLETE, pre-GSD)
- [x] **Phase 3: Operational, security & privacy parity** - Session/CORS/SSRF/redaction/STRIDE/log-envelope/config-gate (COMPLETE, pre-GSD)
- [ ] **Phase 4: Interoperability & performance verification** - Foxx roundtrip, third-party tools, ontoextract, perf SLOs
- [ ] **Phase 5: UI workbench parity completion** - Playwright/a11y CI harness + 3 backend-blocked WPs
- [x] **Phase 6: NL→SPARQL eval harness + seed corpus** - Make NL quality measurable; check in `baseline.json` gate (FIRST ACTIVE) (completed 2026-07-15)
- [ ] **Phase 7: NL→SPARQL few-shot index** - BM25 ≤3-shot index feeding PromptBuilder; prove pass-rate lift
- [ ] **Phase 8: Public release readiness** - Public repo, CI matrix, license/docs/runbook, SBOM on v1.0 tag

## Phase Details

### Phase 1: Deterministic transpiler core

**Goal**: A correct, layout-agnostic SPARQL 1.1 → AQL transpiler covering every physical schema shape.
**Depends on**: Nothing (first phase)
**Requirements**: REQ-w3c-coverage, REQ-physical-model-coverage, REQ-hybrid-bgp-translation, REQ-schema-detection
**Success Criteria** (what must be TRUE):

  1. W3C DAWG query-eval coverage ≥ 96.4% (244/253 pass), regenerable via `analyze_coverage.py --write`
  2. Correct AQL emitted for PG / LPG / RPT / DOCUMENT + PG-LPG hybrids + both edge styles
  3. One BGP spanning ≥ 2 physical models produces a single AQL query joined on subject URI (not split, not rejected)
  4. Both schema detectors ship; analyzer wins on `strategy="auto"` with zero false negatives on the fixture corpus

**Plans**: Shipped pre-GSD (no plans authored)
**Status**: COMPLETE — held as no-regression gate

### Phase 2: SPARQL 1.1 Protocol service + schema HTTP surface

**Goal**: A conformant W3C SPARQL 1.1 Protocol HTTP service with the full schema/mapping route surface.
**Depends on**: Phase 1
**Requirements**: REQ-sparql-protocol-endpoint, REQ-schema-http-parity
**Success Criteria** (what must be TRUE):

  1. `GET/POST /sparql` honours `Accept` for JSON/XML/CSV/TSV with RFC 9110 q-value parsing
  2. Empty `GET /sparql` returns the Service Description as `text/turtle`
  3. Documented error contract in force (405 on Update forms; 400/422/406/503/504/429/401 per §5.2)
  4. All 9 schema/mapping routes exist with documented response shapes matching `arango-cypher-py`

**Plans**: Shipped pre-GSD (no plans authored)
**Status**: COMPLETE — held as no-regression gate
**UI hint**: yes

### Phase 3: Operational, security & privacy parity

**Goal**: Production-grade operational, security, and privacy behaviour at parity with `arango-cypher-py`.
**Depends on**: Phase 2
**Requirements**: REQ-operational-parity, REQ-threat-model-mitigations, REQ-privacy-contract, REQ-config-appendix-normative
**Success Criteria** (what must be TRUE):

  1. Session / connect / public-mode / CORS / rate-limit / SSRF / redaction / startup-guard each have a passing parity test
  2. Every §8.6 STRIDE threat-matrix row has an asserting, CI-blocking security test
  3. No request/response bodies appear in logs; `LOG_FORMAT=json` default emits the §9.5 envelope
  4. Adding an env var without updating Appendix A fails CI

**Plans**: Shipped pre-GSD (no plans authored)
**Status**: COMPLETE — held as no-regression gate

### Phase 4: Interoperability & performance verification

**Goal**: Prove drop-in compatibility with the legacy Foxx service, third-party SPARQL tools, `arango-ontoextract`, and the performance budgets.
**Depends on**: Phase 3
**Requirements**: REQ-foxx-parity, REQ-thirdparty-tool-compat, REQ-ontoextract-integration, REQ-performance-slos
**Success Criteria** (what must be TRUE):

  1. ≥ 90% of translatable legacy Foxx fixtures pass a golden emitting semantically equivalent AQL (`test_foxx_roundtrip.py`, Docker-gated)
  2. Each §11.1 verified-compatible tool (Protégé, YASGUI, SPARQLWrapper, MS Ontology Playground) passes a smoke test (SELECT + ASK + Service Description)
  3. `arango-ontoextract` completes the Q7 roundtrip via `/mapping/export-owl` + `/mapping/import-owl` (Docker-gated, both services live)
  4. Every §9.4 performance budget row passes within ≤ 25% of its stated p95

**Plans**: TBD
**Status**: Not started

### Phase 5: UI workbench parity completion

**Goal**: Close the remaining UI parity gap so every workbench capability row is verified and the backend-blocked WPs unblock.
**Depends on**: Phase 4
**Requirements**: REQ-ui-parity
**Success Criteria** (what must be TRUE):

  1. Every §10.2/§10.3 capability-table row has a passing Playwright test (`ui/tests/playwright/parity.spec.ts`, CI-blocking)
  2. Playwright/axe/Lighthouse CI harness exists and runs (WP-UI-A11Y completed)
  3. Backend slices land to unblock WP-UI-CAT (async schema introspect + status), WP-UI-TENANT (tenant catalogue / `/session/tenant`), WP-UI-CORR (translator source-map metadata)

**Plans**: TBD
**Status**: Not started
**UI hint**: yes

### Phase 6: NL→SPARQL eval harness + seed corpus

**Goal**: Make NL→SPARQL translation quality measurable — implement the stubbed eval harness, author a seed corpus, and check in a baseline as the regression gate. This is the first active phase and the user's immediate goal.
**Depends on**: Phase 3 (transpiler + NL pipeline already ship; interop/UI phases are independent and can run in parallel)
**Requirements**: NL-EVAL-01, NL-EVAL-02
**Success Criteria** (what must be TRUE):

  1. `tests/nl2sparql/eval/runner.py::run()` and `write_report()` are implemented (no `NotImplementedError`) and run each corpus entry against each configured provider
  2. `corpus.yml` + `configs.yml` are authored; the eval marker runs green in CI with a `ScriptedProvider`
  3. The harness reports a numeric **NL→SPARQL pass-rate** (JSON + Markdown) — the PRIMARY quality metric now exists
  4. `baseline.json` is checked in and enforced as the regression gate
  5. W3C DAWG query-eval coverage remains ≥ 96.4% (no transpiler regression)

**Plans**: 3 plansPlans:
**Wave 1**

- [x] 06-01-PLAN.md — Author corpus.yml + configs.yml seed data (incl. deliberate near-miss) [NL-EVAL-02]

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 06-02-PLAN.md — Implement runner.py (run/write_report/judge) + test_eval.py gate + baseline.json [NL-EVAL-01, NL-EVAL-02]

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 06-03-PLAN.md — Add CI eval job + verify W3C coverage ≥ 96.4% no-regression guard [NL-EVAL-01]

**Status**: Planned — FIRST ACTIVE

### Phase 06.1: Re-point nl2sparql onto arango-query-core shared engine (INSERTED)

**Goal:** Re-point the `nl2sparql` adapter off its private generate→validate→repair loop and onto the shared `arango_query_core.nl.NLQueryEngine`, implemented as a `SparqlAdapter` satisfying the 5-seam `QueryLanguageAdapter` protocol. **Behavior-preserving refactor** — the prerequisite that makes engine-side SOTA (few-shot/dense retrieval, etc.) reachable from SPARQL and inherited by Cypher.
**Requirements**: None new — behavior-preserving; gated by the Phase 6 baseline (no NL-EVAL-01/02 regression).
**Depends on:** Phase 6 (needs the eval harness + baseline.json to prove behavior is preserved).
**Success Criteria** (what must be TRUE):

  1. `nl2sparql` exposes a `SparqlAdapter` implementing `arango_query_core.nl.seams.QueryLanguageAdapter` (all 5 seams: grammar prompt, few-shot index [None for now], validate=deterministic transpile, repair_hint, guardrails); `NlPipeline.run()` drives `NLQueryEngine` instead of its own `PromptBuilder`→`LLMClient`→`RepairLoop` loop
  2. `arango-query-core` is a real dependency (the `nl` extra pin in `pyproject.toml` resolves it; editable/PyPI per the 0.2.0 plan) and imports cleanly
  3. Scripted eval pass-rate is **UNCHANGED at 0.833 (5/6)** with identical per-case verdicts vs `baseline.json` — `RUN_EVAL=1 pytest -m eval` stays green
  4. `NLResult` is mapped back to `PipelineOutcome` preserving the public shape (`sparql`, `aql`, `bind_vars`, `warnings`, `latency_ms`, `repaired`) — re-translate the final query once to recover `aql`/`bind_vars` the `validate()` seam discards
  5. W3C DAWG query-eval coverage remains ≥ 96.4% (deterministic transpiler untouched); existing non-eval suite stays green
  6. The `/nl-explain` path and cost/audit (`LLMCallRecord`) behavior are preserved, or any deviation is explicitly documented

**Plans**: 3 plans
**Status**: Planned

Plans:
**Wave 1** *(parallel — no file overlap)*
- [ ] 06.1-01-PLAN.md — Formalize arango-query-core as a real dependency in the pyproject `nl` extra + clean-import guard
- [ ] 06.1-02-PLAN.md — Provider bridge (LLMClient→LLMProvider, per-call LLMCallRecord) + SparqlAdapter (5 seams); reproduces baseline verdicts

**Wave 2** *(blocked on Wave 1)*
- [ ] 06.1-03-PLAN.md — Re-point NlPipeline.run() onto NLQueryEngine + NLResult→PipelineOutcome mapping (re-translate for aql/bind_vars) + cost/audit doc + behavior-preservation gate

### Phase 7: NL→SPARQL few-shot index

**Goal**: Populate + wire the shared engine's few-shot seam (`arango_query_core.nl.FewShotIndex`, reached via the re-pointed `SparqlAdapter.few_shot_index()`) with a curated SPARQL exemplar corpus, and prove it lifts NL→SPARQL pass-rate against the Phase 6 baseline. (Engine-side change — Cypher inherits the retrieval upgrade.)
**Depends on**: Phase 06.1 (needs `nl2sparql` running on the shared engine so few-shot lands engine-side and reaches SPARQL) + Phase 6 (baseline to prove the lift)
**Requirements**: NL-FEW-01, NL-FEW-02
**Success Criteria** (what must be TRUE):

  1. A curated SPARQL exemplar corpus is authored and loaded via `arango_query_core.nl.FewShotIndex.from_corpus_files`; `SparqlAdapter.few_shot_index()` returns it (≤ 3 shots per query, rule-300 budget)
  2. Retrieved examples appear in the engine-built prompt's `## Examples` section (the `NLQueryEngine` few-shot path), not the standalone `PromptBuilder`
  3. A few-shot eval run shows a **positive pass-rate delta over `baseline.json`** via the Phase 6 harness
  4. W3C DAWG query-eval coverage remains ≥ 96.4% (no transpiler regression)

**Plans**: TBD
**Status**: Not started

### Phase 8: Public release readiness

**Goal**: Ship v1.0 publicly with a green CI matrix, complete governance docs, and a signed-off SBOM.
**Depends on**: Phase 7 (and Phases 4–5 verification)
**Requirements**: REQ-public-release-readiness
**Success Criteria** (what must be TRUE):

  1. Repo is public with MIT LICENSE + CONTRIBUTING + SECURITY + operational runbook published
  2. CI is green across Python 3.11/3.12/3.13 and ArangoDB 3.11/3.12
  3. `docker compose up && curl /health/ready` succeeds as a repeatable dev loop
  4. An SBOM artefact is attached to the v1.0 release tag

**Plans**: TBD
**Status**: Not started

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8.
Phases 1–3 are already Complete. The active workstream begins at **Phase 6**
(NL→SPARQL). Phases 4, 5, and 6 have no hard inter-dependency on each other and may
be sequenced by priority — the user's directive puts NL→SPARQL first.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Deterministic transpiler core | shipped | Complete | pre-GSD (mature) |
| 2. Protocol service + schema HTTP | shipped | Complete | pre-GSD (mature) |
| 3. Operational/security/privacy parity | shipped | Complete | pre-GSD (mature) |
| 4. Interop & performance verification | 0/TBD | Not started | - |
| 5. UI workbench parity completion | 0/TBD | Not started | - |
| 6. NL→SPARQL eval harness + corpus | 3/3 | Complete    | 2026-07-15 |
| 7. NL→SPARQL few-shot index | 0/TBD | Not started | - |
| 8. Public release readiness | 0/TBD | Not started | - |

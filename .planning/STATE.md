---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
last_updated: "2026-07-21T02:19:01.623Z"
last_activity: 2026-07-21
progress:
  total_phases: 10
  completed_phases: 2
  total_plans: 10
  completed_plans: 9
  percent: 20
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-07-15)

**Core value:** Deterministic W3C SPARQL→AQL correctness stays sacred (never regress); NL→SPARQL quality becomes measurable and improvable.
**Current focus:** Phase 06.2 — nl-to-sparql-harder-corpus-and-genuine-live-model-baseline

## Current Position

Phase: 06.2 (nl-to-sparql-harder-corpus-and-genuine-live-model-baseline) — EXECUTING
Plan: 4 of 4
Status: Ready to execute
Last activity: 2026-07-21

Progress: [█████████░] 90%

## Performance Metrics

**Velocity:**

- Total plans completed: 3 (Phases 1–3 shipped pre-GSD, outside GSD tracking)
- Average duration: n/a
- Total execution time: n/a

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1–3 | shipped pre-GSD | - | - |
| 06 | 3 | - | - |

**Recent Trend:**

- Last 5 plans: n/a
- Trend: n/a (bootstrap)

*Updated after each plan completion*
| Phase 06 P01 | 6min | 2 tasks | 3 files |
| Phase 06 P02 | 8min | 2 tasks | 3 files |
| Phase 06 P03 | 6min | 2 tasks | 1 files |
| Phase 06.2 P01 | ~10min | 3 tasks | 2 files |
| Phase 06.2 P02 | ~15min | 3 tasks | 2 files |
| Phase 06.2 P03 | 10min | 3 tasks | 3 files |

## Accumulated Context

### Roadmap Evolution

- Phase 06.1 inserted after Phase 6: Re-point nl2sparql onto arango-query-core shared engine (prerequisite for engine-side SOTA) (URGENT)
- Phase 06.2 inserted after Phase 6: harder corpus + genuine live-model baseline (unblocks measurable few-shot lift) (URGENT)

### Decisions

Decisions are logged in PROJECT.md Key Decisions table. Recent decisions affecting current work:

- DEC-0001: Named graphs → per-document `_graph` attribute (Accepted, NOT locked)
- DEC-0002: Cross-subject OPTIONAL LeftJoin — Option A shipped, B/C deferred (Partially resolved, NOT locked)
- Establish NL eval BEFORE tuning (Phase 6 sequenced first); port harness + few-shot from `arango-cypher` sister repo
- [Phase 06]: corpus.yml reuses bgp_select.yml's ontology prefix header (phys:collectionName) so SchemaResolver.from_turtle resolves without modification
- [Phase 06]: Deliberate near-miss case drops the age binding in its scripted response vs. gold, keeping the scripted pass-rate intentionally below 1.0
- [Phase 06]: configs.yml documents openai-gpt4o-mini as the real-provider sweep shape; CI enforces the scripted config only
- [Phase 06]: baseline.json authored from the actual run('scripted') output (5/6 pass, pass_rate=0.8333) rather than an aspirational number
- [Phase 06]: Per-case regression in test_eval.py is a hard gate, not informational only
- [Phase 06]: CI wiring (.github/workflows/ci.yml new eval job) deferred — out of 06-02's file scope
- [Phase 06]: CI eval job installs .[dev,nl,service] and runs RUN_EVAL=1 pytest -m eval; existing test job marker exclusion left unchanged
- [Phase 06]: W3C DAWG query-eval coverage confirmed unchanged at 96.4% (244/253); zero transpiler files touched by Phase 6
- [Phase 06.2-01]: `expect_refusal` pinned as the negatives marker key across corpus data, CorpusCase field, and the _judge branch
- [Phase 06.2-01]: gold-must-parse validator skips refusal cases (expected holds a human rationale, not gold SPARQL); SparqlParseError re-raised as pydantic ValueError
- [Phase 06.2-01]: _load_corpus validates every case then returns the raw dict unchanged (a load-time gate, not a data-flow rewrite)
- [Phase 06.2-01]: BaselineConfig carries optional model/temperature/corpus_sha so Plan 04 folds a live baseline in without re-touching runner.py
- [Phase 06.2-02]: :placed/:knows are dedicated edge collections (phys:edgeCollectionName), not attribute joins — a bare owl:ObjectProperty raises SchemaResolutionError, so the committed Task-1 comment was wrong and its golds could not transpile (fixed)
- [Phase 06.2-02]: property-path positives use :knows/:placed as real graph edges; transitive :knows+/:knows* use the default property_path_max_depth (10) with no knob override
- [Phase 06.2-02]: 16 positive golds added (4 OPTIONAL, 3 aggregation/GROUP BY, 9 property-path/multi-hop); every non-refusal gold proven transpilable by test_gold_transpilable.py
- [Phase 06.2]: [Phase 06.2-03]: 3 expect_refusal negatives — 2 malformed-SPARQL drift-proof triggers + unsupported !(^:knows); all refuse to empty AQL, PASS inverted judge
- [Phase 06.2]: [Phase 06.2-03]: baseline.json regenerated from true run('scripted') — 0.96 (24/25), all 25 cases tracked, near-miss=false, nested schema preserved
- [Phase 06.2]: [Phase 06.2-03]: 0<pass_rate<1 is a SENTINEL; real guard is per-case deliberate-near-miss passed is False (AI-SPEC SC2)

### Pending Todos

- Minor cleanup: legacy `arango_sparql/nl2sparql/_core.py::nl_to_sparql` is a stub returning a comment; real path is `NlPipeline`.

### Blockers/Concerns

- [Phase 5] WP-UI-CAT / WP-UI-TENANT / WP-UI-CORR are backend-blocked (need async introspect, tenant catalogue, translator source-map).
- [Gate] W3C DAWG query-eval coverage must stay ≥ 96.4% throughout the NL workstream (Phases 6–7).
- [Dep] Upstream hard dependency `arangodb-schema-analyzer` pinned ≥0.6.1,<0.7.0.

## Deferred Items

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| Federation | SPARQL `SERVICE` / federated query | v2 | bootstrap |
| OPTIONAL | DEC-0002 Options B/C (doc-emulation + multi-model) | v2 (travels with federation) | bootstrap |
| Write path | SPARQL 1.1 Update | Out of scope (405) | bootstrap |

## Session Continuity

Last session: 2026-07-21T02:18:37.153Z
Stopped at: Completed 06.2-02-PLAN.md — positive difficulty classes (OPTIONAL/aggregation/property-path/multi-hop) landed; Plan 03 next (expect_refusal negatives + baseline.json regen)
Resume file: None

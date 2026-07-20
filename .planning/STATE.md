---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
last_updated: "2026-07-20T23:19:39.192Z"
last_activity: 2026-07-20 -- Phase 06.2 planning complete
progress:
  total_phases: 10
  completed_phases: 2
  total_plans: 10
  completed_plans: 6
  percent: 20
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-07-15)

**Core value:** Deterministic W3C SPARQL→AQL correctness stays sacred (never regress); NL→SPARQL quality becomes measurable and improvable.
**Current focus:** Phase 06.2 — NL→SPARQL harder corpus + genuine live-model baseline

## Current Position

Phase: 06.2
Plan: Not started
Status: Ready to execute
Last activity: 2026-07-20 -- Phase 06.2 planning complete

Progress: [██████████] 100%

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

Last session: 2026-07-15T23:14:56.224Z
Stopped at: Completed 06-03-PLAN.md — Phase 06 all plans complete, ready for verification
Resume file: None

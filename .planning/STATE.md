---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
last_updated: "2026-07-15T23:00:07.696Z"
last_activity: 2026-07-15
progress:
  total_phases: 8
  completed_phases: 0
  total_plans: 3
  completed_plans: 1
  percent: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-07-15)

**Core value:** Deterministic W3C SPARQL→AQL correctness stays sacred (never regress); NL→SPARQL quality becomes measurable and improvable.
**Current focus:** Phase 06 — nl-sparql-eval-harness-seed-corpus

## Current Position

Phase: 06 (nl-sparql-eval-harness-seed-corpus) — EXECUTING
Plan: 2 of 3
Status: Ready to execute
Last activity: 2026-07-15

Progress: [███░░░░░░░] 33%

## Performance Metrics

**Velocity:**

- Total plans completed: 0 (Phases 1–3 shipped pre-GSD, outside GSD tracking)
- Average duration: n/a
- Total execution time: n/a

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1–3 | shipped pre-GSD | - | - |

**Recent Trend:**

- Last 5 plans: n/a
- Trend: n/a (bootstrap)

*Updated after each plan completion*
| Phase 06 P01 | 6min | 2 tasks | 3 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table. Recent decisions affecting current work:

- DEC-0001: Named graphs → per-document `_graph` attribute (Accepted, NOT locked)
- DEC-0002: Cross-subject OPTIONAL LeftJoin — Option A shipped, B/C deferred (Partially resolved, NOT locked)
- Establish NL eval BEFORE tuning (Phase 6 sequenced first); port harness + few-shot from `arango-cypher` sister repo
- [Phase 06]: corpus.yml reuses bgp_select.yml's ontology prefix header (phys:collectionName) so SchemaResolver.from_turtle resolves without modification
- [Phase 06]: Deliberate near-miss case drops the age binding in its scripted response vs. gold, keeping the scripted pass-rate intentionally below 1.0
- [Phase 06]: configs.yml documents openai-gpt4o-mini as the real-provider sweep shape; CI enforces the scripted config only

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

Last session: 2026-07-15T22:58:49.833Z
Stopped at: Bootstrapped PROJECT / REQUIREMENTS / ROADMAP / STATE from ingest intel; Phase 6 ready to plan.
Resume file: None

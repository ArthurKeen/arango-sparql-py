# Synthesis Summary

Single entry point for `gsd-roadmapper`. Produced by `gsd-doc-synthesizer` from
the per-doc classifications in `.planning/intel/classifications/`.

- Mode: new (bootstrap — no pre-existing `.planning/` context)
- Precedence: ADR > SPEC > PRD > DOC
- Repo: arango-sparql-py (SPARQL 1.1 → ArangoDB AQL transpiler + FastAPI service + NL→SPARQL pipeline)

## Doc counts by type (8 total)
- ADR: 2 — `decisions/0001-named-graphs-per-document.md`, `decisions/0002-cross-subject-optional-leftjoin.md` (both redirect stubs; content in PRD Appendix B)
- SPEC: 1 — `.cursor/rules/300-nl2sparql.mdc`
- PRD: 1 — `docs/architecture/PRD.md`
- DOC: 4 — `tests/w3c/COVERAGE_REPORT.md`, `docs/architecture/implementation_plan.md`, `.cursor/skills/sparql-to-aql/SKILL.md`, `docs/architecture/vision.md` (stub)

## Decisions
- 2 extracted; **0 locked**. DEC-0001 (named graphs → per-document `_graph`, Accepted), DEC-0002 (cross-subject OPTIONAL / LeftJoin, Partially resolved: P2 + P1-Option-A shipped, Options B/C deferred). Both authoritatively hosted in PRD Appendix B.1/B.2.
- File: `.planning/intel/decisions.md`

## Requirements
- 16 extracted (REQ-w3c-coverage, -sparql-protocol-endpoint, -physical-model-coverage, -hybrid-bgp-translation, -schema-detection, -schema-http-parity, -foxx-parity, -operational-parity, -ui-parity, -thirdparty-tool-compat, -ontoextract-integration, -performance-slos, -threat-model-mitigations, -privacy-contract, -config-appendix-normative, -public-release-readiness), each mapped to a PRD §3 acceptance criterion + named test/artefact. Non-goals and cross-project integration also captured.
- File: `.planning/intel/requirements.md`

## Constraints
- 6 extracted from the sole SPEC (`300-nl2sparql.mdc`): module layout (protocol), `NL2SparqlResult` dataclass (api-contract), Turtle prompt rules (protocol), few-shot budget ≤ 3 (nfr), SQLite corrections store (schema), forbidden behaviours (protocol).
- File: `.planning/intel/constraints.md`

## Context
- 4 topics: measured W3C coverage; implementation-plan work-package status (incl. 3 backend-blocked UI WPs); SPARQL→AQL porting recipe; vision redirect stub.
- File: `.planning/intel/context.md`

## Conflicts
- **0 blockers, 0 competing-variants, 4 auto-resolved/INFO.**
- INFO: (1) benign cross-ref cycles among redirect stubs and canonical PRD — no synthesis hazard; (2) ADR content lives in PRD appendix, ADR files are stubs; (3) implementation_plan.md self-declared status-vs-intent precedence carve-out (consistent with defaults); (4) PRD §3.1 30%-bucket sub-clause consciously accepted as violated (federation deferred).
- Detail: `.planning/INGEST-CONFLICTS.md`

## Status
READY — no blockers, no competing variants. Safe to route to `gsd-roadmapper`.

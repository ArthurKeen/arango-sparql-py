# Context (DOC intel)

Running notes keyed by topic, appended verbatim-in-substance with source
attribution. Four DOCs classified.

---

## Topic: W3C SPARQL 1.1 DAWG coverage (measured)
- source: /Users/plosiewicz/dev/arango-sparql-py/tests/w3c/COVERAGE_REPORT.md

Measured translation-only coverage (`python tests/w3c/analyze_coverage.py`).
Headline numbers:
- Syntax (positive): 63/63 = 100.0%
- Syntax (negative): 29/43 pass, 14 XFAIL = 67.4%
- Query evaluation: 244/253 pass, 9 XFAIL = 96.4%

XFAIL buckets: `algebra` = 9 (real roadmap gaps — port the visitor method),
`schema` = 0 (should stay 0), `rdflib` = 14 (rdflib parser disagreement, out of
scope). Top algebra XFAILs: `ServiceGraphPattern` ×4 (SPARQL federation,
deferred), `OPTIONAL` cross-subject ×2, `SparqlParse` max-recursion ×2 (both
SERVICE queries), `OPTIONAL` body is `ServiceGraphPattern` ×1. Out-of-scope
counted-not-run types: Update (Positive/Negative syntax + evaluation), Protocol,
Service Description, CSV result-format.

Reproduce: `python tests/w3c/analyze_coverage.py [--write]`;
`pytest -q tests/w3c -m w3c`; `RUN_INTEGRATION=1 ... --live --write` for the live
row.

---

## Topic: Implementation status / work packages
- source: /Users/plosiewicz/dev/arango-sparql-py/docs/architecture/implementation_plan.md

Declared as the **living work-tracking** document, complementary to the PRD:
"the PRD is the spec (what done means) and roadmap (§14); this file is the plan
(discrete WPs, status, files, test signal). When they disagree about **intent**,
the PRD wins; when they disagree about **status**, this file is the source of
truth." (Self-declared precedence carve-out — surfaced in INGEST-CONFLICTS.)

Work-package IDs are stable `WP-<AREA>-<N|NAME>`; statuses Done / In progress /
Planned / Deferred / Blocked. Snapshot highlights:
- Backend done: cross-subject OPTIONAL (ADR-0002 P1-A) + OPTIONAL-rebind-in-MINUS (P2), dedicated-DB auto-provision on boot, analyzer-mapping merge, named-graph down-select, LLM provider resolution, `AQLQueryExecuteError → 400`.
- UI shell (WP-UI-SHELL Phase 0–4) done; most UI WPs done (NL Ask bar, suggestions, graph selector, ontology panel, results panel, history, schema-graph scalability, SPARQL/AQL editors, explain/profile, command palette, light theme).
- **Blocked WPs (backend dependency):** WP-UI-CAT (needs async schema introspect + `pending`/`analyzing` status; `/schema/introspect` is currently synchronous), WP-UI-TENANT (no tenant-catalogue endpoint / `/session/tenant` route), WP-UI-CORR (translator emits no source-map metadata). Each needs a backend slice first.
- **Partial:** WP-UI-A11Y (app-code a11y + i18n scaffold done; Playwright/axe/Lighthouse CI deferred — no harness yet).
- client↔models audit (2026-07-05): fixed several client bugs; noted backend gaps (`/sample-queries` missing, no async introspect, no tenant catalogue, no source map) and unused backend surface (`/nl-explain`, `/nl-execute`, `/schema/properties`, `/schema/summary`, `/connections`).

---

## Topic: SPARQL→AQL porting recipe (skill)
- source: /Users/plosiewicz/dev/arango-sparql-py/.cursor/skills/sparql-to-aql/SKILL.md

Deterministic recipe for adding/fixing any SPARQL construct: translation
behaviour must match the legacy Foxx service's semantics, never be invented.
- Legacy source of truth: `references/arango-sparql/src/lib/` (`aql-translator.js`, `pgt-translator.js`, `rpt-translator.js`, `filter-translator.js`, `triple-constructor.js`, `aql-query-builder.js`, `uri-resolver.js`, `uri-hasher.js`).
- Python target: `arango_sparql/translate/` (`parser.py`, `visitor.py`, `builder.py`, `resolver.py`, `errors.py`).
- Workflow: identify Algebra node → read legacy JS → locate/create `visit_<Node>` → implement via the parameterized builder (never string-concat AQL, never inline literals) → add golden test → add pyoxigraph cross-validation → run ruff + pytest.
- Algebra node map incl. `OPTIONAL`→`LeftJoin`, `MINUS`→`Minus`, `GRAPH`→`Graph`, `SERVICE`→`Service` (raise `UnsupportedSparql` until implemented).
- Forbidden: ANTLR / custom parsers / hand-concatenated AQL / inlined literals; "fixing" a golden to match buggy output (the golden IS the spec); skipping the legacy JS read.

---

## Topic: Vision / inception narrative
- source: /Users/plosiewicz/dev/arango-sparql-py/docs/architecture/vision.md

Redirect stub only — the inception narrative was consolidated into
`PRD.md` Appendix C. No independent content; kept so existing links resolve.

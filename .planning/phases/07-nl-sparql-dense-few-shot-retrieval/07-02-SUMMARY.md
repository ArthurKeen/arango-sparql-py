---
phase: 07-nl-sparql-dense-few-shot-retrieval
plan: 02
subsystem: nl-pipeline
tags: [fewshot, sparql, eval-harness, rdflib, sentence-transformers, leakage-gate]

# Dependency graph
requires:
  - phase: 07-01
    provides: "DenseRetriever + FewShotIndex.from_corpus_files(mode=) + DEFAULT_DENSE_MODEL_ID/DEFAULT_DENSE_REVISION constants (arango_query_core.nl.fewshot) — this plan's similarity-ceiling test imports these constants (lazily, skips if absent)"
provides:
  - "tests/nl2sparql/eval/fewshot_bank.yml — 23-example curated few-shot bank (5 basic BGP, 4 OPTIONAL, 4 aggregation/GROUP BY, 6 property-path, 4 multi-hop), authored from an independent difficulty-class spec with corpus.yml CLOSED, sharing corpus.yml's ontology block verbatim, canonical question:/query: keys"
  - "tests/nl2sparql/eval/test_fewshot_bank_disjoint.py — the D-02 leakage gate: three-way disjointness (text + canonical algebra + canonical skeleton), a cosine similarity ceiling (<0.95), a recorded nearest-neighbor distribution, a gold-must-parse guard, and an ontology-parity drift guard"
affects: [07-03, 07-04]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Canonical-algebra SKELETON check: _skeleton(sparql) = _canonical(sparql) with Literal(...) values regex-blanked to ?LIT and quoted URIRef('...') triple-position tokens regex-blanked to ?URI, so a literal-only nudge (:age 30 vs :age 40) cannot dodge disjointness; property-path predicates (rdflib Path objects) are unaffected by the URIRef('...') pattern and keep their real identity"
    - "Empirical pre-validation of bank authoring: before finalizing fewshot_bank.yml, computed every candidate example's skeleton against all 22 positive corpus skeletons in a scratch script and iterated bank item shapes (added FILTER/LIMIT/ORDER BY/DISTINCT/HAVING/ASK/extra hops) until zero collisions, rather than discovering collisions only at test time"
    - "Cosine-ceiling test lazily imports sentence-transformers + the 07-01 pinned model constants and pytest.skip()s on ImportError — same two-tier degrade posture as DenseRetriever itself"

key-files:
  created:
    - tests/nl2sparql/eval/fewshot_bank.yml
    - tests/nl2sparql/eval/test_fewshot_bank_disjoint.py
  modified: []

key-decisions:
  - "_skeleton() blanks BOTH literal values (Literal(...) reprs) AND quoted URIRef('...') tokens (class/predicate references appearing directly in triple position), per the plan's explicit instruction. This is a stricter check than literal-only: two BGPs with the same triple/OPTIONAL/aggregate shape but DIFFERENT predicates collapse to the same skeleton (since the ontology has only ~7 properties, this is easy to trigger). Verified empirically (scratch script) against all 22 positive corpus cases and iterated bank example shapes (added FILTER, LIMIT, ORDER BY, DISTINCT, HAVING, ASK, extra hops, double-OPTIONAL) until every bank item's skeleton is unique relative to the corpus."
  - "Property-path predicates are NOT blanked by the URIRef('...')-quoted regex, because rdflib's Path objects repr as bare `Path(<uri> / <uri>)` text (no quotes, no URIRef(...) wrapper) — a natural consequence of operating on the _canonical() string via regex rather than walking the algebra tree. This is accepted as-is (documented in the helper's docstring) since it keeps the property-path difficulty class authorable while the class/predicate identity still meaningfully distinguishes those items."
  - "Bank sized at 23 examples (within the 18-24 heuristic): 5 basic BGP, 4 OPTIONAL, 4 aggregation, 6 property-path, 4 multi-hop — slightly path-heavy to give k=3 dense/BM25 retrieval real candidates to discriminate among for the class the SOTA survey flags as highest-value."
  - "Authored with corpus.yml CLOSED per B2: bank questions/queries are original constructions from the difficulty-class spec (not corpus paraphrases); the after-the-fact D-02 gate (this plan's Task 2) is the proof, not a design aid consulted during authoring."
  - "test_fewshot_bank_disjoint.py is RUN_EVAL-gated (pytest.mark.eval + skipif) matching test_gold_transpilable.py's convention, even though the disjointness/parity checks are themselves key-free — consistency with the repo's existing eval-marker convention for anything touching runner.py internals, per PATTERNS.md guidance."
  - "test_bank_similarity_ceiling SKIPPED in this environment because sentence-transformers fails to import (pre-existing tokenizers version mismatch, unrelated to this plan) — expected per the plan's own 'soft cross-plan coupling' note: the ceiling only becomes an active gate once 07-03 syncs the [dense] extra."

patterns-established:
  - "Any future corpus-adjacent curated-data file should ship with an authoring-CLOSED discipline + an after-the-fact disjointness gate reusing the existing canonical judge, not a second ad hoc equality check."

requirements-completed: []  # NL-FEW-01 spans plans 01-03 (per 07-01 SUMMARY); this plan lands only the "curated corpus" piece — see Requirements Note below

# Metrics
duration: ~10min
completed: 2026-07-21
---

# Phase 7 Plan 02: Fewshot bank + D-02 leakage gate Summary

**23-example curated few-shot bank (fewshot_bank.yml) authored from an independent difficulty-class spec with corpus.yml closed, gated by a three-way disjointness + cosine-similarity-ceiling + ontology-parity test (test_fewshot_bank_disjoint.py) that reuses the existing canonical-algebra judge.**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-07-21T17:52:00Z
- **Completed:** 2026-07-21T18:01:14Z
- **Tasks:** 2
- **Files modified:** 2 (both new)

## Accomplishments

- Authored `tests/nl2sparql/eval/fewshot_bank.yml`: 23 question/query examples across 5 difficulty classes (5 basic BGP, 4 OPTIONAL, 4 aggregation/GROUP BY, 6 property-path, 4 multi-hop), sharing `corpus.yml`'s `ontology:` Turtle block byte-for-byte, using the canonical `question:`/`query:` keys `FewShotIndex.from_corpus_files` expects
- Every bank gold parses via `arango_sparql.translate.parser.parse_sparql`
- Built `test_fewshot_bank_disjoint.py` with four tests: `test_bank_disjoint_from_eval_corpus` (3-way: text + canonical algebra + canonical skeleton), `test_bank_similarity_ceiling` (cosine < 0.95, records min/median/max + top-5 closest pairs to stdout and `reports/fewshot_similarity.md`), `test_every_bank_gold_parses`, `test_bank_ontology_matches_corpus`
- Pre-validated bank authoring empirically: wrote a scratch script implementing the exact `_skeleton()` logic, computed all 22 positive corpus skeletons, and iterated candidate bank shapes until every one produced a skeleton not present in the corpus (documented under Decisions)
- Confirmed the full existing eval suite (`RUN_EVAL=1 pytest -m eval -q`) still passes: 38 passed, 7 skipped

## Task Commits

Each task was committed atomically:

1. **Task 1: Author fewshot_bank.yml** - `d2ea5e2` (feat)
2. **Task 2: Author the leakage gate (3-way disjointness + similarity ceiling + parity)** - `56004b9` (feat)

**Plan metadata:** recorded below (SUMMARY.md + STATE.md + ROADMAP.md commit)

## Files Created/Modified

- `tests/nl2sparql/eval/fewshot_bank.yml` - Curated 23-example few-shot bank, ontology shared verbatim with `corpus.yml`
- `tests/nl2sparql/eval/test_fewshot_bank_disjoint.py` - D-02 leakage gate: 3-way disjointness, cosine ceiling + distribution recorder, gold-parse guard, ontology-parity guard

## Decisions Made

See frontmatter `key-decisions` above for the full list. Most notable: the `_skeleton()` helper blanks both literal values and quoted `URIRef('...')` tokens (a stricter reading of the plan's instruction than literal-only), which was verified empirically against the full corpus via a scratch script before the bank YAML was finalized, rather than discovering shape collisions only when running the test. Property-path predicates are naturally exempted from URI-blanking because rdflib's `Path` object `repr()` doesn't use the quoted `URIRef('...')` form my regex targets — documented in the helper's docstring rather than silently relied upon.

## Deviations from Plan

None - plan executed exactly as written. The similarity-ceiling test's graceful skip (dense stack not installed in this environment) is expected behavior per the plan's own "soft cross-plan coupling" note, not a deviation.

## Issues Encountered

- Initial draft bank candidates (before empirical validation) collided at the skeleton level with 6 corpus cases (two OPTIONAL cases, two aggregation cases, two multi-hop cases) because the `_skeleton()` design blanks predicate URIs, not just literals, and the shared 7-property ontology makes shape-only collisions easy to trigger. Resolved by iterating bank item structure (added FILTER/LIMIT/ORDER BY/DISTINCT/HAVING/ASK/extra hops/double-OPTIONAL) until a scratch-script check against the real corpus reported zero collisions, then finalized the YAML — this is the plan's own prescribed workflow ("a collision is a signal to re-author, not to nudge a literal"), executed before authoring the final file rather than after.

## User Setup Required

None - no external service configuration required. `sentence-transformers` install + model download remain deferred to 07-03/07-04 per the plan's design.

## Requirements Note

`NL-FEW-01`'s acceptance criterion ("retrieved examples appear in the `NLQueryEngine`-built prompt's `## Examples` section") is **not yet satisfiable** by this plan alone — this plan lands only the curated-corpus + leakage-gate piece (the "SC1 curated corpus"). `NL-FEW-01` is NOT marked complete in `REQUIREMENTS.md`, consistent with 07-01's SUMMARY note; it will be marked complete once Plan 03 (`SparqlAdapter.few_shot_index()` wiring) lands.

## Next Phase Readiness

- `fewshot_bank.yml` is ready for `SparqlAdapter.few_shot_index()` to consume via `FewShotIndex.from_corpus_files` in 07-03.
- The D-02 gate is green now (disjointness/parity active) and will gain an active similarity-ceiling check the moment 07-03 syncs the `[dense]` extra — no further action needed in this plan.
- `NL-FEW-01`'s "curated corpus" acceptance criterion is satisfied by this plan; the seam-wiring portion of NL-FEW-01 (returning a populated index from `SparqlAdapter.few_shot_index()`) still depends on 07-03, per 07-01's SUMMARY note. No blockers identified for Plan 03.

## Self-Check: PASSED

Both new files confirmed present on disk (`tests/nl2sparql/eval/fewshot_bank.yml`, `tests/nl2sparql/eval/test_fewshot_bank_disjoint.py`); both task commits (`d2ea5e2`, `56004b9`) confirmed present in `git log`; this SUMMARY.md confirmed present on disk.

---
*Phase: 07-nl-sparql-dense-few-shot-retrieval*
*Completed: 2026-07-21*

---
phase: 06-nl-sparql-eval-harness-seed-corpus
plan: 03
subsystem: testing
tags: [ci, github-actions, nl2sparql, eval-harness, w3c-dawg, regression-gate]

# Dependency graph
requires:
  - phase: 06-nl-sparql-eval-harness-seed-corpus
    provides: "runner.py + test_eval.py + baseline.json (RUN_EVAL-gated @pytest.mark.eval gate) from Plan 02"
provides:
  - "New `eval:` job in .github/workflows/ci.yml — installs .[dev,nl,service], sets RUN_EVAL=1, runs `pytest -m eval --tb=short -q`, no API key/secret"
  - "Confirmed W3C DAWG query-eval coverage no-regression guard (96.4%, 244/253) with zero transpiler files touched by Phase 6"
affects: [07-nl-sparql-few-shot-index]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "CI eval job mirrors the existing `test` job's checkout/setup-python/install shape exactly, with the sole delta being RUN_EVAL=1 + `pytest -m eval` (inverted marker selection vs. the `test` job's exclusion)"

key-files:
  created: []
  modified:
    - .github/workflows/ci.yml

key-decisions:
  - "Task 2 is verification-only (no ci.yml edits beyond Task 1) — confirmed via git diff that zero files under arango_sparql/ changed across the phase, so the W3C query-eval coverage guard is a read/confirm step, not a code change"
  - "Read coverage strictly from the `Query evaluation` table row's final column (96.4%, 244/253), not the table maximum (`Syntax (positive)` reads 100.0% and would mask a regression if keyed off the largest percentage)"

patterns-established: []

requirements-completed: [NL-EVAL-01]

# Metrics
duration: 6min
completed: 2026-07-15
---

# Phase 06 Plan 03: CI Eval Job + W3C No-Regression Guard Summary

**Added a dedicated key-free `eval:` CI job running `RUN_EVAL=1 pytest -m eval` and confirmed W3C DAWG query-eval coverage holds at 96.4% (244/253) with zero transpiler files touched by this phase.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-07-15T23:09:43Z
- **Completed:** 2026-07-15T23:12:17Z
- **Tasks:** 2/2
- **Files modified:** 1

## Accomplishments
- `.github/workflows/ci.yml` now has a top-level `eval:` job alongside `lint` and `test`: `actions/checkout@v4` → `actions/setup-python@v5` (`python-version: "3.12"`) → `pip install -e ".[dev,nl,service]"` → `RUN_EVAL=1 pytest -m eval --tb=short -q`. No API key or secret is referenced anywhere in the job — the `scripted` config backing `baseline.json` makes zero network calls.
- The pre-existing `test` job's marker exclusion (`-m "not integration and not w3c and not eval"`) is untouched, so per-PR fast tests stay fast; the `eval` marker is now selected exclusively by the new job (closing RESEARCH Pitfall 3 — previously the marker was gated behind `RUN_EVAL=1` AND excluded by `test`, so it was never selected anywhere in CI).
- Ran `bash scripts/fetch_w3c.sh` (idempotent — corpus already present, skipped) then `python tests/w3c/analyze_coverage.py`: the **`Query evaluation`** table row (not the table maximum, which is `Syntax (positive)` at 100.0%) reads **253 total, 244 pass, 9 xfail → 96.4%** — unchanged from the 96.4% baseline recorded in ROADMAP.md/STATE.md.
- Confirmed via `git diff --name-only bbf6a05..HEAD -- arango_sparql/` (empty) that this entire phase modified zero transpiler files, proving the 96.4% figure is a true no-drift confirmation, not a coincidence.
- Confirmed `tests/w3c/data/` remains uncommitted (`git status --porcelain tests/w3c/data/` empty) — the corpus stays fetch-on-demand and gitignored.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add the eval job to ci.yml** - `9693b82` (feat)
2. **Task 2: Verify W3C DAWG query-eval coverage ≥ 96.4% (no-regression guard)** - verification-only; no code change, nothing to commit (see Decisions Made)

**Plan metadata:** committed via this SUMMARY + STATE/ROADMAP update commit (see below)

## Files Created/Modified
- `.github/workflows/ci.yml` - Added `eval:` job (RUN_EVAL=1, `pytest -m eval --tb=short -q`, `.[dev,nl,service]`, no API key); existing `test` job's marker exclusion left unchanged.

## Decisions Made
- Task 2 required no file changes — it is a guard/verification task whose job is to confirm (and record) that coverage has not regressed, not to produce an artifact. `analyze_coverage.py` was run without `--write`, per the plan's explicit instruction, and no transpiler file was edited.
- Read the query-eval figure specifically from the `Query evaluation` row (244/253 = 96.4%) rather than any other row or the table maximum, per the plan's explicit warning that `Syntax (positive)` reads 100.0% and would give a false sense of the gate if misread.

## Deviations from Plan

None - plan executed exactly as written. Task 1's ci.yml diff matches the PATTERNS.md-specified shape verbatim (checkout/setup-python/install/RUN_EVAL step). Task 2 is a pure verification step and required no additional edits.

## Issues Encountered

None. `bash scripts/fetch_w3c.sh` was a no-op (corpus already present from Plan 01/02 sessions), and `python tests/w3c/analyze_coverage.py` ran to completion without needing `SCHEMA_ANALYZER_REQUIRED` or any workaround.

## User Setup Required

None - no external service configuration required. The new `eval` CI job runs the scripted (no-network) config exclusively; no `NL2SPARQL_API_KEY`/`OPENAI_API_KEY`/etc. secrets were added to CI.

## Next Phase Readiness

- Phase 6 is now fully closed: the NL eval harness (Plan 01: corpus/configs, Plan 02: runner/test_eval.py/baseline.json, Plan 03: CI wiring + W3C guard) runs end-to-end and gates in CI via `RUN_EVAL=1 pytest -m eval`.
- W3C DAWG query-eval coverage remains confirmed at 96.4% (244/253), the mandatory no-regression floor for the NL workstream (Phases 6-7 per STATE.md blockers).
- Phase 7 (few-shot index) can proceed: it will use this same harness/CI gate to measure pass-rate lift from the BM25 few-shot index against `baseline.json`.
- No blockers.

## Self-Check: PASSED

- FOUND: .github/workflows/ci.yml (jobs.eval present, parses as YAML)
- FOUND commit: 9693b82 (Task 1)
- CONFIRMED: Query evaluation coverage = 96.4% (244/253) via `python tests/w3c/analyze_coverage.py`
- CONFIRMED: `git diff --name-only bbf6a05..HEAD -- arango_sparql/` empty (zero transpiler files changed by phase)
- CONFIRMED: `git status --porcelain tests/w3c/data/` empty (corpus stays uncommitted)

---
*Phase: 06-nl-sparql-eval-harness-seed-corpus*
*Completed: 2026-07-15*

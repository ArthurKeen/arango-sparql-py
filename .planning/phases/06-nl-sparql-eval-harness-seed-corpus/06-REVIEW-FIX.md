---
phase: 06-nl-sparql-eval-harness-seed-corpus
fixed_at: 2026-07-15T23:36:52Z
review_path: .planning/phases/06-nl-sparql-eval-harness-seed-corpus/06-REVIEW.md
iteration: 1
findings_in_scope: 4
fixed: 4
skipped: 0
status: all_fixed
---

# Phase 06: Code Review Fix Report

**Fixed at:** 2026-07-15T23:36:52Z
**Source review:** .planning/phases/06-nl-sparql-eval-harness-seed-corpus/06-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 4 (CR-01, WR-01, WR-02, WR-03 — fix_scope=critical_warning; IN-01/IN-02 out of scope, not attempted)
- Fixed: 4
- Skipped: 0

## Fixed Issues

### CR-01: `_judge_execution` compares stringified dicts, so key-order differences make a semantically-correct query fail

**Files modified:** `tests/nl2sparql/eval/runner.py`
**Commit:** 8ae9003
**Applied fix:** Added a `_canon_row()` helper that converts each binding
row (`dict[str, str]`) into a sorted tuple of `(key, value)` pairs, and
changed `_judge_execution` to compare `sorted(map(_canon_row, ...))` on
both sides instead of `sorted(map(str, ...))`. This makes the comparison
insensitive to solution-mapping key/column order, matching SPARQL
solution-mapping semantics (as the review's reproduction demonstrated:
`{'s': ..., 'n': ...} == {'n': ..., 's': ...}` is `True` but their
`str()` forms previously differed). Verified with the exact
before/after reproduction from the review, and via the harness's
existing `RUN_EVAL=1` pass (this tier isn't exercised by any checked-in
corpus case, so no case-level behavior changed).

### WR-01: `RUN_EVAL=0` does not skip the eval gate — only an unset/empty value does

**Files modified:** `tests/nl2sparql/eval/test_eval.py`
**Commit:** 13096b7
**Applied fix:** Replaced `not os.getenv("RUN_EVAL")` with a
`_RUN_EVAL` module-level flag computed as
`os.getenv("RUN_EVAL", "").strip().lower() not in ("", "0", "false", "no")`,
matching the review's exact suggested pattern, and updated the
`skipif` marker to use it. Verified directly: `RUN_EVAL=0 pytest
tests/nl2sparql/eval -m eval -q` now reports `1 skipped` (previously it
ran and passed the test instead of skipping), and `RUN_EVAL=1 pytest
tests/nl2sparql/eval -m eval -q` still reports `1 passed`.

### WR-02: Per-case regression gate only covers cases known at baseline-authoring time; new failing cases can hide behind aggregate dilution

**Files modified:** `tests/nl2sparql/eval/test_eval.py`
**Commit:** 6151747
**Applied fix:** Added the review's suggested `new_names` gate:
computes `corpus_names - baseline_names` and asserts every case name in
that difference set passed live. Since every case in the current
`corpus.yml` is already tracked in `baseline.json`, `new_names` is
currently empty and this addition is a no-op today — it only activates
the moment a new corpus case is added without a matching
`baseline.json` entry, forcing the author to consciously add it once
green. Verified `RUN_EVAL=1 pytest tests/nl2sparql/eval -m eval -q`
still passes with the addition in place (no behavior change to the
existing 6-case corpus).

### WR-03: Canonical judge relies on raw `Project.PV`, which the rest of the codebase documents as PYTHONHASHSEED-non-deterministic for `SELECT *`

**Files modified:** `tests/nl2sparql/eval/runner.py`
**Commit:** ea4f801
**Applied fix:** Added a `_stable_repr()` helper that recursively walks
the rdflib algebra tree (`CompValue` / `dict` / `list` / `tuple` /
`set`/`frozenset`) and canonicalizes every set-derived structure — both
raw `set`/`frozenset` fields (e.g. every `BGP`/`Project` node's
`_vars`) and the `PV` key specifically (list-shaped but built from
`list(a_set)` inside rdflib for `SELECT *`) — to a `sorted(..., key=str)`
form before falling back to plain `repr()` for leaves. Explicitly
ordered structures (e.g. `BGP.triples`) are left untouched. `_canonical()`
now calls `_stable_repr(parse_sparql(sparql).algebra)` instead of
`repr(...)` directly.

Verification performed beyond the standard 3-tier check, per the
environment note:
- Confirmed the bug was real before the fix: `repr()` of
  `parse_sparql('SELECT * WHERE { ?s ?p ?o }').algebra` differed across
  `PYTHONHASHSEED=0/42/1234/7` (element order of the `_vars`/`PV`
  fields varied).
- Confirmed `_stable_repr()` produces byte-identical output across
  `PYTHONHASHSEED=0/42/1234` for the same `SELECT *` query.
- Ran `RUN_EVAL=1 pytest tests/nl2sparql/eval -m eval -q` under both the
  default hash seed and `PYTHONHASHSEED=42` — both pass, confirming the
  scripted pass-rate (0.833) is unchanged (no current corpus case uses
  `SELECT *`, so behavior on the existing corpus is identical, as
  required).
- Ran the full suite (`pytest tests/ -q --deselect tests/nl2sparql/eval`):
  1167 passed, 13 skipped, 0 failures — no regressions from the change
  or from the `rdflib.plugins.sparql.parserutils.CompValue` import.

## Skipped Issues

None — all 4 in-scope findings (CR-01, WR-01, WR-02, WR-03) were fixed
and verified. IN-01 and IN-02 are Info-severity and out of scope for
`fix_scope=critical_warning`; they were not attempted.

---

_Fixed: 2026-07-15T23:36:52Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_

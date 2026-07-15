---
phase: 06-nl-sparql-eval-harness-seed-corpus
reviewed: 2026-07-15T23:22:06Z
depth: deep
files_reviewed: 5
files_reviewed_list:
  - tests/nl2sparql/eval/runner.py
  - tests/nl2sparql/eval/test_eval.py
  - tests/nl2sparql/eval/corpus.yml
  - tests/nl2sparql/eval/configs.yml
  - tests/nl2sparql/eval/baseline.json
  - .github/workflows/ci.yml
findings:
  critical: 1
  warning: 3
  info: 2
  total: 6
status: issues_found
---

# Phase 06: Code Review Report

**Reviewed:** 2026-07-15T23:22:06Z
**Depth:** deep
**Files Reviewed:** 6 (runner.py, test_eval.py, corpus.yml, configs.yml, baseline.json, ci.yml)
**Status:** issues_found

## Summary

The harness's primary path (`scripted` config → `_judge_canonical`) is sound: it correctly
uses `rdflib`'s canonical algebra (never string matching, per rule 200), correctly treats
`outcome.aql == ""` as the authoritative reject signal, correctly catches `SparqlParseError`
(the only exception `parse_sparql` can raise), and a fresh `ScriptedLLMClient` is built per
corpus case inside `run()`'s loop (`runner.py:172`), so there is no cross-case response
leakage despite the client's documented "replay last response forever" behavior. I verified
all of this by executing `RUN_EVAL=1 pytest tests/nl2sparql/eval/test_eval.py`, which passes,
and by directly reproducing the `deliberate-near-miss` case's rdflib-algebra divergence.

However, the optional `_judge_execution` tier (the pyoxigraph execution-equivalence path)
contains a genuine correctness bug that will falsely fail semantically-correct queries the
moment a `data:` fixture case is added to the corpus — it is not exercised by any case today,
so it hasn't caused a visible CI failure, but it ships broken. I also found a footgun in the
`RUN_EVAL` gate check, a coverage gap in the per-case regression gate for newly-added corpus
cases, and a latent (currently dormant) non-determinism risk in the canonical judge should a
future case use `SELECT *`.

No hardcoded secrets, no `eval()`/`exec()`/shell injection, no `yaml.load` usage (both loaders
correctly use `yaml.safe_load`), and no reference to a nonexistent `providers.py` module or to
an unreachable `references/...` path at runtime were found.

## Critical Issues

### CR-01: `_judge_execution` compares stringified dicts, so key-order differences make a semantically-correct query fail

**File:** `tests/nl2sparql/eval/runner.py:135-146`
**Issue:** The execution-equivalence judge computes bindings via `oxi_bindings()` (which
returns `list[dict[str, str]]`, one dict per solution row, keyed by projected variable name in
column order) and then compares them with:

```python
return sorted(map(str, expected_bindings)) == sorted(map(str, actual_bindings))
```

`str(dict)` is sensitive to key **insertion order**, even though `dict.__eq__` (and SPARQL
solution-mapping semantics) is not. If the model's generated query projects the same variables
in a different order than the gold `expected` query (e.g. gold is `SELECT ?s ?n`, the model
emits `SELECT ?n ?s` over an identical WHERE clause — a semantically-identical query), the two
dicts are equal (`{'s': ..., 'n': ...} == {'n': ..., 's': ...}` → `True`) but their `str()`
forms differ, so `_judge_execution` returns `False` — a false rejection of a correct answer.
Reproduced directly:

```python
>>> eb = [{"s": "http://ex.org/a1", "n": "Alice"}]
>>> ab = [{"n": "Alice", "s": "http://ex.org/a1"}]
>>> eb == ab
True
>>> sorted(map(str, eb)) == sorted(map(str, ab))
False
```

This tier is not exercised by any case in the checked-in `corpus.yml` (none carry a `data:`
Turtle fixture), so it currently causes no visible test failure — but it ships broken, and the
corpus.yml docstring / configs.yml comments explicitly anticipate this tier being adopted
later. The first case that uses it with a differently-ordered projection will silently and
non-obviously fail.

**Fix:** Compare canonicalized rows, not their `repr`, e.g.:

```python
def _canon_row(row: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(row.items()))

return sorted(map(_canon_row, expected_bindings)) == sorted(map(_canon_row, actual_bindings))
```

## Warnings

### WR-01: `RUN_EVAL=0` does not skip the eval gate — only an unset/empty value does

**File:** `tests/nl2sparql/eval/test_eval.py:24`
**Issue:**

```python
@pytest.mark.skipif(not os.getenv("RUN_EVAL"), reason="set RUN_EVAL=1 to run the NL eval gate")
```

`os.getenv("RUN_EVAL")` returns the literal string `"0"` when a caller runs
`RUN_EVAL=0 pytest -m eval`, and `not "0"` is `False` in Python (non-empty strings are
truthy), so the test is **not** skipped — it runs anyway. Verified locally:
`RUN_EVAL=0 pytest tests/nl2sparql/eval/test_eval.py` executes and passes the test rather than
skipping it. A developer or CI script that sets `RUN_EVAL=0` intending "eval off" will
silently get "eval on" instead. This doesn't break CI today (the job only ever sets
`RUN_EVAL=1`), but it's a footgun for local/manual use and for any future workflow that toggles
the var to `"0"`/`"false"` to disable it.
**Fix:**

```python
_RUN_EVAL = os.getenv("RUN_EVAL", "").strip().lower() not in ("", "0", "false", "no")

@pytest.mark.skipif(not _RUN_EVAL, reason="set RUN_EVAL=1 to run the NL eval gate")
```

### WR-02: Per-case regression gate only covers cases known at baseline-authoring time; new failing cases can hide behind aggregate dilution

**File:** `tests/nl2sparql/eval/test_eval.py:38-44`
**Issue:** The per-case loop only iterates `baseline["cases"].items()` — i.e. cases that
existed when `baseline.json` was last authored:

```python
live_by_name = {c.name: c.passed for c in report.cases}
for name, was_passing in baseline["cases"].items():
    if was_passing:
        assert live_by_name.get(name) is True, ...
```

A new case added to `corpus.yml` in the same PR that also introduces a real regression (e.g. a
newly-broken prompt/repair interaction) is never checked per-case — it's silently absorbed into
the aggregate `pass_rate` average. If enough other new passing cases are added alongside it (or
the corpus is large enough that one failure barely moves the ratio), the aggregate assertion
(`report.pass_rate >= baseline["pass_rate"] - 1e-9`) can still pass even though a brand-new case
is broken from day one. This is a real "gate passes despite a genuine regression" gap, distinct
from (but related to) the vacuous-pass concern called out in the review brief. It relies
entirely on process discipline (remembering to update `baseline.json` in the same PR) rather
than being enforced by the test itself.
**Fix:** Either (a) assert every corpus case name appears in `baseline["cases"]` (failing loudly
when a case is new/untracked, forcing the author to consciously add it to the baseline), or (b)
add an explicit assertion requiring 100% of *new* (baseline-absent) cases to pass:

```python
corpus_names = {c.name for c in report.cases}
baseline_names = set(baseline["cases"])
new_names = corpus_names - baseline_names
for name in new_names:
    assert live_by_name.get(name) is True, f"new case {name!r} must pass before it's added to baseline.json"
```

### WR-03: Canonical judge relies on raw `Project.PV`, which the rest of the codebase documents as PYTHONHASHSEED-non-deterministic for `SELECT *`

**File:** `tests/nl2sparql/eval/runner.py:118-122`
**Issue:** `_canonical()` computes `repr(parse_sparql(sparql).algebra)` directly from the raw
rdflib algebra. `arango_sparql/translate/parser.py` (lines 9-14, 40-56) and
`arango_sparql/translate/visitor.py` (lines 161-173, 574-580) explicitly document — and work
around — the fact that `rdflib.plugins.sparql.algebra.translateQuery`'s `Project.PV` field is
built from a Python `set` (`PV = list(VS)` at `rdflib/plugins/sparql/algebra.py:707-709`) for
`SELECT *` queries, whose iteration order depends on `PYTHONHASHSEED` and is therefore
unstable across interpreter runs. `parse_sparql()` exposes a separate `explicit_projection`
field specifically so callers don't have to trust the raw `PV`. Traced through rdflib source:
for explicit `SELECT ?a ?b` projections this is *not* an issue today (PV preserves textual
order in that branch), and no case in the current `corpus.yml` uses `SELECT *`, so this is
currently dormant. But the moment a natural NL corpus case is added whose gold/generated query
uses `SELECT *` (a very plausible NL→SPARQL target, e.g. "show me everything about X"), the
eval judge's `repr()`-based comparison will intermittently diverge across CI runs (different
worker processes get different hash seeds) purely due to `Project.PV` ordering, not due to any
actual semantic difference — causing baseline.json flakiness that would be very hard to
diagnose given the codebase already went to the trouble of solving this exact problem
elsewhere.
**Fix:** Route the judge's canonicalization through the same `explicit_projection`-aware
representation the transpiler uses (or normalize `Project.PV`/similar set-derived fields to a
sorted tuple before computing `repr()`), rather than trusting the raw algebra's `repr()`
wholesale.

## Info

### IN-01: `write_report()` is entirely dead/unused and untested

**File:** `tests/nl2sparql/eval/runner.py:203-231`
**Issue:** No call site exists anywhere in the repo (`test_eval.py` calls only `run()`; the CI
`eval` job invokes `pytest -m eval`, which never reaches `write_report`). It's therefore
untested code shipping in a reviewed module — any latent bug in it (e.g. the unvalidated
`report.config` interpolated directly into a filename at `runner.py:205-206`, which would be a
path-traversal concern if `config_name` ever became untrusted input) would go unnoticed.
Currently harmless since `run()` is only ever invoked with a literal, trusted config name
(`"scripted"`), but the function's very presence with zero coverage is worth flagging for a
quality pass.
**Fix:** Either wire a smoke test (`write_report(run("scripted"), out_dir=tmp_path)` +
assertions on file existence/shape) or, if this is intentionally deferred plumbing for a future
CLI/report script, note that explicitly in the module docstring so reviewers don't mistake it
for dead code.

### IN-02: `_judge()` silently downgrades an `execution` judge to `canonical` when a case lacks a `data:` fixture

**File:** `tests/nl2sparql/eval/runner.py:149-152`
**Issue:**

```python
def _judge(judge_name: str, case: dict[str, Any], outcome: Any) -> bool:
    if judge_name == "execution" and case.get("data"):
        return _judge_execution(case["expected"], outcome, case["data"])
    return _judge_canonical(case["expected"], outcome)
```

If a future `configs.yml` entry sets `judge: execution` intending every case to be checked at
the execution-equivalence tier, but some corpus cases lack a `data:` fixture, those cases are
silently judged at the weaker `canonical` tier instead — with no warning or report annotation
that a downgrade occurred. Not exercised today (only `judge: canonical` appears in
`configs.yml`), but worth a log/warning line so a future config author notices the mismatch
rather than assuming full execution-tier coverage.
**Fix:** Emit a `logger.warning(...)` (or record a per-case note in `CaseResult`) when a case is
downgraded from its config's declared judge to `canonical` for lack of fixture data.

---

_Reviewed: 2026-07-15T23:22:06Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_

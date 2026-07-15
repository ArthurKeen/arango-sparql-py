---
phase: 06-nl-sparql-eval-harness-seed-corpus
verified: 2026-07-15T23:30:00Z
status: passed
score: 5/5 must-haves verified
overrides_applied: 0
---

# Phase 06: NL→SPARQL Eval Harness + Seed Corpus Verification Report

**Phase Goal:** Make NL→SPARQL translation quality measurable — implement the stubbed eval harness, author a seed corpus, and check in a baseline as the regression gate. This is the first active phase.
**Verified:** 2026-07-15T23:30:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `runner.py::run()`/`write_report()` implemented (no `NotImplementedError`), runs each corpus entry against configured provider | ✓ VERIFIED | `grep -c 'raise NotImplementedError' tests/nl2sparql/eval/runner.py` = 0. Live execution: `RUN_EVAL=1 python -c "from tests.nl2sparql.eval.runner import run, write_report; r=run('scripted')"` → `pass_rate=0.8333333333333334`, per-case verdicts for all 6 corpus cases (5 True, `deliberate-near-miss` False), `write_report(r)` returned two `Path`s that both `.exists()` |
| 2 | `corpus.yml` + `configs.yml` authored; eval marker runs green with `ScriptedLLMClient` | ✓ VERIFIED | `corpus.yml`: 6 cases incl. `deliberate-near-miss` (scripted response drops `?a` binding vs gold); `configs.yml`: `scripted` (provider.type=scripted, no network) + `openai-gpt4o-mini` real-provider config. `RUN_EVAL=1 pytest -m eval -q` → `1 passed, 6 skipped, 1723 deselected` (the 6 "skipped" are unrelated pre-existing eval-marked tests gated on other envs, not this phase's test) |
| 3 | Harness reports numeric NL→SPARQL pass-rate (JSON + Markdown) | ✓ VERIFIED | `write_report()` produced `reports/scripted.json` (`{"config":"scripted","pass_rate":0.8333...,"cases":[...]}`) and `reports/scripted.md` (Markdown table + pass-rate line); both files confirmed to exist on disk after live run |
| 4 | `baseline.json` checked in and enforced as regression gate | ✓ VERIFIED | `git ls-files tests/nl2sparql/eval/` includes `baseline.json`; `git check-ignore tests/nl2sparql/eval/baseline.json` → rc=1 (not ignored, i.e. tracked). `test_eval.py::test_scripted_pass_rate_meets_baseline` asserts both aggregate (`report.pass_rate >= baseline["pass_rate"] - 1e-9`) and per-case (no case that was `true` at baseline regresses) against the committed file. Test passes live. |
| 5 | W3C DAWG query-eval coverage remains ≥ 96.4% (no transpiler regression) | ✓ VERIFIED | `bash scripts/fetch_w3c.sh && python tests/w3c/analyze_coverage.py` → `Query evaluation \| 253 \| 244 \| 0 \| 9 \| 0 \| 96.4%` (read from the correct row, not the 100.0% `Syntax (positive)` maximum). `git diff --name-only bbf6a05..HEAD -- arango_sparql/` is empty — zero transpiler files touched by this phase, confirming the figure is a true no-drift result. |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `tests/nl2sparql/eval/corpus.yml` | ≥5 cases, ≥1 deliberate near-miss, ontology resolves via `SchemaResolver.from_turtle` | ✓ VERIFIED | 6 cases; `deliberate-near-miss` scripted response omits `?a`/`:age`; ontology header reuses `bgp_select.yml`'s `phys:collectionName` idiom; live `run()` call successfully built resolvers from it |
| `tests/nl2sparql/eval/configs.yml` | `scripted` config (no network) + ≥1 real-provider config | ✓ VERIFIED | `scripted` (type=scripted, judge=canonical, max_repairs=2) + `openai-gpt4o-mini` (type=openai) |
| `tests/nl2sparql/eval/runner.py` | Implemented `run()`+`write_report()`, provider factory, canonical judge, YAML safe_load loaders | ✓ VERIFIED | All present; `grep -E "yaml.load\b"` returns nothing (only `yaml.safe_load` used); `grep -c 'references/'` = 0 |
| `tests/nl2sparql/eval/test_eval.py` | `@pytest.mark.eval` gate asserting scripted pass_rate ≥ baseline | ✓ VERIFIED | `pytestmark = pytest.mark.eval`; `RUN_EVAL`-gated skipif; imports only `run, EVAL_DIR` from `tests.nl2sparql.eval.runner` (no `providers` module) |
| `tests/nl2sparql/eval/baseline.json` | Checked-in regression gate: aggregate pass_rate + per-case verdicts | ✓ VERIFIED | Committed, git-tracked; `pass_rate: 0.8333333333333334` (5/6, < 1.0 as required), per-case map for all 6 names |
| `.github/workflows/ci.yml` | New `eval` job: installs `.[dev,nl,service]`, sets `RUN_EVAL=1`, runs `pytest -m eval` | ✓ VERIFIED | `jobs.eval` present, mirrors `test` job's checkout/setup-python/install steps; final step `RUN_EVAL=1 pytest -m eval --tb=short -q`; no API key/secret referenced; `test` job's `-m "not integration and not w3c and not eval"` exclusion left unchanged |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `corpus.yml` cases | `arango_sparql.nl2sparql.NlPipeline.run` | `case['nl']` fed to pipeline in `run()`'s loop | ✓ WIRED | `pipeline.run(case["nl"], params=case.get("params"))` at `runner.py:181`; confirmed live (6/6 cases produced outcomes) |
| `configs.yml` `provider.type` | `runner._client_for` factory | branches on `type` → `ScriptedLLMClient`/`OpenAICompatibleClient`/`AnthropicClient` | ✓ WIRED | `_client_for()` at `runner.py:97-110`; live run used the `scripted` branch, constructing a fresh `ScriptedLLMClient` per case |
| `runner.py` judge | `arango_sparql.translate.parser.parse_sparql` | `repr(parse_sparql(sparql).algebra)` canonical comparison | ✓ WIRED | `_canonical()` at `runner.py:118-122`; `_judge_canonical` at `runner.py:125-132` — no raw SPARQL string equality anywhere in the file |
| `test_eval.py` | `baseline.json` | `json.loads(...)["configs"]["scripted"]` then aggregate + per-case assert | ✓ WIRED | `test_eval.py:27-44`; live `RUN_EVAL=1 pytest tests/nl2sparql/eval/test_eval.py -q` → 1 passed |
| `.github/workflows/ci.yml` `eval` job | `tests/nl2sparql/eval/test_eval.py` | `RUN_EVAL=1 pytest -m eval` | ✓ WIRED | Job step present verbatim; test carries `pytest.mark.eval` so the marker selects it |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| `run('scripted')` produces a numeric pass_rate < 1.0 with near-miss failing | `RUN_EVAL=1 python -c "from tests.nl2sparql.eval.runner import run; r=run('scripted'); ..."` | `pass_rate=0.8333333333333334`; `deliberate-near-miss` → `False`; all others → `True` | ✓ PASS |
| `write_report()` produces JSON + Markdown files | same session, `write_report(r)` | Both `reports/scripted.json` and `reports/scripted.md` exist | ✓ PASS |
| `RUN_EVAL=1 pytest -m eval -q` is green | `RUN_EVAL=1 pytest -m eval -q --tb=short` | `1 passed, 6 skipped, 1723 deselected` (the 1 passed is this phase's gate test) | ✓ PASS |
| `pytest -m eval -q` (no RUN_EVAL) skips the gate (local fast path preserved) | `pytest -m eval -q` | `7 skipped, 1723 deselected` | ✓ PASS |
| W3C query-eval coverage ≥ 96.4% | `bash scripts/fetch_w3c.sh; python tests/w3c/analyze_coverage.py` | `Query evaluation \| 253 \| 244 \| ... \| 96.4%` | ✓ PASS |
| No transpiler files touched by phase | `git diff --name-only bbf6a05..HEAD -- arango_sparql/` | (empty) | ✓ PASS |
| No secrets in checked-in fixtures | `grep -riE "api[_-]?key\|secret\|sk-" corpus.yml configs.yml ci.yml` | (only a documentation comment mentioning "API key or secret", no literal credential) | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|--------------|------------|-------------|--------|----------|
| NL-EVAL-01 | 06-02-PLAN, 06-03-PLAN | Eval harness implemented (`run()`/`write_report()`), eval marker wired into CI, green with scripted provider | ✓ SATISFIED | `runner.py` fully implemented + empirically run; `ci.yml` `eval` job runs `RUN_EVAL=1 pytest -m eval` |
| NL-EVAL-02 | 06-01-PLAN, 06-02-PLAN | Seed corpus authored (`corpus.yml`+`configs.yml`), `baseline.json` checked in, pass-rate becomes a tracked numeric metric | ✓ SATISFIED | `corpus.yml`/`configs.yml` authored and consumed; `baseline.json` committed with `pass_rate: 0.8333...` |

No orphaned requirements: `.planning/REQUIREMENTS.md` maps only NL-EVAL-01 and NL-EVAL-02 to Phase 6, and both appear in the `requirements:` frontmatter across the three plans (06-01: NL-EVAL-02; 06-02: NL-EVAL-01, NL-EVAL-02; 06-03: NL-EVAL-01).

Note: `.planning/REQUIREMENTS.md`'s traceability table (lines 93-94) still reads "Pending (ACTIVE)" for both IDs even though the checklist items above (lines 46-47) are marked `[x]`. This is a documentation-sync inconsistency in the requirements tracking doc itself, not a code/goal gap — flagged as advisory, not a blocker.

### Anti-Patterns Found

None. `grep -n -E "TODO|FIXME|HACK|PLACEHOLDER|TBD|XXX"` across `runner.py`, `test_eval.py`, `corpus.yml`, `configs.yml`, `ci.yml` returns nothing. No debt markers, no stub returns, no hardcoded-empty judge logic.

### Code Review Findings (06-REVIEW.md) — Impact Assessment

The independent code review (`06-REVIEW.md`, depth: deep) found 1 critical + 3 warning + 2 info issues. Assessed against the 5 success criteria above:

- **CR-01** (`_judge_execution` stringified-dict comparison is key-order-sensitive) — lives in the **optional execution-tier judge**, which is never invoked by the current corpus: no case in `corpus.yml` carries a `data:` Turtle fixture, and no `configs.yml` entry sets `judge: execution`. Confirmed via `grep -n "judge: execution\|data:" corpus.yml configs.yml` → no matches. **Does not affect any of the 5 success criteria** (all of which run through the `canonical` judge only). Latent defect, not a phase-goal blocker.
- **WR-03** (canonical judge's `repr(algebra)` is PYTHONHASHSEED-sensitive for `SELECT *`) — confirmed no case in `corpus.yml` uses `SELECT *` (`grep -n "SELECT \*" corpus.yml` → no matches). **Dormant, does not affect success criteria today.**
- **WR-01** (`RUN_EVAL=0` doesn't skip the gate) — a footgun for future manual toggling; CI only ever sets `RUN_EVAL=1`, never `=0`, so it does not affect CI green/red status or any of the 5 criteria today.
- **WR-02** (per-case regression gate only covers baseline-known case names, so a new case + regression in the same PR could hide behind aggregate dilution) — a process-discipline gap for *future* corpus growth; does not affect the current baseline/gate's correctness for the cases that exist today.
- **IN-01/IN-02** — informational, no impact on success criteria.

**Conclusion:** All review findings are advisory follow-ups on latent/dormant paths or footguns for future corpus growth. None of them cause any of the 5 ROADMAP success criteria to be false today. They are recorded here for a future hardening pass, not as phase gaps.

### Human Verification Required

None. All 5 success criteria and both requirement IDs were verified via direct code execution and file inspection — no visual, real-time, or external-service behavior is involved in this phase's scope.

### Gaps Summary

No gaps. All 5 ROADMAP success criteria are empirically verified against the live codebase (not just SUMMARY.md claims):

1. `run()`/`write_report()` implemented and exercised — confirmed by direct execution.
2. `corpus.yml`+`configs.yml` authored, eval marker green — confirmed by `RUN_EVAL=1 pytest -m eval -q`.
3. Numeric JSON+Markdown reports — confirmed both files exist with correct shape after a live run.
4. `baseline.json` checked in and enforced — confirmed git-tracked and asserted against in `test_eval.py`.
5. W3C query-eval coverage ≥ 96.4% — confirmed 96.4% (244/253) with zero transpiler files touched.

The code review's one critical and three warning findings are all in dormant/unexercised code paths (the optional execution-judge tier and the `SELECT *`/`RUN_EVAL=0`/per-case-dilution footguns) that do not affect any success criterion today. They are advisory follow-up items, not blockers to this phase's goal.

---

_Verified: 2026-07-15T23:30:00Z_
_Verifier: Claude (gsd-verifier)_

---
phase: 06-nl-sparql-eval-harness-seed-corpus
plan: 02
subsystem: testing
tags: [nl2sparql, eval-harness, rdflib, pytest, regression-gate]

# Dependency graph
requires:
  - phase: 06-nl-sparql-eval-harness-seed-corpus
    provides: "corpus.yml (6 seed cases incl. deliberate-near-miss) + configs.yml (scripted + openai-gpt4o-mini) from Plan 01"
provides:
  - "tests/nl2sparql/eval/runner.py — implemented run()/write_report(), rdflib-canonical judge, per-case ScriptedLLMClient factory, real-provider (OpenAI/Anthropic) factory branch, optional lazy pyoxigraph execution-judge tier"
  - "tests/nl2sparql/eval/test_eval.py — RUN_EVAL-gated @pytest.mark.eval test enforcing baseline.json (aggregate + per-case regression)"
  - "tests/nl2sparql/eval/baseline.json — committed regression gate, scripted pass_rate=0.8333 (5/6, deliberate-near-miss fails)"
affects: [07-nl-sparql-few-shot-index]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Fresh ScriptedLLMClient constructed per corpus case (never shared across cases) to avoid the queue-replay leak where a drained client replays its last response forever"
    - "Judge is rdflib canonical-algebra comparison via arango_sparql.translate.parser.parse_sparql (repr(algebra)), never raw SPARQL string equality (rule 200); outcome.aql == '' is the authoritative transpiler-reject signal checked before the algebra comparison"
    - "Optional execution-equivalence judge tier lazy-imports tests.helpers.oxi only when a case carries a data: Turtle fixture and judge: execution, so pyoxigraph absence never breaks the default canonical path"
    - "baseline.json regression gate checks both aggregate pass_rate (>=, epsilon-tolerant) and per-case (any case true in baseline must still be true live) so a swap that preserves the aggregate rate but breaks a different case is caught"

key-files:
  created:
    - tests/nl2sparql/eval/test_eval.py
    - tests/nl2sparql/eval/baseline.json
  modified:
    - tests/nl2sparql/eval/runner.py

key-decisions:
  - "baseline.json records the literal numbers produced by running run('scripted') against Plan 01's corpus (5/6 pass, pass_rate=0.8333...) rather than an aspirational number, per the plan's explicit instruction to author it by running the harness"
  - "Per-case regression is enforced as a hard gate in test_eval.py (not just informational), consistent with RESEARCH.md's Open Question 2 recommendation"
  - "CI wiring (.github/workflows/ci.yml new eval job) is out of scope for this plan — 06-02-PLAN.md's files_modified list is limited to runner.py/test_eval.py/baseline.json; RESEARCH/PATTERNS flag the CI job as a separate concern not enumerated in this plan's tasks"

patterns-established:
  - "Eval harness judge pattern: canonical (default, rdflib algebra) vs execution (optional, per-case data: + pyoxigraph) selected via configs.yml's judge field — future configs/cases can opt into either without runner.py changes"

requirements-completed: [NL-EVAL-01, NL-EVAL-02]

# Metrics
duration: 8min
completed: 2026-07-15
---

# Phase 06 Plan 02: NL Eval Harness Runner + Baseline Gate Summary

**Implemented `runner.py::run()`/`write_report()` as integration glue over the already-shipped `NlPipeline`/`ScriptedLLMClient`/`parse_sparql`, wired the `RUN_EVAL`-gated `@pytest.mark.eval` test, and authored `baseline.json` from the scripted config's actual 5/6 (0.8333) pass-rate.**

## Performance

- **Duration:** 8 min
- **Started:** 2026-07-15T22:58:49Z
- **Completed:** 2026-07-15T23:06:43Z
- **Tasks:** 2/2
- **Files modified:** 3 (1 modified, 2 created)

## Accomplishments
- `runner.py` no longer raises `NotImplementedError`: `run("scripted")` drives all 6 corpus cases from Plan 01 through `SchemaResolver.from_turtle` + `NlPipeline`, judges each with a canonical rdflib-algebra comparison (`parse_sparql(...).algebra` repr, catching `SparqlParseError`), and returns a `Report` with `pass_rate=0.8333...` — the `deliberate-near-miss` case fails exactly as designed (dropped `?a`/`:age` binding), proving the judge discriminates.
- `write_report(report)` writes `{config}.json` + `{config}.md` under `REPORTS_DIR` (gitignored) and returns both paths; verified both files exist after a live run.
- Provider factory (`_client_for`) constructs a **fresh** `ScriptedLLMClient` per case (avoiding the documented queue-replay leak) for `provider.type: scripted`, and binds `openai`/`openrouter` → `OpenAICompatibleClient`, `anthropic` → `AnthropicClient` for real-provider sweeps (not exercised in this plan — no network calls made).
- `test_eval.py` adds `test_scripted_pass_rate_meets_baseline`, marked `pytest.mark.eval` and skipped unless `RUN_EVAL=1`; asserts the live pass_rate meets `baseline.json`'s aggregate rate and that no case marked `true` in the baseline regresses to `false`.
- `baseline.json` is authored from the actual `run('scripted')` output (not a guessed number): `pass_rate=0.8333333333333334`, `passed=5`, `total=6`, per-case verdicts recording `deliberate-near-miss: false`.
- Verified: `RUN_EVAL=1 pytest -m eval -q` → 1 passed (the new gate test; other pre-existing `eval`-marked tests unrelated to this plan remain individually skipped for their own reasons, unaffected here). `pytest -m eval -q` without `RUN_EVAL` → the new test skips. Existing suite (`pytest -m "not integration and not w3c and not eval" -q`) → 1163 passed, unaffected. W3C DAWG query-eval coverage unchanged at 96.4% (`python tests/w3c/analyze_coverage.py`).

## Task Commits

Each task was committed atomically:

1. **Task 1: Implement runner.py — loaders, provider factory, canonical judge, run(), write_report()** - `155a503` (feat)
2. **Task 2: Wire test_eval.py gate and author baseline.json** - `c9eabfa` (feat)

**Plan metadata:** committed via this SUMMARY + STATE/ROADMAP update commit (see below)

## Files Created/Modified
- `tests/nl2sparql/eval/runner.py` - Filled `run()`/`write_report()`; added `_load_corpus`/`_load_configs` (yaml.safe_load), `_wrap_sparql`, `_client_for`, `_canonical`/`_judge_canonical`/`_judge_execution`/`_judge` helpers. `CaseResult`/`Report` dataclasses and module constants (`EVAL_DIR`/`CORPUS_PATH`/`CONFIGS_PATH`/`REPORTS_DIR`) left untouched per the plan.
- `tests/nl2sparql/eval/test_eval.py` - New `@pytest.mark.eval` gate test comparing `run("scripted")` against `baseline.json` (aggregate + per-case).
- `tests/nl2sparql/eval/baseline.json` - New committed regression gate: scripted config's actual pass_rate/passed/total/cases.

## Decisions Made
- Authored `baseline.json` by literally running `run('scripted')` and recording its output rather than hand-computing an expected number, per the plan's explicit instruction — this guarantees the checked-in gate matches runtime behavior exactly at authoring time.
- Made per-case regression a hard `assert` (not just a comment/TODO) in `test_eval.py`, resolving RESEARCH.md's Open Question 2 in favor of the stricter gate.
- Kept the optional `execution` judge tier's `tests.helpers.oxi` import fully lazy (inside the branch, only triggered by `judge: execution` + a case `data:` field) so the default `canonical` judge path — and thus the entire scripted CI gate — has no pyoxigraph dependency.
- Did not touch `.github/workflows/ci.yml` — RESEARCH.md/PATTERNS.md flag a new CI eval job as part of the broader phase intent, but 06-02-PLAN.md's `files_modified` frontmatter scopes this plan to `runner.py`/`test_eval.py`/`baseline.json` only; CI wiring is left for whichever later plan/task explicitly owns it.

## Deviations from Plan

**1. [Rule 1 - Bug] Reworded a runner.py comment to avoid a literal `yaml.load` substring**
- **Found during:** Task 1 acceptance-criteria verification
- **Issue:** My initial section-header comment read `# Loaders — trusted checked-in YAML, never \`yaml.load\`.` — this literally contains the substring `yaml.load`, so the plan's acceptance grep `grep -E "yaml.load\b" tests/nl2sparql/eval/runner.py` would match the comment (a false positive) even though the code only ever calls `yaml.safe_load`.
- **Fix:** Reworded the comment to `# Loaders — trusted checked-in YAML, always via yaml's safe_load only.` — same guidance, no literal `yaml.load` substring anywhere in the file.
- **Files modified:** tests/nl2sparql/eval/runner.py
- **Verification:** `grep -E "yaml\.load\b" tests/nl2sparql/eval/runner.py` now returns nothing; `_load_corpus`/`_load_configs` still call `yaml.safe_load` exclusively.
- **Committed in:** `155a503` (Task 1 commit; caught and fixed before commit, so no separate fix commit was needed).

---

**Total deviations:** 1 auto-fixed (1 bug — false-positive-prone comment wording)
**Impact on plan:** Cosmetic only; no behavior change. No scope creep.

## Issues Encountered

None beyond the deviation above. The environment note in this plan's instructions (analyzer guard passes, no `SCHEMA_ANALYZER_REQUIRED` workaround needed) held true — `arango_sparql.nl2sparql` imported cleanly and `RUN_EVAL=1 pytest -m eval` collected and ran without any missing-package error.

## User Setup Required

None - no external service configuration required. The `scripted` config used by the enforced gate makes zero network calls (`ScriptedLLMClient` needs no API key). Real-provider configs (`openai-gpt4o-mini` in `configs.yml`) remain documented but unexercised — `NL2SPARQL_API_KEY`/`OPENAI_API_KEY`/etc. are only consulted if a future plan actually runs a non-scripted config.

## Next Phase Readiness

- The NL→SPARQL eval harness is now fully functional and gate-enforced: `run(config_name)` + `write_report()` work end-to-end, `baseline.json` is a real (not aspirational) regression gate, and `RUN_EVAL=1 pytest -m eval` is green.
- Phase 7 (few-shot index) can now use this harness to measure pass-rate lift from BM25 few-shot examples — the `PromptBuilder.few_shot_examples` seam already exists and this harness gives Phase 7 a numeric before/after comparison via `run("scripted")` (and, once real-provider baselines are recorded, real-provider configs too).
- No blockers. One follow-up noted but explicitly out of this plan's scope: wiring a new `.github/workflows/ci.yml` job (`RUN_EVAL=1 pytest -m eval`) so the gate runs in CI per RESEARCH.md Pitfall 3 — flagging this so it isn't silently dropped before `/gsd-verify-work` if a later plan doesn't pick it up explicitly.

## Self-Check: PASSED

- FOUND: tests/nl2sparql/eval/runner.py (grep -c 'raise NotImplementedError' == 0)
- FOUND: tests/nl2sparql/eval/test_eval.py
- FOUND: tests/nl2sparql/eval/baseline.json
- FOUND commit: 155a503 (Task 1)
- FOUND commit: c9eabfa (Task 2)

---
*Phase: 06-nl-sparql-eval-harness-seed-corpus*
*Completed: 2026-07-15*

---
phase: 07-nl-sparql-dense-few-shot-retrieval
plan: 03
subsystem: nl-pipeline
tags: [few-shot, engine-adapter, packaging, dense-retrieval, sc1, sc2]

# Dependency graph
requires:
  - phase: 07-01
    provides: "DenseRetriever + FewShotIndex.from_corpus_files(mode=) + cached_few_shot_index() (arango_query_core.nl), pushed to origin at a5a42cdc89184ebbc9896198071a4ea8f0b7aa20"
  - phase: 07-02
    provides: "tests/nl2sparql/eval/fewshot_bank.yml (23-example curated bank) + the D-02 leakage gate"
provides:
  - "SparqlAdapter.few_shot_index() returns a populated FewShotIndex (mode=auto) via the memoized cached_few_shot_index factory — SC1"
  - "NlPipeline drives the engine with few_shot_k=3 and exposes few_shot_k/few_shot_index passthroughs for Plan 04's 3-arm sweep"
  - "tests/nl2sparql/test_fewshot_engine_prompt.py — the SC2 gate proving examples land in the engine-built prompt, not the standalone PromptBuilder"
  - "arango-sparql-py pyproject.toml: bumped nl-extra arango-query-core pin + torch-free-by-default [dense] extra"
affects: [07-04]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Module-scope memoized FewShotIndex construction consumed via cached_few_shot_index(bank_path, mode) — never built inline in a per-request/per-adapter seam (Pitfall 1)"
    - "Explicit-constructor-injection-wins-over-memoized-default seam shape (few_shot_index=None constructor param checked before the module cache) — lets tests / the Plan 04 sweep inject a fake/scripted index without touching the real bank file"
    - "Torch-free-by-default packaging: a dedicated [dense] extra composing arango-query-core[dense] on top of [nl], so a plain .[nl] install never pulls sentence-transformers/torch"

key-files:
  created:
    - tests/nl2sparql/test_fewshot_engine_prompt.py
  modified:
    - pyproject.toml
    - uv.lock
    - arango_sparql/nl2sparql/engine_adapter.py
    - arango_sparql/nl2sparql/pipeline.py
    - tests/nl2sparql/test_engine_adapter.py

key-decisions:
  - "PRODUCTION seam requests mode=\"auto\" (D-05), NOT mode=\"dense\" — a deployment lacking the .[dense] extra gracefully degrades to BM25 then no-op rather than crashing; documented in the few_shot_index() docstring as a WARNING that the measured NL-FEW-02 dense lift only applies to .[dense] deployments, and Plan 04 will report the bm25 arm as the honest default-install number"
  - "M5 precondition verified BEFORE touching the pin: confirmed a5a42cdc89184ebbc9896198071a4ea8f0b7aa20 present on origin/main via `git ls-remote https://github.com/arango-solutions/arango-query-core.git` (returned the SHA at refs/heads/main) before running `uv lock`"
  - "Environment re-synced via `uv sync --extra dev --extra nl` (no --extra dense) — matches CI's `pip install -e '.[dev,nl,service]'` convention closely enough to exercise the bumped pin; torch/sentence-transformers were NOT installed this plan"
  - "Fixed a pre-existing, unrelated-to-this-plan bug in TestVerdictReproduction.test_engine_reproduces_baseline_verdicts (same file Task 3 edits): it predates the 06.2 expect_refusal corpus additions and never learned to judge them, and its final pass_rate assertion hardcoded a stale 6-case-corpus number (0.8333) against the current 25-case 06.2 corpus (0.96). Verified this was ALREADY broken at the tip of 06.2 (commit 16fe4bd), before Phase 7 touched anything — Rule 1 auto-fix, in-scope file."
  - "SC2 gate test uses mode=\"bm25\" (not dense) to stay no-network/no-torch on the default fast path; the dense retrieval ranking logic itself is covered by arango-query-core's own unit tests (07-01)"

patterns-established:
  - "Any future engine-adapter seam that needs a model-backed or otherwise expensive index should follow the injected-then-cached shape: explicit constructor param checked first, memoized module-scope factory as the default fallback"

requirements-completed: [NL-FEW-01]

# Metrics
duration: ~20min
completed: 2026-07-21
---

# Phase 7 Plan 03: Flip the few-shot seam ON (SC1 + SC2) Summary

**`SparqlAdapter.few_shot_index()` now returns a populated `FewShotIndex` (mode="auto", memoized via `cached_few_shot_index`) instead of `None`, `NlPipeline.run()` drives the engine with `few_shot_k=3`, and a new SC2 gate proves retrieved examples land in the engine-built `## Examples` prompt section — not the standalone `PromptBuilder`.**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-07-21T18:04:46Z (STATE.md session marker)
- **Completed:** 2026-07-21T18:15:23Z
- **Tasks:** 3
- **Files modified:** 5 (pyproject.toml, uv.lock, engine_adapter.py, pipeline.py, test_engine_adapter.py) + 1 created (test_fewshot_engine_prompt.py)

## Accomplishments

- **M5 precondition verified on origin BEFORE touching the pin:** `git ls-remote https://github.com/arango-solutions/arango-query-core.git` confirmed `a5a42cdc89184ebbc9896198071a4ea8f0b7aa20` at `refs/heads/main` — the exact SHA 07-01 recorded as pushed.
- **Pin bumped + environment re-synced:** `arango-sparql-py`'s `nl` extra now pins `arango-query-core@a5a42cdc89184ebbc9896198071a4ea8f0b7aa20` (was `c5b6026c...`). `uv lock` regenerated `uv.lock` (not hand-edited); `uv sync --extra dev --extra nl` (no `--extra dense`) installed the bumped git ref fresh into `.venv`. `import arango_query_core.nl; from arango_query_core.nl import cached_few_shot_index, DenseRetriever` imports cleanly — no torch/sentence-transformers pulled.
- **Torch-free-by-default `[dense]` extra added:** composes `arango-sparql-py[nl]` + `arango-query-core[dense]@<same SHA>` — the only install path that pulls torch. `grep -c sentence-transformers` in the `nl` extra is 0.
- **`SparqlAdapter.few_shot_index()` flipped (SC1):** returns `self._few_shot_index` when explicitly injected (tests / Plan 04 sweep), else `cached_few_shot_index(str(_FEWSHOT_BANK_PATH), self._few_shot_mode)` — the memoized module-scope factory (Pitfall 1), never a fresh `FewShotIndex.from_corpus_files(...)` per adapter construction. Production default `few_shot_mode="auto"` (D-05) — a deployment lacking `.[dense]` degrades to BM25 then no-op, never crashes.
- **`NlPipeline` flipped:** `few_shot_k=0` → `few_shot_k=3` (rule-300 ≤3-shot cap) in the `NLQueryEngine(...)` construction inside `run()`; gained optional `few_shot_k`/`few_shot_index` constructor params (defaulting to `3`/`None`) threaded into the `SparqlAdapter`/`NLQueryEngine` construction, so Plan 04's zero/dense/BM25 arm-selection sweep can override both without another edit to `pipeline.py`.
- **SC2 gate landed:** new `tests/nl2sparql/test_fewshot_engine_prompt.py` builds a small BM25-mode `FewShotIndex` from an in-test corpus with a unique sentinel token, injects it into `SparqlAdapter`, and asserts `NLQueryEngine._system_prompt()` contains both `"## Examples"` and the sentinel — while `SparqlAdapter.grammar_prompt_section("")` (the standalone `PromptBuilder` path) contains neither. No-network, no-torch (`mode="bm25"`).
- **Zero-shot lock deliberately released:** `TestSparqlAdapterSeams.test_few_shot_index_is_none` replaced with `test_few_shot_index_returns_populated_index`, asserting a `FewShotIndex` instance is returned.
- Full regression sweep green: `tests/nl2sparql/test_engine_adapter.py` + `test_fewshot_engine_prompt.py` (16 passed), `tests/nl2sparql/test_pipeline.py` (14 passed), `RUN_EVAL=1 pytest -m eval -q` (38 passed, 1 skipped), `pytest -m w3c -q` (343 passed, 191 skipped, 23 xfailed — W3C coverage untouched), full default suite `pytest -q -ra` (1650 passed).

## Task Commits

Each task was committed atomically:

1. **Task 1: Verify origin precondition, bump the arango-query-core pin, add [dense] extra, re-sync env** - `e984dd1` (feat)
2. **Task 2: Flip SparqlAdapter.few_shot_index() and NlPipeline few_shot_k** - `7bcbac9` (feat)
3. **Task 3: Replace test_few_shot_index_is_none + add the SC2 engine-built-prompt gate** - `67aa41f` (test)

**Plan metadata commit:** recorded below (SUMMARY.md + STATE.md + ROADMAP.md + REQUIREMENTS.md).

## Files Created/Modified

- `pyproject.toml` — bumped `nl` extra's `arango-query-core` git pin to `a5a42cdc89184ebbc9896198071a4ea8f0b7aa20`; added a torch-free-by-default `dense` extra
- `uv.lock` — regenerated via `uv lock` (not hand-edited)
- `arango_sparql/nl2sparql/engine_adapter.py` — `SparqlAdapter` gains `few_shot_index`/`few_shot_mode` constructor params; `few_shot_index()` seam returns a populated, memoized index (mode=auto default); updated seam docstring table + operational `.[dense]` WARNING note
- `arango_sparql/nl2sparql/pipeline.py` — `NlPipeline` gains `few_shot_k`/`few_shot_index` constructor params (defaults `3`/`None`); `run()` threads them into `SparqlAdapter`/`NLQueryEngine` construction (flipped from the hardcoded `few_shot_k=0`)
- `tests/nl2sparql/test_engine_adapter.py` — `test_few_shot_index_is_none` replaced with `test_few_shot_index_returns_populated_index`; `test_engine_reproduces_baseline_verdicts` fixed (see Deviations)
- `tests/nl2sparql/test_fewshot_engine_prompt.py` (new) — the SC2 gate

## Decisions Made

See frontmatter `key-decisions` for the full list. Most notable:

- **Production mode is `"auto"`, not `"dense"`** (Open Question 1, resolved per RESEARCH/CONTEXT D-05): the shipped `SparqlAdapter` gracefully degrades without torch. This means the measured NL-FEW-02 dense-mode lift (Plan 04) applies to production **only** when the service is installed with `.[dense]`. A default `.[nl]`-only install silently runs BM25/no-op. This caveat is documented directly in the `few_shot_index()` method docstring so it isn't lost.
- **M5 origin-fetchability was verified live**, not assumed from the 07-01 SUMMARY's claim alone — `git ls-remote` against `arango-solutions/arango-query-core.git` confirmed the SHA before any `uv lock` call.
- **Environment sync used `--extra dev --extra nl`** rather than a bare `uv sync` (which only installs base deps, no extras) — matches the repo's own `tests/nl2sparql/eval/README.md` (`uv sync --extra nl`) and CI (`pip install -e ".[dev,nl,service]"`) conventions closely enough that `cached_few_shot_index`/`DenseRetriever` import correctly from the bumped pin.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug, pre-existing, in-scope file] Fixed `test_engine_reproduces_baseline_verdicts`'s stale refusal-blind logic + hardcoded pass-rate**
- **Found during:** Task 2 verification (running the full `test_engine_adapter.py` suite before committing the seam flip)
- **Issue:** The test iterates `corpus.yml`'s 25 cases (including the 3 `expect_refusal` negatives added in 06.2) but computes `engine_pass` using only the non-refusal canonical-comparison formula — for a refusal case, `case["expected"]` is human-rationale prose (not SPARQL), so `_canonical(case["expected"])` always returns `None`, making `engine_pass` always `False` regardless of the engine's actual (correct) refusal behavior. The final assertion also hardcoded `pass_rate == 0.8333...` (the original 6-case Phase-6 corpus's rate) against the current 25-case corpus (actual scripted rate 0.96). Verified via `git stash` + a checkout of `16fe4bd` (06.2 completion commit) that this exact failure **already existed before Phase 7 touched anything** — a genuine pre-existing regression from 06.2, not caused by this plan's pin bump or seam flip.
- **Fix:** Added an `if case.get("expect_refusal"): engine_pass = not res.ok` branch mirroring `tests/nl2sparql/eval/runner.py::_judge`'s inverted-refusal semantics (pass iff no validated query was produced) for the refusal cases; replaced the hardcoded `0.8333` with `sum(1 for v in baseline.values() if v) / len(baseline)` (derived from `baseline.json` itself) so the assertion can't go stale again as the corpus grows.
- **Files modified:** `tests/nl2sparql/test_engine_adapter.py`
- **Verification:** `pytest tests/nl2sparql/test_engine_adapter.py -q` — 15/15 passed (was 13/15 before the fix, with `test_few_shot_index_is_none` also failing for the expected/deliberate reason).
- **Committed in:** `67aa41f`

---

**Total deviations:** 1 auto-fixed (1 bug, pre-existing/in-scope-file)
**Impact on plan:** No scope creep — this was strictly required to satisfy the plan's own Task 3 acceptance criterion ("`test_engine_reproduces_baseline_verdicts` still reports... the scripted pass-rate") against the repo's actual current corpus state, which the plan's own read-first notes had assumed (stale) was still 0.8333/6-case. The fix makes the assertion self-consistent with `baseline.json` going forward instead of re-hardcoding a new magic number.

## Issues Encountered

None beyond the pre-existing test bug documented above.

## User Setup Required

None — no external service configuration required. `sentence-transformers`/`torch` install remains deferred to Plan 04's gated sweep checkpoint; this plan's environment sync explicitly omitted `--extra dense`.

## Requirements Note

`NL-FEW-01` ("dense retriever wired via `SparqlAdapter.few_shot_index()`, engine-side, ≤3 shots, BM25 as fallback") is now **complete**: 07-01 landed the engine-side `DenseRetriever`/`mode=`/memoization, 07-02 landed the curated bank + leakage gate, and this plan (07-03) landed the seam-wiring flip (SC1) and the engine-prompt-render proof (SC2). Marked complete in `REQUIREMENTS.md` by this plan's metadata commit.

`NL-FEW-02` ("measurable positive delta over the 06.2 live baseline") remains open — it is Plan 04's gated lift-measurement sweep.

## Next Phase Readiness

- Plan 04 can rely on: `SparqlAdapter(few_shot_index=..., few_shot_mode=...)` and `NlPipeline(few_shot_k=..., few_shot_index=...)` for explicit zero/dense/BM25 arm selection without further edits to `engine_adapter.py`/`pipeline.py`.
- Plan 04's D-06 belt-and-suspenders guard can use `FewShotIndex.retriever` (07-01) + explicit `mode="dense"` construction (hard-raises without `.[dense]` installed — measurement integrity).
- Plan 04 must install `.[dense]` (torch/sentence-transformers) itself, behind its own gated checkpoint — this plan deliberately did not install it.
- Plan 04's reporting must reflect the operational caveat recorded in `few_shot_index()`'s docstring: the bm25 arm is the honest default-install (`.[nl]`-only) number; the dense-lift headline is scoped to `.[dense]` deployments.
- No blockers identified for Plan 04.

## Self-Check: PASSED

All modified/created files confirmed present on disk (`pyproject.toml`, `uv.lock`, `arango_sparql/nl2sparql/engine_adapter.py`, `arango_sparql/nl2sparql/pipeline.py`, `tests/nl2sparql/test_engine_adapter.py`, `tests/nl2sparql/test_fewshot_engine_prompt.py`); all 3 task commits (`e984dd1`, `7bcbac9`, `67aa41f`) confirmed present in `git log`; this SUMMARY.md confirmed present on disk.

---
*Phase: 07-nl-sparql-dense-few-shot-retrieval*
*Completed: 2026-07-21*

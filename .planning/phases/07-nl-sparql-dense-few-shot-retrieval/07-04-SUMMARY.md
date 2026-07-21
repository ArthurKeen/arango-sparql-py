---
phase: 07-nl-sparql-dense-few-shot-retrieval
plan: 04
subsystem: nl-pipeline
tags: [dense-retrieval, few-shot, mcnemar, eval-harness, gpt-5, statistics, w3c-coverage-gate]

# Dependency graph
requires:
  - phase: 07-01
    provides: "DenseRetriever + FewShotIndex.from_corpus_files(mode=) + .retriever property + cached_few_shot_index()"
  - phase: 07-02
    provides: "fewshot_bank.yml (23-example curated bank) + D-02 leakage gate"
  - phase: 07-03
    provides: "SparqlAdapter.few_shot_index()/NlPipeline few_shot_k passthrough (SC1/SC2), mode=auto production default"
provides:
  - "_is_reasoning_model temperature guard in OpenAICompatibleClient (gpt-5/o1/o3/o4 omit temperature) + gpt-5/gpt-5-mini pricing rows"
  - "configs.yml 3-arm x 3-model matrix (zero/dense/bm25 x gpt-4o-mini/gpt-5-mini/gpt-5); runner.run() threads few_shot config via a once-per-arm memoized index + D-06 isinstance(retriever, DenseRetriever) guard"
  - "BaselineConfig D-04 embedding-provenance fields; pure-Python paired_mcnemar/bootstrap_paired_delta helpers (no scipy)"
  - "README.md Section 7 -- the redesigned N>=5, paired-McNemar-primary sweep runbook"
  - "tests/w3c/test_coverage_gate.py -- committed, asserting SC4 gate (QUERY_EVAL >= 96.4%) wired into a real ci.yml w3c-coverage job"
  - "NL2SPARQL_TIMEOUT env knob on OpenAICompatibleClient (reasoning-model latency, added mid-checkpoint as an enabling fix)"
  - "The completed, human-run NL-FEW-02 lift sweep result, folded into baseline.json's phase07_dense_few_shot_sweep key (aggregate-only, with full D-04 provenance) -- closed via the documented-null completion path"
affects: [08]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pure-Python exact McNemar test (math.comb-based) + seeded bootstrap percentile CI as the paired confirmatory-analysis convention for future eval-harness lift claims (no scipy dependency)"
    - "Pre-registered single confirmatory test + explicitly-labeled SECONDARY/EXPLORATORY comparisons, published MDE, and a documented-null completion path as the discipline for any future 'does X lift NL quality' claim in this repo"
    - "Aggregate-only sibling top-level key in baseline.json (outside `configs`) as the fold-in shape when only summary statistics (not full per-case verdict dicts) are available from a human-run sweep"

key-files:
  created:
    - tests/nl2sparql/test_client_reasoning_model.py
    - tests/w3c/test_coverage_gate.py
    - .planning/phases/07-nl-sparql-dense-few-shot-retrieval/07-04-SUMMARY.md
  modified:
    - arango_sparql/nl2sparql/client.py
    - arango_sparql/nl2sparql/cost.py
    - tests/nl2sparql/eval/configs.yml
    - tests/nl2sparql/eval/runner.py
    - tests/nl2sparql/eval/README.md
    - tests/nl2sparql/eval/test_eval.py
    - tests/nl2sparql/eval/baseline.json
    - .github/workflows/ci.yml

key-decisions:
  - "NL-FEW-02 is CLOSED via the plan-sanctioned human-accepted-documented-null completion path, NOT via a passed pre-registered confirmatory test: the gpt-4o-mini dense-vs-freshly-run-zero paired McNemar returned p=0.453 (b=5, c=2), which does not clear p<0.05, interpreted against the published ~16pt/4-case MDE at n=25 -- a 'cannot-confirm-at-this-power' null, not proof of no effect."
  - "The SECONDARY bm25-vs-zero comparison on gpt-4o-mini IS statistically significant (p=0.0312, b=6, c=0, delta=+0.24) but is reported as secondary/exploratory, not relabeled as the confirmatory result -- bm25 was a pre-specified arm, but this specific pairing was post-hoc relative to the pre-registered dense-vs-zero test."
  - "Lexical BM25 outperforms dense embeddings in every one of the 3 model tiers (gpt-4o-mini 0.568 vs 0.504; gpt-5-mini 0.440 vs 0.296; gpt-5 0.552 vs 0.496) -- this contradicts the phase's founding SOTA-survey thesis (dense ranked #1, +21 F1) and is documented as a genuine finding, not spun as a partial success."
  - "Sweep results folded into baseline.json as a sibling `phase07_dense_few_shot_sweep` top-level key (outside `configs`), not as `configs['*-dense']`/`['*-bm25']` BaselineConfig entries -- because only aggregate pass-rates + a single fresh McNemar pairing were captured (not full per-case verdict dicts across all N=5 runs per arm), and BaselineConfig.cases is a required dict[str,bool] field that a partial fold-in would have to fabricate to satisfy."
  - "Resolved dated model snapshot ids (for the bare gpt-4o-mini/gpt-5-mini/gpt-5 aliases) were not captured this sweep; the M2 dense-vs-committed-0.32 (06.2) continuity check is recorded as inapplicable/invalid rather than silently treated as valid, since 06.2's resolved snapshot was also never recorded."

patterns-established:
  - "Any future 'prove a quality lift' measurement in this repo should follow this plan's discipline: ONE pre-registered paired test as the sole pass/fail bar, all other comparisons explicitly labeled secondary/exploratory, a published minimum-detectable-effect so nulls aren't over-read, and a documented-null closure path when the human-run sweep returns a null that the plan itself pre-authorized as an acceptable outcome."

requirements-completed: [NL-FEW-02]

# Metrics
duration: ~35min (Tasks 1-3: ~7min autonomous execution; Task 4: human-run sweep out-of-band + ~28min continuation fold-in/summary)
completed: 2026-07-21
---

# Phase 7 Plan 04: 3-arm x 3-model dense-few-shot lift sweep Summary

**The pre-registered gpt-4o-mini dense-vs-zero paired McNemar test returned a documented null (p=0.453, b=5/c=2) against a ~16pt minimum-detectable-effect at n=25; NL-FEW-02 closes via the plan's human-accepted-documented-null path, while a secondary bm25-vs-zero comparison IS significant (p=0.031) and lexical BM25 beat dense embeddings in every one of 3 model tiers -- a genuine finding that contradicts the phase's founding dense-#1 thesis.**

## Performance

- **Duration:** ~35 min total across two sessions -- Tasks 1-3 (~7 min autonomous execution, commits `7ce312e`/`b2aa008`/`f1c327e`) then a blocking-human checkpoint (out-of-band: package legitimacy check, `.[dense]` install, live model-resolution check, N=5 runs per (model,arm) across 3 arms x 3 models = 45 live OpenAI runs, paired McNemar/bootstrap computation) then this continuation's Task 4 fold-in (~28 min: baseline.json edit, this SUMMARY, tracking updates)
- **Started:** 2026-07-21T18:23:38Z (Task 1 commit)
- **Completed:** 2026-07-21T23:10:04Z (this fold-in)
- **Tasks:** 4 (3 autonomous + 1 blocking-human checkpoint, now resolved)
- **Files modified:** 9 (client.py, cost.py, configs.yml, runner.py, README.md, test_eval.py, baseline.json, ci.yml, + 2 new test files)

## Accomplishments

- **Task 1 (temperature guard + pricing):** `_is_reasoning_model` predicate omits `temperature` for gpt-5/o1/o3/o4-family models in `OpenAICompatibleClient.generate()` (avoiding the 400 RESEARCH Pitfall 2 predicted); gpt-5/gpt-5-mini pricing rows added to `cost.py`; a no-network unit test locks the body-shape behavior.
- **Task 2 (harness extension):** `configs.yml` gained the additive 3-arm x 3-model matrix; `runner.run()` threads `few_shot` config into `NlPipeline` via the 07-03 passthrough with a once-per-arm memoized `cached_few_shot_index` and a D-06 `isinstance(retriever, DenseRetriever)` guard; `BaselineConfig` gained the three D-04 provenance fields; pure-Python `paired_mcnemar`/`bootstrap_paired_delta` helpers added (no scipy) -- `run(config_name) -> Report` stayed byte-identical.
- **Task 3 (runbook + gates):** README.md Section 7 documents the redesigned N>=5, paired-McNemar-primary sweep runbook (MDE, exploratory tiers, both install numbers, model-resolution check, MANUAL fold-in); `test_dense_baseline_companion_structural` added to `test_eval.py` (no-network, skips until a `-dense`-suffixed entry exists); `tests/w3c/test_coverage_gate.py` is a new committed test asserting QUERY_EVAL coverage >= 96.4%, wired into a new `w3c-coverage` CI job (off the xfail-tolerant `-m w3c` path).
- **Mid-checkpoint enabling fix (`3136c17`):** added a configurable `NL2SPARQL_TIMEOUT` env knob to `OpenAICompatibleClient` (default unchanged at 30s) so the gpt-5/gpt-5-mini reasoning tiers -- which spend tokens thinking before responding -- didn't spuriously time out during the live sweep; this was necessary latency-tier support that Task 1's temperature fix alone didn't cover.
- **Task 4 (the live sweep, human-run):** the credentialed human confirmed `sentence-transformers`/`torch` package legitimacy, ran `uv sync --extra dense`, and executed N=5 runs per (model, arm) across the full 3-arm x 3-model matrix (45 live OpenAI runs total) with `NL2SPARQL_TIMEOUT=120` for the gpt-5 tiers. Results and provenance are recorded below and folded into `baseline.json`.

## The NL-FEW-02 Measurement Result

### PRIMARY confirmatory test (pre-registered, the sole pass/fail bar)

On the **gpt-4o-mini anchor**: dense vs a freshly-run zero arm, paired McNemar over the same 25 cases.

| Metric | Value |
|---|---|
| b (zero-fail -> dense-pass flips) | 5 |
| c (zero-pass -> dense-fail flips) | 2 |
| McNemar p-value | 0.453 |
| Paired pass-rate delta | +0.12 |
| Bootstrap 95% CI | (-0.08, 0.32) |
| **Verdict** | **NULL -- does not clear p < 0.05** |

**Minimum detectable effect context:** at n=25 and a base pass-rate of ~0.32, this paired design detects roughly a 4-case (~16pt) lift at p<0.05. A +12pt point estimate with a CI spanning zero is exactly the "cannot confirm at this power" outcome the plan pre-registered as a legitimate, non-cherry-pickable result -- it is **not** proof dense few-shot has no effect, only that this sweep's N did not reach significance.

**NL-FEW-02 disposition:** closed via the plan's own human-accepted-documented-null completion path (07-04-PLAN.md Task 4 acceptance criteria explicitly allow "a documented, human-accepted null ... not over-read"). This is **explicitly not** a passed confirmatory test -- stating that distinction plainly, per the checkpoint's own instruction, rather than reframing the null as a win.

### SECONDARY test: bm25-vs-zero (gpt-4o-mini) -- IS significant

| Metric | Value |
|---|---|
| b | 6 |
| c | 0 |
| McNemar p-value | 0.0312 |
| Paired pass-rate delta | +0.24 (0.376 -> 0.568) |
| Bootstrap 95% CI | (0.08, 0.40) |
| **Verdict** | **SIGNIFICANT** |

This is reported as **secondary/exploratory**, not the confirmatory result: bm25 was a pre-specified arm in the matrix, but the bm25-vs-zero comparison itself was not the pre-registered test (dense-vs-zero was). The confirmatory verdict for NL-FEW-02 remains the dense null above -- this secondary result is not substituted in its place.

### EXPLORATORY tiers (gpt-5-mini, gpt-5) -- reported unfiltered, no cherry-picking

| Model | dense-vs-zero b | c | p | delta | CI 95% | Verdict |
|---|---|---|---|---|---|---|
| gpt-5-mini | 1 | 1 | 1.000 | 0.00 | (-0.12, 0.12) | null (no effect) |
| gpt-5 | 5 | 1 | 0.219 | +0.16 | (0.00, 0.36) | null (point estimate +16pt, not significant at n=25) |

Neither tier shows a statistically significant dense lift. gpt-5's null is a point estimate identical in magnitude to the gpt-4o-mini anchor's (+16pt) but with a different discordant-pair split (5/1 vs 5/2) -- both are underpowered nulls at this n, not evidence of a genuine ceiling effect specifically, though a ceiling-effect explanation remains plausible for the flagship tier per the phase's original framing.

### Full per-(model, arm) mean pass-rate table (N=5 runs each)

| Model | zero | dense | bm25 | dense stdev | bm25 stdev | zero stdev |
|---|---|---|---|---|---|---|
| gpt-4o-mini | 0.376 | 0.504 | 0.568 | -- (not captured) | 0.016 | -- (not captured) |
| gpt-5-mini | 0.240 | 0.296 | 0.440 | 0.032 | 0.025 | 0.044 |
| gpt-5 | 0.264 | 0.496 | 0.552 | 0.065 | 0.039 | 0.041 |

Per-(model,arm) standard deviation is reported per the plan's own framing: a **secondary noise-floor sanity check only**, never the pass/fail bar. gpt-4o-mini's zero/dense stdevs were not captured in the paste-back (only bm25's was); this is listed as a limitation below, not backfilled with an assumption.

### BOTH install numbers (M3)

| Model | DEFAULT-INSTALL (bm25, production `.[nl]`) | DENSE-INSTALL (dense, `.[dense]` extra) |
|---|---|---|
| gpt-4o-mini | 0.568 | 0.504 |
| gpt-5-mini | 0.440 | 0.296 |
| gpt-5 | 0.552 | 0.496 |

The default-install (bm25) number is **higher** than the dense-install number in every tier. The headline dense-lift claim explicitly does NOT hold for `.[dense]` deployments at this bank size -- the honest number for anyone installing without `.[dense]` (bm25) actually outperforms the number for anyone who pays the torch/sentence-transformers cost (dense).

### Key findings (reported honestly, no spin)

1. The pre-registered confirmatory test (dense vs zero, gpt-4o-mini) is a documented NULL (p=0.453), interpreted against the ~16pt MDE -- a "cannot-confirm-at-n=25" null, NOT proof of no effect. NL-FEW-02 closes on the human-accepted-documented-null path, not a passed confirmatory test.
2. Lexical BM25 >= dense embeddings in EVERY tier (gpt-4o-mini 0.568>0.504; gpt-5-mini 0.440>0.296; gpt-5 0.552>0.496). This directly contradicts the phase's founding thesis (the SOTA survey ranked dense embeddings #1, up to +21 F1). It is reported as a genuine finding, not reframed as a partial success.
3. The SECONDARY significant result (gpt-4o-mini bm25-vs-zero, p=0.031, 6/0 favorable flips, +19pt) is real but is explicitly NOT the confirmatory result -- bm25 was pre-specified as an arm, but this specific pairing was post-hoc relative to the pre-registered dense-vs-zero test.
4. Default-install story (M3): production `SparqlAdapter` requests `mode="auto"` (07-03 D-05) and a torch-free `.[nl]` install never runs dense in practice -- the significant lift rides entirely on the DEFAULT install (bm25). Dense's extra weight (torch + sentence-transformers) did not pay off at this ~18-24-item bank size.
5. gpt-4o-mini's zero-shot pass-rate (0.376) beat both gpt-5 tiers' zero-shot pass-rates (0.24-0.26). Recorded as an observation only, not over-interpreted -- most plausibly a reasoning-model output-shape vs judge-strictness interaction (e.g. the canonical judge's exact-algebra-match may penalize gpt-5's answer phrasing/verbosity differently than gpt-4o-mini's), not evidence gpt-5 is "worse" at the underlying task.

### Provenance (D-04) and limitations

| Field | Value |
|---|---|
| `embedding_model` | `sentence-transformers/all-MiniLM-L6-v2` |
| `embedding_revision` | `7dbbc90392e2f80f3d3c277d6e90027e55de9125` |
| `sentence_transformers_version` | `5.6.0` |
| `corpus_sha` | `d3d3806` (unchanged since 06.2's committed live baseline) |
| Model aliases used | `gpt-4o-mini`, `gpt-5-mini`, `gpt-5` |
| Resolved dated snapshot ids | **NOT captured** -- limitation (see below) |
| N | 5 runs per (model, arm) |
| Timeout | `NL2SPARQL_TIMEOUT=120` for the gpt-5 tiers |

**Limitations, recorded honestly:**
- Only aggregate pass-rates + a single fresh-pairing McNemar/bootstrap result per comparison were captured and pasted back -- NOT full per-case verdict dicts across all 5 runs per (model, arm). This is exactly why the fold-in below is an aggregate-only sibling key in `baseline.json`, not a `configs['*-dense']` structural entry.
- Resolved model snapshot ids for the bare `gpt-4o-mini`/`gpt-5-mini`/`gpt-5` aliases were not captured. Consequently the M2 dense-vs-committed-0.32 (06.2) continuity check is **inapplicable/invalid** here: 06.2's resolved snapshot was also never recorded, so no snapshot-equality claim can be made in either direction. This continuity check is not reported as either "valid" or a number -- it is flagged inapplicable, per the plan's own instruction that it must be flagged invalid across differing/unknown snapshots.
- The 07-02 nearest-neighbor bank<->corpus similarity distribution was not re-surfaced as part of the paste-back; it is unchanged from 07-02's own measurement (not re-run this sweep) and is not repeated here to avoid implying a fresh measurement occurred.
- gpt-4o-mini's zero-arm and dense-arm per-run standard deviations were not captured in the paste-back (only bm25's stdev was reported for that tier).

### W3C coverage gate confirmation (SC4/M4)

`pytest tests/w3c/test_coverage_gate.py -q` passes (1 passed / 5 skipped in this environment where the W3C corpus fixture governs skip vs assert; the committed gate asserts QUERY_EVAL coverage >= 96.4% and is wired into the `w3c-coverage` CI job added in Task 3). No transpiler code was touched by this phase, consistent with the ~nil regression risk the gate's own docstring states.

## Task Commits

Each task was committed atomically:

1. **Task 1: Reasoning-model temperature guard + gpt-5 pricing rows** - `7ce312e` (feat)
2. **Task 2: 3-arm x 3-model configs + runner few_shot threading + paired-analysis helpers** - `b2aa008` (feat)
3. **Task 3: Redesigned sweep runbook + dense-baseline structural test + committed W3C coverage gate** - `f1c327e` (feat)
4. **Mid-checkpoint enabling fix: configurable NL2SPARQL_TIMEOUT** - `3136c17` (feat)
5. **Task 4 fold-in: dense-few-shot lift sweep results into baseline.json** - `ac19edc` (feat)

**Plan metadata commit:** recorded below (SUMMARY.md + STATE.md + ROADMAP.md + REQUIREMENTS.md).

_Note: `be4c469` (docs: record Tasks 1-3 complete) was the interim checkpoint-handoff commit from the prior session; it is metadata, not a task commit._

## Files Created/Modified

- `arango_sparql/nl2sparql/client.py` -- `_is_reasoning_model` predicate + conditional temperature omission; `NL2SPARQL_TIMEOUT` env knob (mid-checkpoint fix)
- `arango_sparql/nl2sparql/cost.py` -- gpt-5/gpt-5-mini pricing rows
- `tests/nl2sparql/eval/configs.yml` -- additive 3-arm x 3-model matrix
- `tests/nl2sparql/eval/runner.py` -- few_shot config threading, D-06 guard, BaselineConfig D-04 fields, `paired_mcnemar`/`bootstrap_paired_delta`
- `tests/nl2sparql/eval/README.md` -- Section 7 sweep runbook
- `tests/nl2sparql/eval/test_eval.py` -- `test_dense_baseline_companion_structural`
- `tests/nl2sparql/eval/baseline.json` -- `phase07_dense_few_shot_sweep` aggregate fold-in (this session)
- `tests/nl2sparql/test_client_reasoning_model.py` (new) -- temperature-guard + timeout unit tests
- `tests/w3c/test_coverage_gate.py` (new) -- committed, asserting SC4 gate
- `.github/workflows/ci.yml` -- new `w3c-coverage` job

## Decisions Made

See frontmatter `key-decisions` for the full list. Most notable: NL-FEW-02 closes via the documented-null path (not a passed confirmatory test); the secondary bm25-vs-zero significant result is reported as secondary, never substituted as the confirmatory result; the sweep is folded into `baseline.json` as an aggregate-only sibling key rather than fabricating per-case data to satisfy `BaselineConfig`'s required `cases` field; the M2 continuity check is flagged inapplicable given unrecorded snapshot ids on both sides.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added configurable `NL2SPARQL_TIMEOUT` env knob**
- **Found during:** the human-run Task 4 sweep (between Task 3's commit and this fold-in)
- **Issue:** the gpt-5/gpt-5-mini reasoning tiers spend tokens thinking before emitting output, so a single call could exceed the hardcoded 30s default that suits gpt-4o-mini -- this would have blocked the exploratory-tier runs of the live sweep.
- **Fix:** added an `NL2SPARQL_TIMEOUT` env var read in `OpenAICompatibleClient` (default unchanged at 30s; an explicit `timeout=` constructor arg still wins), then ran the gpt-5-tier arms with `NL2SPARQL_TIMEOUT=120`.
- **Files modified:** `arango_sparql/nl2sparql/client.py`, `tests/nl2sparql/test_client_reasoning_model.py`
- **Verification:** `pytest tests/nl2sparql/test_client_reasoning_model.py -q` green; the human's subsequent gpt-5/gpt-5-mini sweep runs completed without timing out.
- **Committed in:** `3136c17` (landed mid-checkpoint, before this continuation)

**2. [Rule 2 - Missing critical, schema safety] Folded the sweep in as an aggregate-only sibling key rather than fabricating per-case verdicts**
- **Found during:** this continuation's fold-in step
- **Issue:** the checkpoint's paste-back captured only aggregate pass-rates + a single fresh McNemar pairing per test, not full per-case `{name: passed}` dicts across all 5 runs per (model, arm). `BaselineConfig.cases` is a required `dict[str, bool]` field with no default, so a `configs['openai-gpt4o-mini-dense']`-style entry could not be constructed without inventing per-case data that was never actually observed.
- **Fix:** added a `phase07_dense_few_shot_sweep` top-level key in `baseline.json` (a sibling of `configs`, never touched by `BaselineConfig`/the `-dense`-suffix structural test) holding the full aggregate matrix, both primary/secondary/exploratory tests, provenance, and limitations -- honoring the plan's fold-in instructions' explicit option (b)/(a) hybrid: results ARE recorded in `baseline.json` (not merely deferred to the SUMMARY), but in a schema-compatible aggregate-only shape rather than one requiring fabricated per-case data.
- **Files modified:** `tests/nl2sparql/eval/baseline.json`
- **Verification:** `RUN_EVAL=1 pytest -m eval -q` (38 passed, 8 skipped -- unchanged from pre-edit) and `pytest tests/nl2sparql/eval/test_eval.py tests/w3c/test_coverage_gate.py -q` (1 passed, 5 skipped -- unchanged) both stay green after the edit; `test_dense_baseline_companion_structural` still skips (no `-dense`-suffixed `configs` entry was added), confirming this fold-in shape does not trip that structural gate.
- **Committed in:** `ac19edc`

---

**Total deviations:** 2 auto-fixed (1 blocking/enabling-fix landed mid-checkpoint, 1 schema-safety choice in the fold-in itself)
**Impact on plan:** No scope creep. The timeout knob was strictly necessary to complete the exploratory-tier live runs; the aggregate-only fold-in shape was the only way to record the sweep in `baseline.json` without violating the plan's own explicit "do NOT invent a per-case cases map" instruction.

## Issues Encountered

The live sweep, being human-run and out-of-band, returned less granular data than the plan's ideal (aggregate pass-rates + single-pairing McNemar rather than full per-case dicts across all N=5 runs, and no resolved model snapshot ids). Per the checkpoint's own instruction, this was treated as the authoritative, complete record -- no re-run was attempted (no key held by the executor), and the gaps are documented as limitations above rather than backfilled or assumed.

## User Setup Required

None further -- the credentialed human already completed the one-time `sentence-transformers`/`torch` package-legitimacy check and `.[dense]` install as part of resolving the Task 4 checkpoint in the prior session.

## Next Phase Readiness

- **NL-FEW-02 is now complete** (closed via the documented-null path) and **NL-FEW-01** was already complete (07-03). Phase 7's two requirements are both satisfied; Phase 7 itself is complete.
- **Phase 8 (public release readiness)** can proceed. It should inherit the honest framing established here: the phase's public-facing narrative (if any references NL-FEW quality) should NOT claim a proven dense-embedding lift -- it should state the actual, more nuanced result (a documented null on the pre-registered test, a secondary bm25 win, and BM25 beating dense across all 3 tiers at this corpus/bank size).
- **A candidate follow-up** (not scoped to any current phase) would be re-running the sweep at a larger N and/or with a larger eval corpus to increase statistical power past the ~16pt MDE ceiling that made the +12pt gpt-4o-mini dense point estimate non-significant -- flagged here for future consideration, not actioned.
- No blockers for Phase 8.

## Self-Check: PASSED

- `tests/nl2sparql/eval/baseline.json` confirmed present and valid JSON with the new `phase07_dense_few_shot_sweep` top-level key (`configs` unchanged: `scripted`, `openai-gpt4o-mini`).
- `tests/w3c/test_coverage_gate.py`, `tests/nl2sparql/test_client_reasoning_model.py` confirmed present on disk.
- This SUMMARY.md confirmed present on disk.
- Commits `7ce312e`, `b2aa008`, `f1c327e`, `3136c17`, `ac19edc` confirmed present in `git log --oneline --all`.
- `RUN_EVAL=1 pytest -m eval -q` (38 passed, 8 skipped) and `pytest tests/nl2sparql/eval/test_eval.py tests/w3c/test_coverage_gate.py -q` (1 passed, 5 skipped) both confirmed green after the fold-in commit.

---
*Phase: 07-nl-sparql-dense-few-shot-retrieval*
*Completed: 2026-07-21*

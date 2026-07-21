---
phase: 07-nl-sparql-dense-few-shot-retrieval
verified: 2026-07-21T23:40:00Z
status: gaps_found
score: 3/4 roadmap success criteria fully verified (1 partial); requirements NL-FEW-01 satisfied, NL-FEW-02 satisfied only via a documented null
overrides_applied: 0
gaps:
  - truth: "SC3 (ROADMAP): A dense few-shot eval run shows a positive pass-rate delta over the Phase 06.2 live-model baseline"
    status: partial
    reason: >
      The PRE-REGISTERED confirmatory test (the phase's own single pass/fail bar per
      D-07/D-08/D-09/B1/M1/M2) — gpt-4o-mini dense-vs-freshly-run-zero, paired McNemar
      over the same 25 cases — returned p=0.453 (b=5, c=2), a NULL result, not a proven
      lift. This is verified directly in tests/nl2sparql/eval/baseline.json's
      `phase07_dense_few_shot_sweep.primary_confirmatory_test.verdict`. The DENSE-specific
      mechanism the roadmap goal names ("Dense/embedding ≤3-shot index... prove pass-rate
      lift") is NOT proven. A SECONDARY, non-pre-registered comparison (bm25-vs-zero,
      gpt-4o-mini) IS significant (p=0.031, +19pt), and lexical BM25 beat dense embeddings
      in all 3 model tiers measured (gpt-4o-mini 0.568>0.504; gpt-5-mini 0.440>0.296;
      gpt-5 0.552>0.496) — directly contradicting the phase's founding SOTA-survey thesis
      that dense retrieval is the #1 win. The phase plan itself pre-authorized a
      "documented, human-accepted null" as a legitimate closure path (07-04-PLAN.md Task 4
      acceptance criteria), and the ROADMAP.md/STATE.md were updated honestly (not spun) to
      reflect this exact outcome — so this is not a concealed failure. But goal-backward
      verification must still report that the literal roadmap success criterion ("shows a
      positive pass-rate delta") was not achieved for the mechanism (dense embeddings) the
      phase goal names; it was achieved only via a different mechanism (BM25) that the
      phase's own design treats as the fallback/ablation, not the primary.
    artifacts:
      - path: "tests/nl2sparql/eval/baseline.json"
        issue: "phase07_dense_few_shot_sweep.primary_confirmatory_test.verdict = 'NULL -- does not clear p<0.05'; dense_stdev not captured for gpt-4o-mini; resolved model snapshot ids not captured (M2 continuity check flagged inapplicable)"
    missing:
      - "An explicit developer/product decision on whether this documented null is accepted as sufficient to close NL-FEW-02 and proceed to Phase 8, or whether a higher-N re-run (to clear the ~16pt/4-case MDE ceiling that made the +12pt point estimate non-significant at n=25) is warranted before treating dense few-shot as validated."
      - "If accepted: Phase 8's public-facing materials must not claim a proven dense-embedding pass-rate lift (per 07-04-SUMMARY's own 'Next Phase Readiness' note) — this should be an explicit go/no-go check at Phase 8 kickoff, not assumed."
  - truth: "07-01 must-have: DenseRetriever ranks a relevant example above irrelevant ones, tested via an injectable fake encoder with no network / no torch on the fast CI path (D-03)"
    status: failed
    reason: >
      The test that is supposed to prove this (test_dense_retrieval_ranks_by_relevance in
      the sibling arango-query-core repo) is non-deterministic: its _FakeEncoder fixture
      buckets tokens via Python's `hash(token) % _FAKE_DIM`, and CPython randomizes str
      hashing per-process by default (no PYTHONHASHSEED pin). This is CR-01 from
      07-REVIEW.md (rated Critical, reproduced there at ~25-30% failure over 15 runs).
      Independently re-verified during this verification: 2 failures in 8 repeated runs
      with PYTHONHASHSEED=random. The bug remains UNFIXED as of this verification — no
      commit after the review (2026-07-21T23:25:51Z) exists in the sibling repo's git log
      addressing it. A flaky fast-path unit test does not reliably prove the ranking
      behavior it exists to gate, and arango-query-core's own CI (test/publish workflows,
      no PYTHONHASHSEED pin) will intermittently fail on it too.
    artifacts:
      - path: "~/Desktop/arango-query-core/tests/test_nl_fewshot.py"
        issue: "_FakeEncoder.__call__ (lines ~24-38) uses hash(token) % _FAKE_DIM instead of a process-stable hash — non-deterministic ranking assertion"
    missing:
      - "Replace hash(token) with a deterministic hash (e.g. zlib.crc32(token.encode('utf-8'))) per the code review's CR-01 fix, then re-verify zero flakes across ~30 repeated runs, in the sibling arango-query-core repo."
human_verification:
  - test: "Confirm whether the documented NULL on the pre-registered dense-vs-zero confirmatory test is accepted as sufficient closure for NL-FEW-02, given BM25 (the phase's designated fallback/ablation) outperformed dense embeddings in every measured tier"
    expected: "A developer sign-off decision, recorded somewhere durable (STATE.md/ROADMAP.md already documents the null honestly, but no explicit 'accepted, proceeding to Phase 8 anyway' confirmation exists yet)"
    why_human: "This is a scientific/product judgment call (accept a null vs. invest in a higher-powered re-run), not a code-verifiable fact — the fact itself (p=0.453, null) is already independently confirmed in the codebase"
---

# Phase 7: NL→SPARQL Dense Few-Shot Retrieval Verification Report

**Phase Goal:** Dense/embedding ≤3-shot index via the shared engine's few-shot seam; prove pass-rate lift over the live baseline. W3C query-eval coverage must never drop below 96.4%.
**Verified:** 2026-07-21T23:40:00Z
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | A dense/embedding few-shot retriever is loaded via `arango_query_core.nl.FewShotIndex` and returned by `SparqlAdapter.few_shot_index()` (≤3 shots, rule-300); BM25 available as ablation | ✓ VERIFIED | `DenseRetriever` class + `DEFAULT_DENSE_MODEL_ID`/`DEFAULT_DENSE_REVISION` + `.retriever` property + `cached_few_shot_index()` all present in sibling repo `arango_query_core/nl/fewshot.py` (commit `a5a42cd`, pushed to origin, confirmed via `git log`). `arango-sparql-py`'s `pyproject.toml` pins this exact SHA. `engine_adapter.py` imports `cached_few_shot_index`, wires `_FEWSHOT_BANK_PATH`, and `SparqlAdapter().few_shot_index()` returns a populated `FewShotIndex` — confirmed by direct grep + passing `test_engine_adapter.py`/`test_fewshot_engine_prompt.py`/`test_pipeline.py` (30/30 passed, live-run). `NlPipeline` drives `few_shot_k=self.few_shot_k` (default 3). BM25 arms exist in `configs.yml` (`openai-*-bm25`). |
| 2 | Retrieved examples appear in the engine-built prompt's `## Examples` section (`NLQueryEngine`), not the standalone `PromptBuilder` | ✓ VERIFIED | `tests/nl2sparql/test_fewshot_engine_prompt.py` passes live (1 passed) — asserts the sentinel token appears in `_system_prompt()`'s `## Examples` section and is ABSENT from `grammar_prompt_section("")` (the standalone `PromptBuilder` path). |
| 3 | A dense few-shot eval run shows a positive pass-rate delta over the Phase 06.2 live-model baseline via the Phase 6 harness | ⚠️ PARTIAL / FAILED for the dense mechanism specifically | The PRE-REGISTERED confirmatory test (gpt-4o-mini dense-vs-freshly-run-zero, paired McNemar) returned p=0.453 (NULL) — verified directly in `tests/nl2sparql/eval/baseline.json`'s `phase07_dense_few_shot_sweep.primary_confirmatory_test`. Only a SECONDARY, non-pre-registered comparison (bm25-vs-zero, p=0.031) reached significance, and BM25 beat dense in all 3 tiers. See Gaps. |
| 4 | W3C DAWG query-eval coverage remains ≥96.4% (no transpiler regression) | ✓ VERIFIED | `tests/w3c/test_coverage_gate.py` exists, asserts (not merely runs) `analyze()[QUERY_EVAL].coverage >= 96.4`, carries no `w3c` marker (escapes the xfail-tolerant path), and is wired into a real `.github/workflows/ci.yml` job `w3c-coverage` (confirmed via grep: `test_coverage_gate` appears at both the job definition and the `pytest` invocation line). Independently re-ran `python -m pytest tests/w3c/test_coverage_gate.py -q` against the on-disk W3C corpus fixture in this environment: **1 passed**. No transpiler/translate code was touched by any of the 4 plans (confirmed by `files_modified` frontmatter across all plans — none touch `arango_sparql/translate/`). |

**Score:** 3/4 fully verified, 1 partial (dense-specific mechanism not proven; a different mechanism — BM25 — was)

### Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
|---|---|---|---|---|
| NL-FEW-01 | 07-01, 07-02, 07-03 | Dense retriever wired via `SparqlAdapter.few_shot_index()`, engine-side, ≤3 shots, BM25 fallback; acceptance = examples appear in engine prompt + unit tests pass | ✓ SATISFIED | All artifacts/key-links verified above (Truths 1-2); `requirements-completed: [NL-FEW-01]` recorded in 07-03-SUMMARY.md and REQUIREMENTS.md marks it Complete — this matches actual code state, not just the claim. |
| NL-FEW-02 | 07-04 | Measurable accuracy lift — dense few-shot run shows positive delta over Phase 06.2 live baseline; acceptance = eval report delta > 0 over the live baseline | ⚠️ PARTIALLY SATISFIED | REQUIREMENTS.md marks this "Complete," but the literal acceptance criterion (delta > 0 for the DENSE mechanism, pre-registered as the sole confirmatory bar) was NOT met (p=0.453 null). The requirement was closed via the plan's own pre-authorized "documented null" completion path, which is a legitimate and honestly-reported outcome, but is not the same as the acceptance criterion being met. Flagged for human sign-off (see `human_verification`). |

No orphaned requirements found — REQUIREMENTS.md's Phase 7 row maps exactly to NL-FEW-01/NL-FEW-02, both of which appear in a plan's `requirements:` frontmatter.

### Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `~/Desktop/arango-query-core/arango_query_core/nl/fewshot.py` | `DenseRetriever`, `mode=` dispatch, `.retriever` property, `cached_few_shot_index` | ✓ VERIFIED | All four present via grep; commit `a5a42cd` on `origin/main` |
| `~/Desktop/arango-query-core/pyproject.toml` | `[dense]` extra | ✓ VERIFIED | Present, torch-free `[nl]` |
| `~/Desktop/arango-query-core/tests/test_nl_fewshot.py` | DenseRetriever unit tests | ⚠️ FLAKY (CR-01) | Tests exist and normally pass, but `test_dense_retrieval_ranks_by_relevance` is non-deterministic (reproduced 2/8 failures independently) — see Gaps |
| `tests/nl2sparql/eval/fewshot_bank.yml` | 15-26 example curated bank, disjoint from corpus | ✓ VERIFIED | 23 examples, `RUN_EVAL=1 pytest tests/nl2sparql/eval/test_fewshot_bank_disjoint.py` passes live (7 passed, 2 skipped — similarity-ceiling env-dependent skip noted in 07-02-SUMMARY, unrelated to gate correctness) |
| `tests/nl2sparql/eval/test_fewshot_bank_disjoint.py` | 3-way disjointness + cosine ceiling + parity gate | ✓ VERIFIED | Present, passes |
| `arango_sparql/nl2sparql/engine_adapter.py` | `few_shot_index()` flip, `cached_few_shot_index` import | ✓ VERIFIED | Confirmed via grep + passing tests |
| `arango_sparql/nl2sparql/pipeline.py` | `few_shot_k=3` flip + passthroughs | ✓ VERIFIED | `few_shot_k=self.few_shot_k` present |
| `tests/nl2sparql/test_fewshot_engine_prompt.py` | SC2 gate | ✓ VERIFIED | Passes live |
| `pyproject.toml` (arango-sparql-py) | bumped nl-extra pin + `[dense]` extra | ✓ VERIFIED | Pin = `a5a42cdc89184ebbc9896198071a4ea8f0b7aa20` in both `nl` and `dense` extras |
| `arango_sparql/nl2sparql/client.py` | `_is_reasoning_model` temperature guard | ✓ VERIFIED | Present, unit-tested |
| `tests/nl2sparql/eval/configs.yml` | 3-arm x 3-model matrix | ✓ VERIFIED | 3 dense arms + 3 bm25 arms confirmed via yaml load; `scripted` unchanged |
| `tests/nl2sparql/eval/runner.py` | `paired_mcnemar`/`bootstrap_paired_delta`, D-06 guard | ✓ VERIFIED (with caveat) | Both helpers import and compute correctly (code review verified formula correctness); D-06 guard exists but implemented as a bare `assert` — see WR-01 below (strippable under `-O`/`PYTHONOPTIMIZE`) |
| `tests/w3c/test_coverage_gate.py` | Asserting SC4 gate | ✓ VERIFIED | Passes live against on-disk corpus; wired into CI job |
| `.github/workflows/ci.yml` | `w3c-coverage` job | ✓ VERIFIED | Job present, runs `fetch_w3c.sh` then the gate |

### Key Link Verification

| From | To | Via | Status | Details |
|---|---|---|---|---|
| `DenseRetriever.__init__` | `sentence_transformers.SentenceTransformer` | lazy import | ✓ WIRED | Confirmed in fewshot.py; hard-raises in explicit `mode="dense"` |
| `from_corpus_files` | `DenseRetriever`/`BM25Retriever`/`_NoopRetriever` | `mode=` dispatch | ✓ WIRED | 3-way dispatch confirmed |
| `SparqlAdapter.few_shot_index` | `arango_query_core.nl.cached_few_shot_index` | memoized factory | ✓ WIRED | grep + runtime check both confirm |
| `NlPipeline.run` | `NLQueryEngine(few_shot_k=self.few_shot_k)` | engine construction | ✓ WIRED | Confirmed |
| `NLQueryEngine._system_prompt` | `index.format_prompt_section` | engine-built `## Examples` render | ✓ WIRED | SC2 test passes |
| `OpenAICompatibleClient.generate` | `_is_reasoning_model` | conditional temperature omission | ✓ WIRED | Unit-tested |
| `runner.run` (dense arm) | `isinstance(index.retriever, DenseRetriever)` | D-06 guard | ⚠️ WIRED BUT WEAK | Guard exists at runner.py:384 but implemented as a bare `assert`, strippable via `-O`/`PYTHONOPTIMIZE` (WR-01, code review) — see Anti-Patterns |
| `runner few_shot config` | `NlPipeline(few_shot_k=..., few_shot_index=...)` | 07-03 passthrough | ✓ WIRED | Confirmed |
| `runner.paired_mcnemar`/`bootstrap_paired_delta` | `zero_report.cases` vs `dense_report.cases` | paired flip analysis | ✓ WIRED | Confirmed importable + formula-correct per code review |
| `tests/w3c/test_coverage_gate.py` | `tests.w3c.analyze_coverage.analyze` | asserts ≥96.4 | ✓ WIRED | Confirmed passing live |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|---|---|---|---|---|
| `~/Desktop/arango-query-core/tests/test_nl_fewshot.py` | 37 | `hash(token) % _FAKE_DIM` — non-deterministic Python str hash | 🛑 BLOCKER (CR-01, unresolved) | Flaky fast-path unit test for the exact must-have this plan's Task 3 was meant to lock in; independently reproduced failing 2/8 runs |
| `tests/nl2sparql/eval/runner.py` | 384-390 | Bare `assert isinstance(...)` for a measurement-integrity guard | ⚠️ WARNING (WR-01) | Strippable under `-O`/`PYTHONOPTIMIZE`; if the sweep shell ever inherits such a flag, a silently-degraded (non-dense) retriever could be recorded as a dense measurement with zero runtime signal — directly undermines the documented mitigation for threat T-07-11 |
| `~/Desktop/arango-query-core/arango_query_core/nl/fewshot.py` | ~126-134, ~257-275 | Overly broad `except ImportError` conflates "not installed" with "installed but broken" | ⚠️ WARNING (WR-02) | Verified reproducible in the reviewer's sandbox (a real tokenizers-version ImportError was silently treated as "sentence-transformers absent"); could mask a broken dense stack as a graceful degrade rather than surfacing the real root cause |
| `arango_sparql/service/routes/nl.py`, `pipeline.py`, `engine_adapter.py` | various | Production route now always loads a real few-shot index (BM25 by default) with no opt-out env var and no dedicated production-default baseline entry | ⚠️ WARNING (WR-03) | Intentional per the phase design (this IS the seam being flipped ON), but no operator kill-switch exists yet, unlike the `NL2SPARQL_TIMEOUT` precedent set in this same phase |
| `arango_sparql/nl2sparql/engine_adapter.py` | 171, 176, 202 | `few_shot_mode` constructor param never exercised with a non-default value anywhere in `arango_sparql` | ℹ️ INFO (WR-04) | Dead/unreachable-in-practice parameter; not goal-blocking |

The remaining code-review Info items (IN-01 through IN-04 — fragile `is True`/`is False` identity comparisons, unverified gpt-5 pricing, bootstrap percentile-index rounding, and the dense-baseline structural test never having run against real folded data) are minor/cosmetic and do not affect phase-goal achievement; they are not repeated in full here (see 07-REVIEW.md).

## Deviations / Notable Honesty

The phase's own documentation (ROADMAP.md, STATE.md, 07-04-SUMMARY.md, and the `phase07_dense_few_shot_sweep` key in `baseline.json`) already reports the null result transparently and does not attempt to spin it as a pass — this is commendable and independently confirmed to match the actual data. This verification's disagreement is narrower and more technical: goal-backward verification requires checking the literal roadmap success criterion against the codebase, and that criterion (a proven dense-embedding pass-rate lift) was not met, regardless of how honestly the miss was documented.

## Gaps Summary

1. **CR-01 (unresolved, verified independently):** The sibling repo's `DenseRetriever` fast-path ranking test is flaky due to Python's randomized `hash()`, undermining the 07-01 must-have that this behavior be reliably "tested... on the fast CI path." This is a concrete, closable engineering fix (swap to `zlib.crc32`), not a judgment call.
2. **NL-FEW-02's dense-specific confirmatory test is a documented null**, not a passed lift. BM25 (the phase's designated fallback/ablation) is what actually delivered a significant, positive pass-rate lift over zero-shot — and outperformed dense embeddings in every tier measured. This does not block Phase 8 mechanically, but it does mean the ROADMAP's literal SC3 wording ("shows a positive pass-rate delta" via the dense mechanism) was not achieved, and a human should explicitly decide whether that's an acceptable phase closure or whether a higher-powered re-run is warranted before Phase 8's public messaging references this work.
3. Two code-review Warnings (WR-01 bare-assert D-06 guard, WR-02 overly-broad `except ImportError`) remain unaddressed and are measurement-integrity-adjacent; they did not change the actual reported (null) result this sweep, but would matter for any future re-run's trustworthiness.

---

_Verified: 2026-07-21T23:40:00Z_
_Verifier: Claude (gsd-verifier)_

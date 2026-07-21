---
phase: 07-nl-sparql-dense-few-shot-retrieval
reviewed: 2026-07-21T23:25:51Z
depth: standard
files_reviewed: 20
files_reviewed_list:
  - arango_sparql/nl2sparql/client.py
  - arango_sparql/nl2sparql/cost.py
  - arango_sparql/nl2sparql/engine_adapter.py
  - arango_sparql/nl2sparql/pipeline.py
  - arango_sparql/service/routes/nl.py
  - tests/nl2sparql/eval/runner.py
  - tests/nl2sparql/eval/configs.yml
  - tests/nl2sparql/eval/fewshot_bank.yml
  - tests/nl2sparql/eval/baseline.json
  - tests/nl2sparql/eval/test_eval.py
  - tests/nl2sparql/eval/test_fewshot_bank_disjoint.py
  - tests/nl2sparql/test_client_reasoning_model.py
  - tests/nl2sparql/test_engine_adapter.py
  - tests/nl2sparql/test_fewshot_engine_prompt.py
  - tests/w3c/test_coverage_gate.py
  - pyproject.toml
  - .github/workflows/ci.yml
  - /Users/plosiewicz/Desktop/arango-query-core/arango_query_core/nl/fewshot.py
  - /Users/plosiewicz/Desktop/arango-query-core/arango_query_core/nl/__init__.py
  - /Users/plosiewicz/Desktop/arango-query-core/tests/test_nl_fewshot.py
findings:
  critical: 1
  warning: 4
  info: 4
  total: 9
status: issues_found
---

# Phase 07: Code Review Report

**Reviewed:** 2026-07-21T23:25:51Z
**Depth:** standard
**Files Reviewed:** 20 (16 in `arango-sparql-py`, 4 in the sibling `arango-query-core` repo)
**Status:** issues_found

## Summary

Reviewed the client.py reasoning-model temperature guard, the gpt-5 pricing rows, the
`SparqlAdapter.few_shot_index()` seam and its production wiring, the `NlPipeline`
passthroughs, the eval runner's few-shot threading + D-06 dense guard + `paired_mcnemar`/
`bootstrap_paired_delta` statistical helpers, the eval configs/bank/baseline data, the
committed W3C coverage gate + its CI job, and the sibling `arango-query-core` repo's
`DenseRetriever`/`FewShotIndex.from_corpus_files(mode=)`/`cached_few_shot_index` additions
and their unit tests.

The statistical core (`paired_mcnemar`, `bootstrap_paired_delta`) checks out: the exact
McNemar formula matches the standard `statsmodels`-style implementation (verified against
known reference values for b=c=1 and b=0,c=5), the bootstrap correctly resamples paired
case keys with replacement using a seeded RNG, and both raise on misaligned key sets as
documented. `_is_reasoning_model`'s prefix predicate and the conditional-temperature body
construction in `OpenAICompatibleClient.generate()` are correct and covered by a real
request-body-capture unit test (verified passing). The W3C coverage gate correctly asserts
and skips as designed (verified passing locally against the fetched corpus).

However, actually running the delivered test suites surfaced one concrete, reproducible,
CI-breaking bug (a flaky test in the sibling repo caused by using Python's randomized
`hash()` in a fake-encoder fixture — verified to fail ~25-30% of the time across repeated
runs), plus several design-level gaps around the new "always-on" few-shot seam reaching
production with no baseline/flag, a safety-critical guard implemented with a strippable
`assert`, and an overly broad `except ImportError` that can silently misclassify a broken
(not merely absent) ML dependency as "not installed."

## Critical Issues

### CR-01: Flaky `DenseRetriever` test due to Python's randomized `hash()` — verified failing ~25-30% of runs

**File:** `~/Desktop/arango-query-core/tests/test_nl_fewshot.py:24-38, 146-149`
**Issue:** `_FakeEncoder.__call__` buckets tokens with `vectors[row, hash(token) % _FAKE_DIM] += 1.0` (line 37). CPython randomizes `str.__hash__` per-process by default (`PYTHONHASHSEED` is not fixed), so the bucket a given token lands in — and therefore the fake cosine ranking `test_dense_retrieval_ranks_by_relevance` asserts on — is non-deterministic across process invocations. This was empirically reproduced: running `pytest tests/test_nl_fewshot.py::test_dense_retrieval_ranks_by_relevance -q` 15 times (no `PYTHONHASHSEED` pinned) failed 4/15 times (~27%), each time asserting `top[0][1] == "Q_ORDERS"` instead of the expected `"Q_MOVIES"`.

This is not a local-environment fluke: `arango-query-core`'s own `.github/workflows/ci.yml` `test` job runs `pytest -q --tb=short` across the 3.11/3.12 matrix on every push/PR with no `PYTHONHASHSEED` pin, and `publish.yml` runs the same suite before every tagged release build. Both will intermittently fail on this test, and a maintainer re-running CI "because it looked flaky" will intermittently get it green again, masking the real cause.

**Fix:** Replace the randomized `hash()` with a deterministic hash (e.g. `hashlib.md5(token.encode()).digest()` truncated to an int, or `zlib.crc32(token.encode())`), or pin `PYTHONHASHSEED=0` for the encoder's own local computation. Simplest fix in-place:
```python
import zlib

class _FakeEncoder:
    def __call__(self, texts: list[str]) -> np.ndarray:
        vectors = np.zeros((len(texts), _FAKE_DIM), dtype=float)
        for row, text in enumerate(texts):
            for token in text.lower().split():
                bucket = zlib.crc32(token.encode("utf-8")) % _FAKE_DIM
                vectors[row, bucket] += 1.0
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vectors / norms
```
`zlib.crc32` is stable across processes/interpreters (unlike `hash()` on `str`), so the test becomes fully deterministic. Re-run the affected test in a loop (`for i in $(seq 1 30); do pytest ...; done`) to confirm zero flakes after the fix.

## Warnings

### WR-01: D-06 dense-mode measurement-integrity guard is a bare `assert` — stripped by `-O`/`PYTHONOPTIMIZE`

**File:** `tests/nl2sparql/eval/runner.py:384-390`
**Issue:** The guard that is supposed to make it structurally impossible for a silently-degraded (non-dense) retriever to be recorded as a "dense" measurement is implemented as:
```python
assert isinstance(few_shot_index.retriever, DenseRetriever), (...)
```
Python strips all `assert` statements when run with `-O` or `PYTHONOPTIMIZE=1`/`2` (e.g. `python -O -c "..."`, or `PYTHONOPTIMIZE=1` set globally in some deployment/packaging environments). If the credentialed human's sweep shell has either set — for any reason, e.g. an inherited CI/perf-tuning env var — this belt-and-suspenders check silently becomes a no-op, and a BM25/no-op-degraded run (triggered, for instance, by exactly the tokenizers-version conflict this review reproduced locally — see WR-02) would be recorded and reported as a "dense" pass-rate lift with no runtime signal that anything was wrong. This directly undermines threat `T-07-11` ("a non-dense retriever silently filed as a dense lift"), which this exact line is the documented mitigation for.
**Fix:** Use an explicit, non-strippable check:
```python
if not isinstance(few_shot_index.retriever, DenseRetriever):
    raise RuntimeError(
        f"D-06 guard failed: config {config_name!r} requested mode='dense' but the "
        f"built index's retriever is {type(few_shot_index.retriever).__name__!r}, not "
        "DenseRetriever. This means sentence-transformers is not installed/importable "
        "(install `.[dense]` before running this arm) — never record this as a "
        "dense-mode measurement."
    )
```

### WR-02: Overly broad `except ImportError` conflates "not installed" with "installed but broken"

**File:** `~/Desktop/arango-query-core/arango_query_core/nl/fewshot.py:126-134` (`DenseRetriever.__init__`), and the `from_corpus_files` dense/auto branches (lines ~257-275)
**Issue:** Verified locally: in this sandbox's Python environment `sentence-transformers` (and `torch`) ARE installed, but `import sentence_transformers` itself raises `ImportError: tokenizers>=0.22.0,<=0.23.0 is required for a normal functioning of this module, but found tokenizers==0.21.4.` — a genuine, real ImportError raised deep inside `transformers`' own version-check machinery, not the "package absent" case the error-handling code assumes. `DenseRetriever.__init__`'s `except ImportError as exc: raise ImportError("...requires the 'sentence-transformers' package. Install it with `pip install sentence-transformers`...")` (and the `auto`-mode degrade-to-BM25 path in `from_corpus_files`) catch this exact scenario identically to a true "not installed" case:
1. In `mode="auto"` (the production default), a broken-but-present dense stack silently degrades to BM25 with only a `logger.info` breadcrumb — an operator who believes `.[dense]` is correctly installed gets no signal that dense retrieval never actually ran.
2. In `mode="dense"` (the eval sweep's explicit request), the re-raised `ImportError` tells the human "install sentence-transformers" when the real fix is "resolve the tokenizers/transformers version conflict" — a misleading diagnostic that will send whoever debugs a failed sweep down the wrong path.

**Fix:** Narrow the except to genuinely distinguish "module not found" from "module import failed for another reason," e.g. inspect `exc.name` (Python 3.6+ sets `ImportError.name` to the failing module) and only swallow/rewrap when `exc.name in ("sentence_transformers", None)` at the top level, otherwise let the original exception (with its real root cause) propagate or re-raise it verbatim instead of substituting a canned message. At minimum, include `str(exc)` in the wrapped message so the real root cause isn't lost:
```python
except ImportError as exc:
    raise ImportError(
        "DenseRetriever requires the 'sentence-transformers' package "
        f"(root cause: {exc}). Install it with `pip install sentence-transformers` or "
        "`pip install 'arango-query-core[dense]'`."
    ) from exc
```

### WR-03: Production `/nl-translate` now silently loads a real few-shot index on every request, with no baseline for the actual production default and no way to disable it

**File:** `arango_sparql/service/routes/nl.py:112-117`, `arango_sparql/nl2sparql/pipeline.py:104-122`, `arango_sparql/nl2sparql/engine_adapter.py:183-202`
**Issue:** The production route builds `NlPipeline` with no `few_shot_k=`/`few_shot_index=` override, so it inherits `NlPipeline`'s defaults: `few_shot_k=3`, `few_shot_index=None`. Verified empirically:
```python
adapter = SparqlAdapter(resolver=SchemaResolver.from_turtle(""))
idx = adapter.few_shot_index()
# -> FewShotIndex wrapping a real BM25Retriever, 23 examples loaded from
#    tests/nl2sparql/eval/fewshot_bank.yml
```
Because `SparqlAdapter.few_shot_index()`'s fallback (`cached_few_shot_index(_FEWSHOT_BANK_PATH, "auto")`) fires whenever no explicit index is injected, **every production translation request now silently reads `fewshot_bank.yml` from disk on first use, builds a real retriever (BM25 or, if `.[dense]` is installed, a `SentenceTransformer`), and injects up to 3 bank examples into the LLM prompt** — a material, silent change to what gets sent to the LLM in production, with:
- no environment variable or route parameter to opt out (unlike `NL2SPARQL_TIMEOUT`, which was added specifically to give operators a knob),
- no corresponding entry in `baseline.json` measuring pass-rate/cost at the actual production default (`k=3`, `mode="auto"`) — the closest measured number is the sweep's `bm25` arm, but that was captured out-of-band under the ad hoc `phase07_dense_few_shot_sweep` key, not as a `configs["*"]` baseline reproducible via `run()`,
- a first-request latency/availability risk if `.[dense]` is installed without pre-warming: the first `/nl-translate` call after process start would synchronously construct `SentenceTransformer(model_id, revision=revision)`, which can trigger a Hugging Face Hub network call if the weights aren't already cached locally (`HF_HUB_OFFLINE` is a sweep-runbook convention, not something the production route sets).

**Fix:** At minimum, thread an explicit, documented opt-out (e.g. `NL2SPARQL_FEW_SHOT_K` env var defaulting to `3`, mirroring the `NL2SPARQL_TIMEOUT` pattern already established in this same phase) so operators can disable few-shot in production without a code change, and add a `configs["openai-gpt4o-mini-production-default"]`-style baseline entry (or equivalent doc note) that actually measures pass-rate/cost at the real default (`k=3`, `mode="auto"`) rather than only at the eval harness's explicit `bm25`/`dense`/`zero` arms.

### WR-04: `SparqlAdapter.few_shot_mode` constructor parameter is dead code — never exercised with a non-default value anywhere in `arango_sparql`

**File:** `arango_sparql/nl2sparql/engine_adapter.py:171,176,202`
**Issue:** `SparqlAdapter.__init__` accepts `few_shot_mode: str = "auto"` and `few_shot_index()` uses it in the `cached_few_shot_index(str(_FEWSHOT_BANK_PATH), self._few_shot_mode)` fallback call. However, `grep -rn "SparqlAdapter(" arango_sparql tests` shows every call site (`NlPipeline.run()`, `tests/nl2sparql/test_engine_adapter.py`, `tests/nl2sparql/test_fewshot_engine_prompt.py`) constructs `SparqlAdapter` without ever passing `few_shot_mode=`. `NlPipeline` itself has no `few_shot_mode` field and never threads one through to `SparqlAdapter`. The eval runner (`runner.py`) never uses this parameter either — it bypasses the seam entirely by pre-building the index itself and passing it in via `few_shot_index=`. As delivered, `_few_shot_mode` is always `"auto"` in every real code path, so the parameter exists but is unreachable/untested surface — either wire it through `NlPipeline` (so a future caller could force `mode="dense"`/`"bm25"` without an index injection) or remove it until there's a real caller.
**Fix:** Either (a) add a `few_shot_mode` field to `NlPipeline.__init__` and thread it to `SparqlAdapter(..., few_shot_mode=self.few_shot_mode)`, with a unit test asserting a non-default mode actually changes the built index's retriever type, or (b) drop the unused parameter and inline `"auto"` directly in the `cached_few_shot_index` call until a real caller needs it.

## Info

### IN-01: `is True`/`is False` identity comparisons in the paired-analysis helpers are fragile

**File:** `tests/nl2sparql/eval/runner.py:470-471`
**Issue:** `paired_mcnemar` computes `b`/`c` via `zero[name] is False and dense[name] is True` / `zero[name] is True and dense[name] is False`. This only works because every current producer of these dicts (`CaseResult.passed`, `BaselineConfig.cases` via pydantic `bool` coercion, JSON-decoded `baseline.json`) happens to yield genuine Python `bool` singletons. If a future caller passes e.g. a `numpy.bool_` (`np.True_ is True` is `False` in NumPy) or an int (`1 is True` is `False`), the flip counts would silently come out wrong (undercounting both `b` and `c`) with no error raised. Prefer plain truthiness: `not zero[name] and dense[name]` / `zero[name] and not dense[name]`.

### IN-02: gpt-5/gpt-5-mini pricing rows are explicitly unverified this session

**File:** `arango_sparql/nl2sparql/cost.py:9-17,40-42`
**Issue:** The module docstring already discloses that the gpt-5-family pricing rows "could not be re-verified live this session (openai.com's pricing page is behind a Cloudflare interactive challenge...)" and should be "spot-checked against the live page before the Task 4 credentialed sweep is treated as cost-authoritative." This is good self-disclosure, but nothing in the codebase enforces the spot-check actually happening before the numbers get used for real accounting (e.g. no `TODO`-gate or dated re-verification reminder tied to CI). Low severity since it's already flagged in-repo; consider a dated follow-up task so this doesn't silently go stale.

### IN-03: `bootstrap_paired_delta`'s percentile indices are off-by-a-fraction from the conventional definition

**File:** `tests/nl2sparql/eval/runner.py:522-523`
**Issue:** `lo_idx = max(0, int(0.025 * len(deltas)))` / `hi_idx = min(len(deltas) - 1, int(0.975 * len(deltas)))`. With the default `iters=10000`, this yields `lo_idx=250`, `hi_idx=9750` (0-indexed into a 10000-element sorted array) — a defensible percentile-bootstrap approximation, but not identical to more conventional definitions (e.g. `numpy.percentile`'s linear interpolation, or `hi_idx = ceil(0.975*n) - 1 = 9749`). The discrepancy is at most 1 index position out of 10000 (negligible for a reporting-only CI, not a hard statistical guarantee), so this is informational rather than a correctness bug — flagging so the README's reported CI bounds aren't over-interpreted as matching a specific canonical bootstrap-CI library's exact output.

### IN-04: `test_dense_baseline_companion_structural`'s D-04 assertions have never actually been exercised against real folded-in data

**File:** `tests/nl2sparql/eval/test_eval.py:150-181`, `tests/nl2sparql/eval/baseline.json`
**Issue:** The Task 4 sweep's actual results were folded into `baseline.json` under a bespoke top-level `phase07_dense_few_shot_sweep` key (with an explicit `_note_schema` explaining why — a documented, reasonable deviation given the sweep only captured aggregate pass-rates, not full per-case verdict dicts). Because no `configs["*-dense"]` entry exists, `test_dense_baseline_companion_structural`'s `dense_names` list is always empty and the test always takes the `pytest.skip(...)` branch. The D-04 provenance-field assertions (`embedding_model`, `embedding_revision`, `sentence_transformers_version` populated, `0.0 < pass_rate < 1.0`) this test was built to enforce have therefore never actually run against real data end-to-end. Not a bug in the test itself, but worth tracking so a future genuine `configs["*-dense"]` fold-in doesn't assume this gate has already been exercised.

---

_Reviewed: 2026-07-21T23:25:51Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_

# Deferred Items — Phase 07.1

Out-of-scope discoveries logged during plan execution (not fixed — see
executor scope-boundary rule: only auto-fix issues directly caused by the
current task's changes).

## 07.1-04: pre-existing `baseline.json`/`corpus.yml` drift (not caused by this plan)

- **Found during:** 07.1-04 Task 2 verification (`RUN_EVAL=1 pytest tests/nl2sparql -q`).
- **Symptom:** `tests/nl2sparql/test_engine_adapter.py::TestVerdictReproduction::test_engine_reproduces_baseline_verdicts`
  fails with `KeyError: 'neg-service-federation'`.
- **Root cause:** `tests/nl2sparql/eval/corpus.yml` gained a `neg-service-federation`
  case (07.1-03, `feat(07.1-03): author 3 drift-proof absent-schema-term refusal
  cases`) but `tests/nl2sparql/eval/baseline.json` was never regenerated to include
  a verdict for it.
- **Verified pre-existing:** confirmed present at the wave-1 merge base commit
  `06aa5da3cbfad46e288c99558d96f446d827538d` — `corpus.yml` at that commit already
  contains `neg-service-federation` while `baseline.json` does not. Neither file is
  in 07.1-04's `files_modified` scope (only `tests/nl2sparql/eval/vendored/qald9plus/**`).
- **Not fixed here:** out of scope per the scope-boundary rule (pre-existing failure
  in unrelated files, not caused by this plan's Task 1/2 changes).
- **Suggested owner:** whichever plan regenerates `baseline.json` next (07.1-03's own
  follow-up, or the phase-gate verification step) should re-run
  `run('scripted')`/`write_report` (or the equivalent fold-in script) so
  `baseline.json` carries a verdict for every `corpus.yml` case, including
  `neg-service-federation`.

## 07.1-05 (CK25 vendoring)

- **`tests/nl2sparql/test_engine_adapter.py::TestVerdictReproduction::test_engine_reproduces_baseline_verdicts`
  fails with `KeyError: 'neg-service-federation'`.** Same pre-existing drift as noted
  above under 07.1-04 — `neg-service-federation` is an existing case name in the root
  `tests/nl2sparql/eval/corpus.yml` (added by 07.1-03), and neither
  `test_engine_adapter.py` nor `tests/nl2sparql/eval/baseline.json` were touched by
  this plan (`git diff --stat <base> -- tests/nl2sparql/test_engine_adapter.py
  tests/nl2sparql/eval/baseline.json` is empty). Left unfixed — out of this plan's file
  scope (`tests/nl2sparql/eval/vendored/ck25/**` only).

> **Orchestrator note:** Both drift reports refer to the same root cause. Resolution is
> owned by **07.1-06 Task 2**, which regenerates `baseline.json` and explicitly folds in
> the Plan-03 refusal cases (per 07.1-06-PLAN.md). This entry closes once that lands green.
>
> **CLOSED (07.1-06):** `baseline.json`'s `scripted` entry now carries all 34 corpus
> cases (25 original + 9 refusal), and `test_engine_reproduces_baseline_verdicts` is
> confirmed green (`uv run pytest tests/nl2sparql/test_engine_adapter.py -q`).

## 07.1-06: pre-existing W3C corpus data absent in this environment (out of scope)

- **Found during:** 07.1-06 Task 2 full-suite verification
  (`pytest -q -m "not integration and not w3c and not eval"`).
- **Symptom:** `tests/cross/test_minus_optional_cross.py::test_minus_optional_matches_oxigraph[full_minuend]`
  and `[part_minuend]` fail with
  `FileNotFoundError: .../tests/w3c/data/sparql11-test-suite/negation/part-minuend.rq`.
- **Root cause:** `tests/w3c/data/` does not exist in this worktree/environment at all —
  the W3C DAWG test-suite corpus was never fetched (`scripts/fetch_w3c.sh` has not been
  run here). Confirmed unrelated to any file this plan touches (`configs.yml`,
  `baseline.json`, `README.md`, `runner.py`'s `_alpha_normalize`/`_skeleton`,
  `test_eval.py`) — `tests/w3c/test_coverage_gate.py` itself correctly `pytest.skip`s
  for the same reason (`w3c_corpus_root() is None`), confirming this is an environment
  data-fetch gap, not a code regression.
- **Not fixed here:** out of scope — fetching/vendoring the full W3C DAWG suite is
  unrelated to this phase's NL->SPARQL benchmark-adoption work, and the transpiler
  itself is untouched (`git diff --stat` over `arango_sparql/translate/` is empty for
  this plan).
- **Suggested owner:** whichever CI/dev-environment task is responsible for running
  `scripts/fetch_w3c.sh` before the full test suite in this sandbox.

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

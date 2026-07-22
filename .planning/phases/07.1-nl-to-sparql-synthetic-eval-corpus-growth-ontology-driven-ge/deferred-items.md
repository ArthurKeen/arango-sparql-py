# Deferred Items — Phase 07.1

Out-of-scope discoveries logged during plan execution (not fixed, per the
Scope Boundary rule: only auto-fix issues directly caused by the current
task's changes).

## 07.1-05 (CK25 vendoring)

- **`tests/nl2sparql/test_engine_adapter.py::TestVerdictReproduction::test_engine_reproduces_baseline_verdicts`
  fails with `KeyError: 'neg-service-federation'`.** Pre-existing, unrelated
  to CK25 vendoring — `neg-service-federation` is an existing case name in
  the root `tests/nl2sparql/eval/corpus.yml` (added by an earlier phase),
  and neither `test_engine_adapter.py` nor `tests/nl2sparql/eval/baseline.json`
  were touched by this plan (`git diff --stat <base> -- tests/nl2sparql/test_engine_adapter.py
  tests/nl2sparql/eval/baseline.json` is empty). Looks like `baseline.json`
  is missing a `neg-service-federation` entry that the engine-adapter
  verdict-reproduction test expects. Left unfixed — out of this plan's file
  scope (`tests/nl2sparql/eval/vendored/ck25/**` only).

---
phase: 6
slug: nl-sparql-eval-harness-seed-corpus
status: approved
nyquist_compliant: true
wave_0_complete: false
created: 2026-07-15
---

# Phase 6 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (markers declared in `pyproject.toml`) |
| **Config file** | `pyproject.toml` (`[tool.pytest.ini_options]`, `eval` marker line ~68) |
| **Quick run command** | `RUN_EVAL=1 pytest -m eval --tb=short -q` |
| **Full suite command** | `pytest -m "not integration" --tb=short -q` |
| **Estimated runtime** | ~10–30 seconds (scripted provider; no network) |

---

## Sampling Rate

- **After every task commit:** Run `RUN_EVAL=1 pytest -m eval -q`
- **After every plan wave:** Run `pytest -m "not integration" -q` (guards against transpiler/W3C regression)
- **Before `/gsd-verify-work`:** Full suite must be green AND W3C DAWG coverage ≥ 96.4%
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

*Every task delivering NL-EVAL-01/02 behavior maps to an automated `eval`-marked test or a Wave 0 fixture dependency. Derived from the 3-plan set.*

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 06-01-01 | 01 | 1 | NL-EVAL-02 | T-06-01 | `yaml.safe_load` on trusted-repo corpus | fixture | `python -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('tests/nl2sparql/eval/corpus.yml').read_text())"` | ❌ W0 | ⬜ pending |
| 06-01-02 | 01 | 1 | NL-EVAL-02 | T-06-02 | no secrets in checked-in configs | fixture | `python -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('tests/nl2sparql/eval/configs.yml').read_text())"` | ❌ W0 | ⬜ pending |
| 06-02-01 | 02 | 2 | NL-EVAL-01, NL-EVAL-02 | T-06-02 | scripted path makes no network call | unit | `RUN_EVAL=1 pytest -m eval -q` | ❌ W0 | ⬜ pending |
| 06-02-02 | 02 | 2 | NL-EVAL-01, NL-EVAL-02 | T-06-02 | baseline is a real gate (pass-rate < 1.0) | unit | `RUN_EVAL=1 pytest -m eval -q` | ❌ W0 | ⬜ pending |
| 06-03-01 | 03 | 3 | NL-EVAL-01 | T-06-03 | CI eval job runs key-free (scripted only) | ci | `.github/workflows/ci.yml` runs `RUN_EVAL=1 pytest -m eval` | ❌ W0 | ⬜ pending |
| 06-03-02 | 03 | 3 | NL-EVAL-01 | — | N/A | guard | W3C DAWG query-eval coverage ≥ 96.4% | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/nl2sparql/eval/test_eval.py` — eval-marked test exercising `run()` + `write_report()` with a `ScriptedLLMClient` (stubs for NL-EVAL-01)
- [ ] `tests/nl2sparql/eval/corpus.yml` — seed corpus fixture (NL-EVAL-02)
- [ ] `tests/nl2sparql/eval/configs.yml` — provider config fixture (NL-EVAL-02)
- [ ] `tests/nl2sparql/eval/baseline.json` — checked-in regression gate (NL-EVAL-02)

*pytest framework already installed; `eval` marker already declared. No framework install needed.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.* The scripted-provider eval path is fully deterministic and runs in CI; real-provider sweeps (gated behind `RUN_EVAL=1` with a live key) are out of scope for this phase's regression gate.

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (test_eval.py, corpus.yml, configs.yml, baseline.json)
- [x] No watch-mode flags
- [x] Feedback latency < 30s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved 2026-07-15

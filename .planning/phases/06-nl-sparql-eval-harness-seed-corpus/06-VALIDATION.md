---
phase: 6
slug: nl-sparql-eval-harness-seed-corpus
status: draft
nyquist_compliant: false
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

*Populated by the planner. Every task delivering NL-EVAL-01/02 behavior must map to an automated `eval`-marked test or a Wave 0 fixture dependency.*

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 6-01-01 | 01 | 1 | NL-EVAL-01 | — | N/A | unit | `RUN_EVAL=1 pytest -m eval -q` | ❌ W0 | ⬜ pending |

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

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (test_eval.py, corpus.yml, configs.yml, baseline.json)
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending

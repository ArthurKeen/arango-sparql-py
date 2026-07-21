---
phase: 07
slug: nl-sparql-dense-few-shot-retrieval
status: approved
nyquist_compliant: true
wave_0_complete: true
created: 2026-07-21
---

# Phase 07 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest >= 8.0.0 (both repos: `arango-sparql-py` and the sibling `~/Desktop/arango-query-core`) |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` — markers `integration`, `w3c`, `cross`, `eval` already defined; no new marker needed (dense/no-network tests run on the default fast path, the live sweep is human-gated) |
| **Quick run command** | `pytest -q -k "fewshot or dense or engine_adapter"` (fast, no-network subset; arango-query-core tests run in that repo via `cd ~/Desktop/arango-query-core && pytest tests/test_nl_fewshot.py -q`) |
| **Full suite command** | `RUN_EVAL=1 pytest -m eval -q` (existing scripted eval gate; the NL-FEW-02 lift sweep is a human-gated runbook step, NOT part of this suite) |
| **Estimated runtime** | ~15-30s quick subset; ~30-60s full scripted eval gate |

---

## Sampling Rate

- **After every task commit:** Run `pytest -q -k "fewshot or dense or engine_adapter"` (07-01 tasks: `cd ~/Desktop/arango-query-core && pytest tests/test_nl_fewshot.py -q`)
- **After every plan wave:** Run `RUN_EVAL=1 pytest -m eval -q` plus `RUN_EVAL=1 pytest tests/nl2sparql/eval/test_fewshot_bank_disjoint.py -q`
- **Before `/gsd-verify-work`:** Full suite green (`pytest -q -ra` + `RUN_EVAL=1 pytest -m eval -q` + `pytest -m w3c`)
- **Max feedback latency:** ~60 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 07-01-01 | 01 | 1 | NL-FEW-01 | T-07-01 | Model id + HF revision pinned; module imports with no torch (lazy import) | unit (structural) | `cd ~/Desktop/arango-query-core && python -c "import arango_query_core.nl.fewshot as f; assert hasattr(f,'DenseRetriever'); assert hasattr(f,'cached_few_shot_index'); assert 'retriever' in dir(f.FewShotIndex); print('ok')"` | ❌ (extend `fewshot.py`) | ⬜ pending |
| 07-01-02 | 01 | 1 | NL-FEW-01 | T-07-SC | `[nl]` stays torch-free; `[dense]` isolates the ML supply chain | unit | `cd ~/Desktop/arango-query-core && grep -q 'dense = ["sentence-transformers' pyproject.toml && python -c "from arango_query_core.nl import DenseRetriever, cached_few_shot_index; print('ok')"` | ❌ (edit `pyproject.toml`/`__init__.py`) | ⬜ pending |
| 07-01-03 | 01 | 1 | NL-FEW-01 | — | Ranking logic exercised via injectable fake encoder; no network/torch import | unit | `cd ~/Desktop/arango-query-core && python -m pytest tests/test_nl_fewshot.py -x -q` | ❌ (extend test file) | ⬜ pending |
| 07-02-01 | 02 | 1 | NL-FEW-01 | T-07-03 | Bank loaded via `yaml.safe_load`; every gold parseable (no malformed exemplar) | unit | `python -c "import yaml; d=yaml.safe_load(open('tests/nl2sparql/eval/fewshot_bank.yml')); ex=d['examples']; assert 15<=len(ex)<=26; assert all('question' in e and 'query' in e for e in ex); from arango_sparql.translate.parser import parse_sparql; [parse_sparql(e['query']) for e in ex]; print('parsed', len(ex))"` | ❌ (new `fewshot_bank.yml`) | ⬜ pending |
| 07-02-02 | 02 | 1 | NL-FEW-01 | T-07-05 | Three-way disjointness (text + canonical algebra + skeleton, so `:age 30`/`:age 40` collide) + cosine similarity ceiling (<0.95; skips cleanly w/o the dense stack) + ontology parity guard block train-on-test leakage, near-clones, and drift (B2) | unit (committed invariant, RUN_EVAL-gated) | `RUN_EVAL=1 python -m pytest tests/nl2sparql/eval/test_fewshot_bank_disjoint.py -x -q` | ❌ (new gate) | ⬜ pending |
| 07-03-01 | 03 | 2 | NL-FEW-01 | T-07-08 | Pin bumped + `uv lock && uv sync` (no `--extra dense`) — no torch pulled; symbols import | unit (structural) | `python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); ex=d['project']['optional-dependencies']; assert 'dense' in ex; assert not any('sentence-transformers' in s for s in ex['nl']); assert any('arango-query-core[dense]' in s for s in ex['dense']); print('ok')" && python -c "import arango_query_core.nl; from arango_query_core.nl import cached_few_shot_index, DenseRetriever; print('symbols-ok')"` | ✅ (edit `pyproject.toml`) | ⬜ pending |
| 07-03-02 | 03 | 2 | NL-FEW-01 | T-07-07 | Seam uses memoized `cached_few_shot_index` (one load/process); production `mode="auto"` degrades, never crashes | unit | `python -c "from arango_sparql.nl2sparql.engine_adapter import SparqlAdapter; from arango_sparql.translate.resolver import SchemaResolver; from arango_query_core.nl import FewShotIndex; a=SparqlAdapter(resolver=SchemaResolver.from_turtle('')); idx=a.few_shot_index(); assert idx is not None and isinstance(idx, FewShotIndex); print('ok')" && grep -c "few_shot_k=self.few_shot_k" arango_sparql/nl2sparql/pipeline.py` | ✅ (edit adapter/pipeline) | ⬜ pending |
| 07-03-03 | 03 | 2 | NL-FEW-01 | T-07-06 | Examples land only in the engine-built `## Examples`, never the standalone PromptBuilder (SC2) | unit/integration | `python -m pytest tests/nl2sparql/test_engine_adapter.py tests/nl2sparql/test_fewshot_engine_prompt.py -x -q` | ✅ (edit) / ❌ (new SC2 gate) | ⬜ pending |
| 07-04-01 | 04 | 3 | NL-FEW-02 | — | gpt-5-family omits unsupported `temperature` (no 400); gpt-4o-mini keeps it; cost rows non-zero | unit (no-network) | `python -m pytest tests/nl2sparql/test_client_reasoning_model.py -x -q && python -c "from arango_sparql.nl2sparql.cost import known_pricing; k=known_pricing(); assert ('openai','gpt-5') in k and ('openai','gpt-5-mini') in k; print('ok')"` | ❌ (new client test) | ⬜ pending |
| 07-04-02 | 04 | 3 | NL-FEW-02 | T-07-11 | Additive `few_shot:` config; `run()` signature byte-identical; D-06 dense `isinstance` guard prevents a mis-filed dense number; pure-Python (no-scipy) `paired_mcnemar`/`bootstrap_paired_delta` present for the B1 primary signal | unit (structural) | `python -c "import yaml,inspect; c=yaml.safe_load(open('tests/nl2sparql/eval/configs.yml'))['configs']; assert 'scripted' in c; dense=[k for k,v in c.items() if (v.get('few_shot') or {}).get('mode')=='dense']; assert dense; assert all(c[k]['few_shot']['k']<=3 for k in dense); from tests.nl2sparql.eval.runner import BaselineConfig, run, paired_mcnemar, bootstrap_paired_delta; f=BaselineConfig.model_fields; assert 'embedding_model' in f and 'embedding_revision' in f and 'sentence_transformers_version' in f; assert list(inspect.signature(run).parameters)==['config_name']; z={'a':False,'b':True,'c':True}; d={'a':True,'b':True,'c':False}; b,cc,p=paired_mcnemar(z,d); assert (b,cc)==(1,1) and 0.0<=p<=1.0; delta,lo,hi=bootstrap_paired_delta(z,d,iters=2000); assert lo<=delta<=hi; print('ok')"` | ✅ (edit configs/runner) | ⬜ pending |
| 07-04-03 | 04 | 3 | NL-FEW-02 | T-07-10 | Pinned revision + run-time-captured ST version recorded in dense baseline (D-04); structural test locks artifact shape | unit (no-network) | `python -m pytest tests/nl2sparql/eval/test_eval.py -x -q && grep -ci "dense" tests/nl2sparql/eval/README.md` | ✅ (edit README/test) | ⬜ pending |
| 07-04-04 | 04 | 3 | NL-FEW-02 | T-07-09 / T-07-SC / T-07-13 | Blocking-human package-legitimacy checkpoint before first install; key only in `NL2SPARQL_API_KEY`; W3C ≥ 96.4% ASSERTED (not merely run) | manual (gated live sweep — see below); SC4 gate is automated | `pytest tests/w3c/test_coverage_gate.py -q` (asserts QUERY_EVAL coverage ≥ 96.4%, off the xfail-tolerant `-m w3c` path — M4; wired into the `w3c-coverage` CI job). The lift measurement itself is the manual runbook. | ✅ (README §7 runbook + `tests/w3c/test_coverage_gate.py`) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements. pytest (>= 8.0.0) and the
`eval`/`w3c` markers already exist in both repos' `pyproject.toml`; every new test file
(`tests/test_nl_fewshot.py` extension, `fewshot_bank.yml`, `test_fewshot_bank_disjoint.py`,
`test_fewshot_engine_prompt.py`, `test_client_reasoning_model.py`, the `test_eval.py`
structural addition) is authored inside its own task, not as separate Wave 0 scaffolding.

Note: 07-03 Task 1 runs `uv lock && uv sync` (WITHOUT `--extra dense`) to rebuild the venv
against the bumped `arango-query-core` pin BEFORE 07-03 Tasks 2/3 run their bare
`python`/`pytest` verify commands — this is an in-task environment step, not a MISSING
test-infrastructure reference.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| The gated dense-few-shot lift sweep (3-arm × 3-model × N≥5) proving a positive, statistically-supported NL→SPARQL pass-rate lift | NL-FEW-02 | Requires a live OpenAI key, the first real `torch`/`sentence-transformers` install (blocking `[ASSUMED]` package-legitimacy checkpoint), a HF model download, and a MANUAL human-reviewed `baseline.json` fold-in — not automatable in CI (mirrors 06.2's live-baseline precedent) | 07-04 Task 4 (blocking human checkpoint) → follow README §7: (1) confirm `sentence-transformers`/`torch` on pypi.org then `uv sync --extra dense`; (2) live model-resolution check for the bare `gpt-5`/`gpt-5-mini` aliases; (3) pre-warm HF model, export `NL2SPARQL_API_KEY`, run each arm **N ≥ 5** — run each model's zero arm freshly IN THE SAME SESSION as its dense arm (M2); (4) **PRIMARY confirmatory bar (pre-registered, m1/B1): on the gpt-4o-mini anchor, compare dense vs the freshly-run zero arm PAIRED over the same 25 cases via `runner.paired_mcnemar` — the lift PASSES iff McNemar p < 0.05, reported with `runner.bootstrap_paired_delta`'s 95% CI. Per-(model,arm) stddev is a SECONDARY check; the ~4-case minimum detectable effect is published so a null is not over-read. Other tiers and dense-vs-bm25 are EXPLORATORY (a null there is uninterpretable). Report BOTH the default-install (bm25 arm) and dense-install (dense arm) numbers (M3);** a null lift on gpt-5 is a documented ceiling-effect finding, not a failure; (5) fold dense/bm25 entries into `baseline.json` with D-04 provenance + the nearest-neighbor bank↔corpus similarity distribution; (6) the W3C ≥ 96.4% floor is ASSERTED by `pytest tests/w3c/test_coverage_gate.py -q` (M4), not the xfail-tolerant `-m w3c` path. The "+21 F1" survey number is background motivation only, never the success bar (M1). |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies (07-04 Task 4 is a `checkpoint:human-verify` — Nyquist-exempt, listed under Manual-Only)
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (none — existing infrastructure covers all)
- [x] No watch-mode flags
- [x] Feedback latency < 60s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved 2026-07-21

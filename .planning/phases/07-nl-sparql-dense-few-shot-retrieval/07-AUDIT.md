# Phase 7 Plan Audit — Findings

**Audited:** 2026-07-21 · Method: three parallel adversarial auditors (external-facts/web, codebase-consistency, design & measurement-logic).

**Bottom line:** Facts and engineering are verified-sound. The measurement design behind NL-FEW-02 / SC3 has two validity holes: the noise bar is too *insensitive* (false-null risk) and the leakage gate is too *permissive* (inflatable-lift risk). Fixes below. Waves 1–2 are executable; the 07-04 sweep must be revised before it is run.

---

## Verified solid (no action)

- **External facts (all VERIFIED):** gpt-5 rejects non-default `temperature` → 400 (the 07-04 fix is necessary + correct); model-ids/deprecations accurate (`gpt-5.5`/`gpt-5.4-mini` successors, shutdown 2026-12-11, bare `gpt-4o-mini` anchor safe); sentence-transformers pinning/offline correct, candidate hash `7dbbc90…` is a real non-tip commit; "+21 F1" traces to DFSL (arXiv 2407.01409) but is in-distribution best-case (no gain under QALD-10 shift); package versions healthy (`rank_bm25` unmaintained but low-risk as ablation only).
- **Codebase (all CONFIRMED):** every cited signature/state (`few_shot_index()→None`, `few_shot_k=0`, `temperature=0.1` @ client.py:159), invented symbols (`DenseRetriever`, `cached_few_shot_index`, `.retriever`), and every verify-command import path resolve. Wave ordering + torch-absent `mode="auto"→BM25` degrade + additive-config/06.2 reproducibility all hold.

---

## BLOCKER-class (fix before the sweep / bank authoring)

### B1 — D-09 noise bar is statistically unsound & underpowered (→ 07-04)
`(dense_mean − zero_mean) > max within-arm spread`, N=3 on 25 cases. Range-of-3 is a poor noise estimate; binomial SE alone (~2.3 cases at base-rate 0.32/n=25) means the design can only detect a ≥4-case (~16pt) lift — a real 2–3 case win is undetectable. "max spread" scope (global vs per-model) is undefined and can flip the verdict.
**Fix:** arms run the SAME 25 cases → use **paired McNemar test + bootstrap CI on per-case zero→dense flips** as the primary signal; raise **N≥5**; define spread **per-(model,arm)** stddev; publish the ~4-case **minimum detectable effect** in the runbook so a null isn't over-read.

### B2 — Disjointness gate proves non-identity, not non-contamination (→ 07-02)
D-02 is equality-only (normalized text + exact canonical algebra) and 07-02 instructs authoring "with corpus.yml open to avoid collisions" → optimizes for near-clones (corpus `:age 30` → bank `:age 40` clears the gate; dense retrieval then hands the model the template). Lift measures bank↔test proximity, not generalization.
**Fix:** add a **similarity ceiling** to the gate (max embedding cosine + canonical-skeleton similarity below a threshold between any corpus question and its nearest bank item); author the bank from an **independent difficulty spec with corpus.yml CLOSED**, then run disjointness+similarity as an after-the-fact check; **report nearest-neighbor bank↔corpus similarity** alongside the lift.

---

## MAJOR

- **M1 (→ 07-04/ROADMAP framing):** "+21 F1" (thousand-question KGQA F1) can't be validated by binary exact-match over 25 cases. Drop F1 framing; state "≥X-case pass-rate lift on the 25-case corpus."
- **M2 (→ 07-04):** self-baselined fresh zero arm ≠ committed 06.2 baseline (0.32); bare alias may not reproduce 0.32 on sweep day. Make confirmatory test **dense vs freshly-run zero (paired, same session)**; dense-vs-0.32 is a secondary continuity check only.
- **M3 (→ 07-04/07-03):** production `mode="auto"` + torch-optional means default `.[nl]` installs run BM25/no-op, never dense. Scope headline claim to `.[dense]` deployments **and report the sweep's bm25 arm as the honest default-install number**.
- **M4 (→ 07-04):** SC4 "verify by `pytest -m w3c`" doesn't verify — no w3c CI workflow exists and the test `xfail`s unsupported constructs (green at any coverage). Add a test asserting coverage **≥0.964** via `analyze_coverage.py` in a real CI job. (Regression risk ~nil — transpiler untouched — but the gate must actually assert.)
- **M5 (→ 07-03):** pin is `git+https://…@<SHA>`; `uv lock` fetches from **origin**, not local `~/Desktop/arango-query-core`. Make "push 07-01's commit to origin + confirm fetchable" an explicit verified precondition of 07-03 Task 1.

---

## MINOR

- **m1:** multiple comparisons (3 models × arms) → pre-register **gpt-4o-mini dense-vs-zero (paired)** as THE confirmatory test; others exploratory (or correct).
- **m2:** `## Examples` is the Anthropic cache breakpoint; per-query dense examples defeat prompt-cache reuse in production (cost note; not a sweep issue).
- **m3:** dense-vs-bm25 underpowered at ~18–24 bank items — a null there is uninterpretable, not "dense doesn't help." State as exploratory.
- **doc-drift (no execution impact):** PATTERNS.md lists `tests/test_engine_adapter.py` (real path `tests/nl2sparql/test_engine_adapter.py`; the plan uses the right one); `references/` is a plain dir, not a symlink (CLAUDE.md premise stale — no violation); sibling `arango-query-core/pyproject.toml` advertises `ArthurKeen/…` repo URL vs the `arango-solutions/…` origin the pins use.

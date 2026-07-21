# Phase 7: NL→SPARQL dense few-shot retrieval - Context

**Gathered:** 2026-07-21
**Status:** Ready for planning

<domain>
## Phase Boundary

Add a **dense/embedding few-shot retriever** to the shared NL engine
(`arango_query_core.nl.fewshot`, engine-side so Cypher inherits it), wire it
through `SparqlAdapter.few_shot_index()`, and **prove a positive NL→SPARQL
pass-rate delta** over the Phase 06.2 live-model baseline via the Phase 6 eval
harness. ≤ 3 shots per query (rule-300). BM25 is the ablation/fallback, not the
primary. Requirements NL-FEW-01, NL-FEW-02.

**The wiring rail already exists** — this phase does NOT build it:
- `arango_query_core.nl.fewshot` ships `FewShotIndex`, a `Retriever` protocol,
  `BM25Retriever`, `_NoopRetriever`, and `from_corpus_files()`.
- `NLQueryEngine._system_prompt()` already calls `adapter.few_shot_index()` and
  renders the `## Examples` section when the index returns matches.
- Phase 7 = (a) add a `DenseRetriever` alongside `BM25Retriever` engine-side,
  (b) flip `SparqlAdapter.few_shot_index()` from `None` → a populated index and
  the pipeline's `few_shot_k=0` → `3`, (c) author the example bank, (d) run the
  gated lift-measurement sweep.

**Not in scope:** changing the deterministic transpiler (W3C ≥ 96.4% is
untouched anyway), the eval judge (canonical-algebra, from Phase 6), or the
harness runner contract (from 06.2). Growing the eval corpus further is a future
concern, not Phase 7 (see Deferred).

</domain>

<decisions>
## Implementation Decisions

### Example-bank source (leakage boundary)
- **D-01:** Retrieval examples come from a **separate curated few-shot bank**
  (question → gold-SPARQL pairs), authored fresh and **disjoint from the eval
  `corpus.yml`**. The eval corpus stays fully held-out — no train-on-test. The
  bank covers the same difficulty classes as the corpus (OPTIONAL, aggregation,
  property-path, multi-hop). This is the SC1 "curated corpus".
- **D-02:** Disjointness is enforced by a **committed CI test**, not convention.
  The test asserts `bank ∩ eval_corpus = ∅` measured **two ways**: (1) normalized
  question text, AND (2) **canonical algebra of the gold SPARQL** — so a
  paraphrased question or a re-spelled-but-equivalent gold cannot smuggle a test
  case into the retrieval pool. Fits the repo's existing gate style
  (gold-must-parse, headroom-invariant). Bank lives beside the eval corpus
  (`tests/nl2sparql/eval/`, e.g. a `fewshot/` subdir or `fewshot_bank.yml`).

### Embedding backend
- **D-03:** **Local sentence-transformers** (e.g. `all-MiniLM-L6-v2`), not API
  embeddings. Reproducible offline once cached; no per-query key/cost. Pulls
  torch — acceptable because it loads **only in the gated dense path**. CI's
  key-free scripted default never imports it (the no-network guarantee from
  Phase 6/06.2 is preserved unchanged).
- **D-04:** **Pin the embedding model name + HF revision/commit hash** and the
  sentence-transformers version. Record the model id + revision alongside
  `corpus_sha` in the dense baseline artifact (mirrors 06.2's `corpus_sha`
  provenance capture) so a re-run reproduces the same retrieval order.

### Fallback & packaging
- **D-05:** **Two-tier degradation**, keyed on explicitness of intent (mirrors
  the existing `BM25Retriever` precedent, where `__init__` raises but
  `from_corpus_files` catches-and-degrades):
  - **Explicit dense request** (`DenseRetriever(...)` directly, or
    `from_corpus_files(mode="dense")`) → **hard `ImportError`** with an install
    hint if sentence-transformers is absent. This is the path the eval sweep
    uses → measurement integrity: a missing dep can never silently downgrade a
    "dense" run to BM25/zero-shot.
  - **Auto/unspecified path** → graceful **dense → BM25 → no-op** chain, so
    library consumers (Cypher, production NL pipeline) never crash for lack of
    torch.
- **D-06:** **Belt-and-suspenders guard** — the dense eval sweep **asserts the
  active retriever is a `DenseRetriever`** before recording a dense baseline, so
  a wrong-mode number can never be filed as a dense lift.

### Lift-measurement design
- **D-07:** **3-arm comparison, self-baselining:** run the live config three
  ways — **zero-shot** (the baseline), **dense few-shot**, and **BM25 few-shot**
  (ablation). The claim: `dense > zero` (required lift, SC3) and ideally
  `dense > BM25` (shows dense specifically is the win, per the SOTA survey).
- **D-08:** **Multi-model matrix, spanning capability tiers** (OpenAI-only — the
  only key available):
  - `gpt-4o-mini` — **anchor**: the committed 06.2 baseline, proven headroom,
    literally satisfies "delta over the Phase 06.2 baseline".
  - `gpt-5-mini` — mid tier (likely retains headroom on the hard corpus).
  - `gpt-5` — flagship: the stronger model, and a deliberate **ceiling-effect
    stress test** — if it saturates zero-shot, that null-lift is a *finding*
    (model already maxed), not a phase failure. Tests the core question: does
    the dense lift survive as the base model strengthens?
  - Each model runs its own full 3-arm set (each model self-baselines via its
    own zero-shot arm).
- **D-09:** **Noise floor — a lift only counts if it beats the noise.** Run each
  arm **N = 3 times**; report mean pass-rate + run-to-run spread; require
  `(dense_mean − zero_mean) > max within-arm spread`. gpt-4o-mini is not
  bit-deterministic even at low temp (documented in the eval README), so a raw
  single-run delta is not trustworthy. ~3× calls — acceptable for a gated,
  manual/opt-in sweep (like 06.2's live baseline, never in CI).

### Claude's Discretion
- **Packaging shape of the dense dep** (D-05 owner asked me to decide): default
  to a **dedicated `[dense]` extra** in `arango-query-core` (keeps `[nl]`
  torch-free, matches the lazy-import fallback), pending a check of
  `arango-query-core`'s existing `pyproject.toml` extras layout and how
  `arango-sparql` pins it. Fold into `[nl]` only if the existing layout argues
  for it.
- **Bank size / per-class balance** and **retrieval unit-test fixtures** (how to
  test retrieval offline without the live LLM): left to research/planning to
  size against the corpus difficulty classes.
- **Similarity metric / normalization** (cosine, etc.): standard, planner's call.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase spec & requirements
- `.planning/ROADMAP.md` §"Phase 7: NL→SPARQL dense few-shot retrieval" — goal,
  4 success criteria, dependencies (06.1 + 06.2).
- `.planning/REQUIREMENTS.md` — NL-FEW-01 (dense retriever wired via
  `SparqlAdapter.few_shot_index()`, engine-side, ≤3 shots, BM25 as fallback) and
  NL-FEW-02 (measurable positive delta over the 06.2 live baseline).
- `.planning/BRIEF-nl-to-conceptual-sota.md` — the SOTA survey; dense retrieval
  is the #1 win (up to +21 F1, highest evidence-per-cost); BM25 is the ablation.
- `.planning/research/nl-to-sparql-sota.md` — supporting research.

### Shared engine (where the dense retriever lands)
- `~/Desktop/arango-query-core/arango_query_core/nl/fewshot.py` — `FewShotIndex`,
  `Retriever` protocol, `BM25Retriever` (lazy-import + hard-raise precedent),
  `_NoopRetriever`, `from_corpus_files()`, `format_prompt_section()` (renders
  `## Examples`). **The dense retriever extends this file.**
- `~/Desktop/arango-query-core/arango_query_core/nl/engine.py` — `NLQueryEngine`
  `_system_prompt()` few-shot path (`few_shot_k`, calls `adapter.few_shot_index()`).
- `~/Desktop/arango-query-core/arango_query_core/nl/seams.py` —
  `QueryLanguageAdapter.few_shot_index()` seam signature (`FewShotIndex | None`).
- `~/Desktop/arango-query-core/arango_query_core/nl/providers.py` — `## Examples`
  is the Anthropic cache breakpoint (`_ANTHROPIC_CACHE_BREAKPOINT`); mind prompt
  structure when injecting examples.
- `arango-query-core` `pyproject.toml` — extras layout (for the `[dense]` vs
  `[nl]` packaging decision, D-05 discretion).

### SPARQL repo wiring
- `arango_sparql/nl2sparql/engine_adapter.py` — `SparqlAdapter.few_shot_index()`
  (currently returns `None`; flip to a populated index) and the seam docstring
  table.
- `arango_sparql/nl2sparql/pipeline.py` — `NlPipeline.run()` sets `few_shot_k=0`
  (flip to `3`); builds `SparqlAdapter` with the pipeline's own resolver.
- `arango_sparql/nl2sparql/prompt.py` — `_FEWSHOT_LIMIT`, `_render_few_shot_section`
  (the standalone PromptBuilder path; examples must land in the **engine-built**
  prompt, not this one — SC2).

### Eval harness (measurement)
- `tests/nl2sparql/eval/corpus.yml` — the 25-case held-out eval set (the bank
  must be disjoint from this).
- `tests/nl2sparql/eval/configs.yml` — provider/judge configs; add
  `openai`-type entries for `gpt-5-mini` / `gpt-5` (confirm exact ids) + few-shot
  variants; `scripted` stays the CI default.
- `tests/nl2sparql/eval/runner.py` — `run()`, `write_report()`, provider factory,
  `BaselineConfig` (`model` / `temperature` / `corpus_sha`); hardcoded
  `temperature=0.1` — gpt-5 reasoning models may need a per-model param path.
- `tests/nl2sparql/eval/README.md` — live-baseline runbook (key-gated sweep via
  `NL2SPARQL_API_KEY`, `corpus_sha` capture, MANUAL human-reviewed fold-in,
  never auto-regenerated in CI); nondeterminism note.
- `baseline.json` + its live `openai-gpt4o-mini` companion — the reference floor
  the dense lift is measured against.

### Rules
- `.cursor/rules/300-nl2sparql.mdc` (rule-300) — ≤3 shots, LLM emits SPARQL only
  (never AQL), prompt construction, few-shot insertion contract.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `FewShotIndex` / `Retriever` protocol / `from_corpus_files()`
  (`arango_query_core.nl.fewshot`): the dense retriever is a new `Retriever`
  impl slotted into the existing index — no new plumbing.
- `BM25Retriever`: doubles as the **ablation arm** (already built) AND the model
  for the lazy-import + hard-`ImportError` pattern the dense retriever copies.
- `NLQueryEngine._system_prompt()` few-shot path: already renders `## Examples`
  when `few_shot_index()` returns matches — satisfies SC2 once the seam returns a
  populated index and `few_shot_k > 0`.
- Eval harness (`runner.py` / `configs.yml` / `BaselineConfig`): already
  model-agnostic (openai/openrouter/anthropic factory branches); a new model =
  a new config block, no runner surgery.

### Established Patterns
- **Lazy-import + two-tier degrade** (BM25): construct-time hard raise,
  `from_corpus_files` catch-and-degrade. Dense retriever mirrors it exactly (D-05).
- **Provenance capture** (06.2): `corpus_sha` + model/temperature in the baseline
  artifact. Dense baseline extends this with embedding model id + revision (D-04).
- **Gated-not-CI live sweep** (06.2): `RUN_EVAL=1` + `NL2SPARQL_API_KEY`, MANUAL
  human-reviewed fold-in, `scripted` stays the no-network CI default. The dense
  sweep follows the same discipline (D-03, D-09).
- **Committed invariant tests** (gold-must-parse, headroom-invariant): the
  bank-disjointness test (D-02) is authored in this style.

### Integration Points
- `SparqlAdapter.few_shot_index()` (`None` → populated `FewShotIndex`) and
  `NlPipeline.run()` (`few_shot_k=0` → `3`) — the two flips that turn the engine
  few-shot path on for SPARQL.
- New `DenseRetriever` in `arango_query_core.nl.fewshot` + a `[dense]` extra in
  its `pyproject.toml` (engine-side; Cypher inherits).
- New `configs.yml` entries + a dense baseline artifact under
  `tests/nl2sparql/eval/`.

</code_context>

<specifics>
## Specific Ideas

- The interesting research question the matrix is built to answer: **does dense
  few-shot's value shrink as the base model gets stronger?** Tier spread
  (mini → gpt-5-mini → gpt-5) is deliberate — smaller models are headroom
  insurance, the flagship is the ceiling stress test.
- "N runs, delta > spread" (D-09): the bar for a real lift is that it exceeds the
  observed run-to-run noise, not merely that mean improved.

</specifics>

<deferred>
## Deferred Ideas

- **Leave-one-out cross-check on the eval corpus** — considered as a second
  disjointness-proof signal (Area 1 "Both" option). Not adopted; the separate
  curated bank + the two-way disjointness test is sufficient. Could add later if
  a reviewer wants corroborating generalization evidence.
- **API embeddings (OpenAI text-embedding-*)** — considered for the backend;
  rejected in favor of a pinned local model for reproducibility/offline. Could
  become a pluggable alt-backend later if a consumer wants it.
- **Growing the eval corpus if a stronger model saturates it** — if `gpt-5`
  hits the ceiling zero-shot, the fix (a harder corpus) is a *new corpus phase*,
  not Phase 7. Phase 7 records the null-lift finding and stops there.
- **Pluggable `Embedder` seam** — considered (Area 2 "pluggable" option); default
  local ST is enough for now; a formal seam is a future engine refinement.

</deferred>

---

*Phase: 7-NL→SPARQL dense few-shot retrieval*
*Context gathered: 2026-07-21*

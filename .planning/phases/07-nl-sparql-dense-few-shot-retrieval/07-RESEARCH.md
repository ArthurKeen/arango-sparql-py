# Phase 7: NL→SPARQL dense few-shot retrieval - Research

**Researched:** 2026-07-21
**Domain:** Sentence-embedding few-shot retrieval, cross-repo (`arango-query-core` engine) NL pipeline wiring, LLM eval-harness lift measurement
**Confidence:** HIGH (code paths read directly, both repos); MEDIUM (OpenAI gpt-5-family API behavior, verified via web search of official/community docs, not Context7); LOW (bank sizing heuristics, which are this research's own recommendation, not an external source)

## Summary

Phase 7 is a small, well-bounded code change (one new `Retriever` implementation in a
sibling repo, two one-line flips in this repo, one new curated YAML file) wrapped around a
much larger **measurement** problem (a 3-arm × 3-model × 3-run gated sweep with a real
noise-floor bar). The wiring rail genuinely already exists — `FewShotIndex`,
`NLQueryEngine._system_prompt()`, and the `## Examples` render path all work today and
need zero changes to accept a new retriever. The real engineering risk is NOT the dense
retriever itself (a ~40-line class mirroring `BM25Retriever`'s already-established
lazy-import + hard-raise pattern) — it is three sharp edges discovered by reading the
actual code paths that will silently break the measurement if unaddressed:

1. **Model/index reload-per-request.** `SparqlAdapter` is constructed fresh on every
   `NlPipeline.run()` call (once per HTTP request in production, once per corpus case
   inside the eval harness's `run()` loop — 25 times per sweep arm). If `few_shot_index()`
   builds a fresh `FewShotIndex`/`DenseRetriever` on each call, sentence-transformers
   reloads its model and re-embeds the whole bank 25× per arm × 3 runs × 3 models = 225
   redundant model loads. This must be memoized at module/process scope, not per-adapter.
2. **`gpt-5`-family models reject the hardcoded `temperature=0.1` the client always
   sends.** `OpenAICompatibleClient.generate()` (`arango_sparql/nl2sparql/client.py`)
   unconditionally puts `"temperature": self.temperature` in the request body. OpenAI's
   `gpt-5`/`gpt-5-mini` reasoning-model family returns a 400 for any non-default
   temperature value — this will hard-fail every gpt-5-family arm in D-08's matrix unless
   patched.
3. **Wiring the 3-arm matrix into the eval harness necessarily touches `runner.py` /
   `configs.yml`'s schema** (a `few_shot: {mode, k}` config field must exist somewhere for
   `run(config_name)` to select zero/dense/BM25), which sits close to — but is not
   identical to — the "harness runner contract" CONTEXT.md marks out of scope. This
   research recommends a narrow, additive extension (new optional config keys, unchanged
   `run(config_name) -> Report` signature) and flags it for planner confirmation.

Also load-bearing but lower-risk: `gpt-4o-mini`'s **bare** model alias (not a dated
snapshot) is confirmed still live and unscheduled for retirement as of this research date
— the 06.2 anchor baseline is safe. The `gpt-5`/`gpt-5-mini` bare aliases likewise appear
unscheduled, but the broader `gpt-5` *family of dated snapshots* is mid-deprecation
(shutdown 2026-12-11) in favor of `gpt-5.4-mini`/`gpt-5.5` — this is timing-sensitive and
worth a final live check before the sweep runs (see Open Questions).

**Primary recommendation:** Land the `DenseRetriever` + `mode=` parameter in
`arango_query_core/nl/fewshot.py` (mirroring `BM25Retriever`'s lazy-import contract
exactly), memoize `FewShotIndex` construction behind a module-level cache keyed on
bank-file identity + embedding-model revision, extend `configs.yml`'s schema additively
with a `few_shot:` block, and patch `OpenAICompatibleClient` to omit `temperature` for
`gpt-5`-family models before attempting any part of the D-08 matrix.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Dense embedding + cosine ranking (`DenseRetriever`) | Shared NL Engine (`arango-query-core`) | — | Lives beside `BM25Retriever` in `nl/fewshot.py`; language-agnostic, benefits Cypher too (per BRIEF §4) |
| Few-shot index selection (`SparqlAdapter.few_shot_index()`) | Adapter (`arango_sparql/nl2sparql`) | Shared NL Engine (consumes via seam 2) | Adapter decides WHICH corpus/mode; engine only calls the seam |
| Prompt rendering of `## Examples` | Shared NL Engine (`NLQueryEngine._system_prompt`) | — | Already implemented; do not duplicate in `prompt.py`'s standalone `PromptBuilder._render_few_shot_section` (SC2 forbids it) |
| Example bank authorship + disjointness gate | Adapter / Eval harness (`tests/nl2sparql/eval/`) | — | Bank is SPARQL/ontology-specific curated data, not engine logic |
| 3-arm × 3-model sweep orchestration | Eval harness (`tests/nl2sparql/eval/runner.py` + a new gated script) | — | Measurement-only; never touches the transpiler or the engine's generate/validate/repair loop |
| Model-family-conditional request shaping (temperature/reasoning_effort) | Adapter (`arango_sparql/nl2sparql/client.py`) | — | `OpenAICompatibleClient` is SPARQL-repo-local HTTP glue, not engine code |
| Embedding model caching/offline guarantee | Environment / packaging (`pyproject.toml` extras, CI config) | — | Must not regress the no-network CI default (D-03) |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `sentence-transformers` | latest verified on PyPI: **5.6.0** [VERIFIED: PyPI registry, `pip index versions`]; API surface (`SentenceTransformer(model, revision=, local_files_only=, cache_folder=)`, `.encode(..., normalize_embeddings=True)`) [CITED: sbert.net official docs] | Loads the pinned sentence embedding model and encodes text to vectors | The de facto standard local-embedding library; wraps `transformers`+`torch`; ships `revision`/offline params D-04/D-03 need natively — no hand-rolled HF Hub client needed |
| `sentence-transformers/all-MiniLM-L6-v2` (HF model id) | Example commit `7dbbc90392e2f80f3d3c277d6e90027e55de9125` surfaced in search [ASSUMED — package/commit name discovered via WebSearch, not Context7 or a fetched official page; **verify the exact revision hash directly on huggingface.co before pinning**] | The embedding model D-03 names as the example | Small (~90MB), fast, widely used as the reference "cheap good-enough" sentence encoder; explicitly named in CONTEXT.md D-03 |
| `torch` | latest verified on PyPI: **2.13.0** [VERIFIED: PyPI registry] | Backend tensor runtime `sentence-transformers` requires | Pulled transitively by `sentence-transformers`; CPU wheel is sufficient (no GPU needed for a ≤30-example bank) |
| `rank_bm25` | `>=0.2.2` (already pinned, both repos) [VERIFIED: existing codebase — `arango_query_core/nl/fewshot.py` `BM25Retriever`, both `pyproject.toml` files] | BM25 ablation/fallback retriever | Already shipped; no change needed except the new `mode=` selector in `from_corpus_files` |
| `PyYAML` | `>=6.0.0` (already pinned) [VERIFIED: existing codebase] | Loads the bank YAML | Already a dependency of both repos' `nl` extra |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `huggingface_hub` | transitive via `sentence-transformers` (not a direct pin needed) [ASSUMED — standard transitive dependency, not independently verified this session] | Model download + `HF_HUB_OFFLINE` / `HF_HOME` cache control | Pulled automatically; only surface directly if the plan needs explicit `local_files_only=True` handling in `DenseRetriever.__init__` |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Local `sentence-transformers` | OpenAI/Cohere embedding API | Rejected by D-03 (reproducibility/offline; no per-query cost) — recorded here only as the documented alternative, not a live option |
| `all-MiniLM-L6-v2` | `all-mpnet-base-v2` (used by the SOTA-survey's DFSL paper, `.planning/research/nl-to-sparql-sota.md` §2) | Larger (~420MB vs ~90MB), slower, marginally higher retrieval quality; D-03 names MiniLM as "e.g." (not locked) — planner may swap if bank-size testing shows MiniLM underperforms, but must re-verify the revision hash and re-pin |
| Full `sentence-transformers` (torch-backed) | ONNX-runtime backend (`sentence-transformers[onnx]`) avoids the full torch install | Smaller install footprint, but adds a second inference backend to maintain; not worth the complexity for a ≤30-example bank — defer unless install size becomes an actual blocker |

**Installation** (arango-query-core's `pyproject.toml`, new `[dense]` extra — see Packaging Decision below):

```bash
uv pip install -e '.[dense]'   # arango-query-core repo
```

**Version verification:** the exact `sentence-transformers` version actually installed
should be captured at RUN TIME via `sentence_transformers.__version__` and written into
the dense baseline artifact (mirroring how `corpus_sha` is captured via `git log`, not
hardcoded into `pyproject.toml`) — this is more robust to registry drift than a hard pin,
and satisfies D-04's "record the... sentence-transformers version" requirement without
fighting the fast-moving upstream release cadence (5.6.0 today; training-data knowledge of
this library is likely to be stale within weeks).

## Packaging Decision (resolves D-05 discretion)

`arango-query-core/pyproject.toml` today:

```toml
[project.optional-dependencies]
owl = ["rdflib>=7.0"]
nl = ["requests>=2.31", "PyYAML>=6.0.0", "rank_bm25>=0.2.2"]
dev = ["pytest>=8.0.0", "ruff>=0.6.0", "mypy>=1.11", "arango-query-core[owl,nl]"]
```

The package's own docstring states the design intent explicitly: *"The core is
deliberately dependency-free... so a transpiler that only needs MappingBundle pays for
nothing else."* `rank_bm25` already lives in `nl`, not a separate extra — but `rank_bm25`
is a small pure-Python package (no compiled/GPU weight), while `sentence-transformers`
pulls `torch` (a multi-hundred-MB compiled dependency). This asymmetry is exactly what
D-05's default anticipates.

**Recommendation: add a dedicated `[dense]` extra**, matching CONTEXT.md's default:

```toml
dense = ["sentence-transformers>=3.0"]   # exact floor TBD at implementation time
```

`nl2sparql`'s `pyproject.toml` `nl` extra currently pins `arango-query-core` via a direct
git-commit reference:

```toml
"arango-query-core @ git+https://github.com/arango-solutions/arango-query-core.git@c5b6026c344cfa994c442181b797f5400919d70c"
```

Two consequences the planner must account for:
1. **This pin must be bumped** to a new commit once `DenseRetriever` lands in
   arango-query-core (Phase 7 is cross-repo — the dense code physically lives in a
   sibling repo whose current local HEAD (`c5b6026`) matches the pin exactly, confirming
   this repo is not yet ahead of what's installed).
2. Extras compose through git refs (`pip`/`uv` support `pkg[extra1,extra2] @ git+...`), so
   `arango-sparql-py`'s own `nl` extra can request
   `"arango-query-core[nl,dense] @ git+...@<new-sha>"` directly — no need for a
   `arango-sparql-py`-local `dense` extra unless the team wants installs that skip torch
   by default (recommended: make it opt-in for the SPARQL repo too, e.g. a `dense` extra
   here that adds `arango-query-core[dense]` on top of the existing `nl` extra, so a
   default `pip install .[nl]` stays torch-free and CI-light).

## Package Legitimacy Audit

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| `sentence-transformers` | PyPI | 6+ years (UKPLab/sbert.net origin) | Very high (tens of millions/month class) | github.com/huggingface/sentence-transformers | OK | Approved |
| `torch` | PyPI | 9+ years | Extremely high | github.com/pytorch/pytorch | OK | Approved |
| `rank_bm25` | PyPI | Already a shipped dependency | — | github.com/dorianbrown/rank_bm25 | OK | Approved (no change) |

`slopcheck scan --pkg pypi <name> --json` was run for `sentence-transformers`, `torch`,
`rank_bm25`, and `huggingface-hub`; all four returned `"status": "OK"`, `"flags": []`.
Per the package-name-provenance rule, the package **names** themselves were sourced from
training knowledge + WebSearch (not Context7, which was unavailable in this session — no
`ctx7` CLI on PATH), so despite the clean slopcheck + registry verification they are
tagged `[ASSUMED]` above rather than `[VERIFIED]`. This is a formality, not a real risk:
`sentence-transformers` and `torch` are among the most widely-known packages in the Python
ML ecosystem and match training knowledge exactly; a planner `checkpoint:human-verify`
before the first `pip install` is nonetheless cheap insurance given the rule's intent.

**Packages removed due to slopcheck `[SLOP]` verdict:** none.
**Packages flagged as suspicious `[SUS]`:** none.

## Architecture Patterns

### System Architecture Diagram

```
                         Production request path (/nl-translate)
┌──────────────────────────────────────────────────────────────────────────┐
│  NL question                                                             │
│      │                                                                   │
│      ▼                                                                   │
│  NlPipeline.run(nl)              ← constructed fresh per HTTP request    │
│      │  builds SparqlAdapter(resolver=..., ontology_ttl=...,             │
│      │                        few_shot_index=<MEMOIZED>, few_shot_k=3)   │
│      ▼                                                                   │
│  NLQueryEngine.generate(question)          [arango_query_core.nl.engine]│
│      │                                                                   │
│      ├─► adapter.grammar_prompt_section()  (seam 1 — PromptBuilder)     │
│      ├─► adapter.few_shot_index()          (seam 2 — THIS PHASE)  ──┐   │
│      │        returns a FewShotIndex wrapping a DenseRetriever      │   │
│      │        (or BM25Retriever / _NoopRetriever on graceful         │   │
│      │        degrade — see Fallback Chain below)                    │   │
│      │◄──────────────────────────────────────────────────────────────┘   │
│      │   index.format_prompt_section(question, k=3) → "## Examples"     │
│      │   (rendered by the ENGINE, never by prompt.py's PromptBuilder)   │
│      ▼                                                                   │
│  provider.generate(system, user)  → LLM completion                      │
│      │                                                                   │
│      ▼                                                                   │
│  adapter.validate(candidate)      (seam 3 — deterministic transpiler)   │
│      │  ok?──no──► adapter.repair_hint() → retry (seam 4, ≤ max_retries)│
│      ▼ yes                                                              │
│  adapter.guardrails(candidate)    (seam 5 — allow-all today)            │
│      ▼                                                                   │
│  final SPARQL → re-translate once → PipelineOutcome{sparql, aql, ...}   │
└──────────────────────────────────────────────────────────────────────────┘

                    Dense-retriever construction (module-scope, ONCE)
┌──────────────────────────────────────────────────────────────────────────┐
│  fewshot_bank.yml ──► FewShotIndex.from_corpus_files([bank], mode="dense")│
│         │                                                                │
│         ▼                                                                │
│  DenseRetriever.__init__(examples)                                       │
│      │  lazy `from sentence_transformers import SentenceTransformer`    │
│      │  (ImportError → hard-raise in explicit "dense" mode;             │
│      │   caught-and-degrade to BM25→noop in "auto" mode)                │
│      ▼                                                                  │
│  model = SentenceTransformer(MODEL_ID, revision=PINNED_SHA)             │
│  bank_embeddings = model.encode([q for q,_ in examples],                │
│                                  normalize_embeddings=True)  # cached    │
└──────────────────────────────────────────────────────────────────────────┘
                              (memoized — see Pitfall 1)

                    Gated lift-measurement sweep (opt-in, never CI)
┌──────────────────────────────────────────────────────────────────────────┐
│  for model in [gpt-4o-mini, gpt-5-mini, gpt-5]:                          │
│    for arm in [zero, dense, bm25]:                                       │
│      for run in 1..3:                                                   │
│        run(config_name)  →  Report(pass_rate, per-case verdicts)        │
│  compute mean/spread per (model, arm); assert dense_mean-zero_mean       │
│  > max within-arm spread (D-09); D-06 asserts active retriever is Dense │
│  before recording a "dense" number                                       │
└──────────────────────────────────────────────────────────────────────────┘
```

### Recommended Project Structure

```
arango-query-core/                          # sibling repo — edit for the retriever
├── arango_query_core/nl/
│   └── fewshot.py                          # ADD: DenseRetriever, mode= param on
│                                            #      from_corpus_files, public
│                                            #      FewShotIndex.retriever property (D-06)
└── tests/
    └── test_nl_fewshot.py                  # EXTEND: DenseRetriever unit tests
                                             #   (injectable-encoder pattern, no network)

arango_sparql/nl2sparql/
├── engine_adapter.py                        # EDIT: SparqlAdapter gains
                                             #   few_shot_index: FewShotIndex | None = None
├── pipeline.py                              # EDIT: NlPipeline gains few_shot_k=3 default
                                             #   + few_shot_index passthrough
└── client.py                                # EDIT: OpenAICompatibleClient — omit/adapt
                                             #   temperature for gpt-5-family models

tests/nl2sparql/eval/
├── fewshot_bank.yml                         # NEW: curated (question, gold SPARQL) bank
├── test_fewshot_bank_disjoint.py            # NEW: D-02 disjointness gate
├── configs.yml                               # EXTEND: few_shot: {mode, k} per config
├── runner.py                                 # EXTEND (additively): thread few_shot config
                                             #   through to NlPipeline; run() signature
                                             #   unchanged
├── test_engine_adapter.py                    # EDIT: test_few_shot_index_is_none →
                                             #   test_few_shot_index_returns_populated_index
└── (new, gated) lift_sweep.py or a documented # NEW: D-07/D-08/D-09 orchestration script
    python -c snippet in README.md, per the     #   (mirrors 06.2's manual-invocation style)
    06.2 precedent
```

### Pattern 1: Lazy-import + two-tier degrade (mirror `BM25Retriever` exactly)

**What:** Constructor does the lazy `import`; explicit-mode callers get a hard
`ImportError` with an install hint, auto-mode callers get caught-and-degraded.
**When to use:** Any new `Retriever` implementation added to `fewshot.py`.
**Example:**
```python
# Source: arango_query_core/nl/fewshot.py (existing BM25Retriever, read this session)
class DenseRetriever:
    def __init__(self, examples: list[tuple[str, str]], *, model_id: str, revision: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "DenseRetriever requires the 'sentence-transformers' package. "
                "Install it with `pip install sentence-transformers` or "
                "`pip install 'arango-query-core[dense]'`."
            ) from exc
        self._examples = list(examples)
        self._model = SentenceTransformer(model_id, revision=revision)
        questions = [q for q, _ in self._examples]
        self._bank_embeddings = self._model.encode(questions, normalize_embeddings=True) if questions else None

    def retrieve(self, question: str, k: int = 3) -> list[tuple[str, str]]:
        if not self._examples or self._bank_embeddings is None or k <= 0:
            return []
        q_emb = self._model.encode([question], normalize_embeddings=True)[0]
        scores = self._bank_embeddings @ q_emb  # cosine sim since both normalized
        ranked = sorted(range(len(self._examples)), key=lambda i: (-float(scores[i]), i))
        return [self._examples[i] for i in ranked[:k]]
```

```python
# from_corpus_files gains an explicit mode selector — additive, backward compatible
def from_corpus_files(cls, paths: list[Path], *, mode: str = "auto") -> FewShotIndex:
    examples = ...  # unchanged loading logic
    if mode == "dense":
        retriever = DenseRetriever(examples, model_id=..., revision=...)  # HARD raise
    elif mode == "bm25":
        try:
            retriever = BM25Retriever(examples)
        except ImportError:
            retriever = _NoopRetriever()
    else:  # "auto"
        try:
            retriever = DenseRetriever(examples, model_id=..., revision=...)
        except ImportError:
            try:
                retriever = BM25Retriever(examples)
            except ImportError:
                retriever = _NoopRetriever()
    return cls(retriever, examples=examples)
```

### Pattern 2: Module-scope memoization of the index (NEW — not in the existing codebase; required to avoid Pitfall 1)

**What:** Cache the constructed `FewShotIndex` (and therefore the loaded
`SentenceTransformer` model + bank embeddings) at module or process scope, keyed on bank
file path(s) + mode + embedding revision — never rebuild it inside `SparqlAdapter.__init__`
or `NlPipeline.__init__`, both of which are instantiated fresh per request/per eval case.
**When to use:** Any place `SparqlAdapter.few_shot_index()` is implemented.
**Example:**
```python
from functools import lru_cache

@lru_cache(maxsize=4)
def _cached_few_shot_index(bank_path: str, mode: str) -> "FewShotIndex":
    return FewShotIndex.from_corpus_files([Path(bank_path)], mode=mode)

class SparqlAdapter:
    def few_shot_index(self) -> FewShotIndex | None:
        if self._few_shot_index is not None:      # explicit injection (tests, eval sweep)
            return self._few_shot_index
        return _cached_few_shot_index(str(BANK_PATH), self._few_shot_mode)
```
`lru_cache` is sufficient here (the bank file and mode are small, finite key spaces); no
need for a more elaborate cache invalidation scheme since the bank only changes between
process restarts / test-suite reloads, and pytest test isolation can call
`_cached_few_shot_index.cache_clear()` in a fixture if a test needs a fresh index.

### Pattern 3: Committed invariant test (mirror `test_gold_transpilable.py` / headroom-invariant style) for D-02 disjointness

**What:** A CI-visible (or `RUN_EVAL`-gated, matching the eval-suite's existing marker
convention) test that loads both YAML files and asserts the two-way disjointness.
**Example:**
```python
# Source: pattern lifted from tests/nl2sparql/eval/test_gold_transpilable.py (read this session)
from tests.nl2sparql.eval.runner import _canonical, _load_corpus
import re

def _normalize_question(q: str) -> str:
    return re.sub(r"[^\w\s]", "", q.strip().lower())

def test_bank_disjoint_from_eval_corpus() -> None:
    corpus = _load_corpus()["cases"]
    bank = yaml.safe_load(BANK_PATH.read_text())["examples"]

    corpus_questions = {_normalize_question(c["nl"]) for c in corpus}
    bank_questions = {_normalize_question(e["question"]) for e in bank}
    overlap_q = corpus_questions & bank_questions
    assert not overlap_q, f"bank questions overlap eval corpus (normalized text): {overlap_q}"

    corpus_canon = {_canonical(c["expected"]) for c in corpus if not c.get("expect_refusal")}
    corpus_canon.discard(None)
    bank_canon = {_canonical(e["query"]) for e in bank}
    bank_canon.discard(None)
    overlap_c = corpus_canon & bank_canon
    assert not overlap_c, f"bank gold SPARQL overlaps eval corpus (canonical algebra): {overlap_c}"
```
Reuses `_canonical` directly from `runner.py` — no new judge logic needed, satisfying
"reusing the repo's existing canonical-algebra judge from Phase 6" from the phase brief.

### Anti-Patterns to Avoid

- **Populating `PromptBuilder.few_shot_examples`** (`arango_sparql/nl2sparql/prompt.py`,
  `_render_few_shot_section`, `_FEWSHOT_LIMIT=3`): this is dead/reserved code for the
  standalone pipeline that Phase 06.1 already re-pointed away from. SC2 explicitly
  requires examples to land in the **engine-built** prompt only. `SparqlAdapter.
  grammar_prompt_section()` calls `PromptBuilder(...).render_system()` with no
  `few_shot_examples` argument — leave it that way; do not wire the bank through this path.
- **Rebuilding the model per call.** See Pitfall 1 below — this is the single biggest risk
  to both correctness (latency budget) and cost (3 models × 3 arms × 3 runs × 25 cases).
- **Reaching into `FewShotIndex._retriever` (private) from a cross-package test** to
  satisfy D-06's belt-and-suspenders guard. Add a public read-only `.retriever` property
  to `FewShotIndex` in the same PR as `DenseRetriever` instead.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Sentence embedding + cosine similarity | A custom TF-IDF/embedding scheme, a hand-rolled HTTP client to an embedding API | `sentence-transformers.SentenceTransformer.encode(..., normalize_embeddings=True)` + `@` dot product | Normalized-embedding dot product IS cosine similarity; no need for a separate cosine function or `sklearn` |
| HF model download/caching/offline mode | A custom download-and-cache-to-disk routine | `sentence-transformers`'s built-in `cache_folder`/`local_files_only`/`revision` params (backed by `huggingface_hub`) | Already handles retries, cache dirs, `HF_HUB_OFFLINE` env var, revision pinning — reinventing this is pure risk for zero benefit |
| Canonical-SPARQL-equality for the disjointness gate | A second string-similarity or fuzzy-match heuristic | The existing `_canonical()` function in `tests/nl2sparql/eval/runner.py` | It is already alpha-equivalence-aware (proven by `test_judge.py`, read this session) — a second ad hoc equality check would diverge from the judge the eval harness trusts everywhere else |
| Per-model temperature/reasoning-effort branching | An `if model.startswith("gpt-5")` scattered across call sites | A single conditional in `OpenAICompatibleClient.generate()`'s body-construction (or a small `_is_reasoning_model(model)` helper) | Centralizes the one place that must know about the gpt-5 family's incompatible request shape |

**Key insight:** this phase's only genuinely novel code is ~40 lines (the `DenseRetriever`
class) and ~15 lines (the disjointness test). Everything else is either already built
(the engine seam, the judge) or a narrow, mechanical extension of an existing pattern
(BM25's lazy-import contract, the eval config-block convention). Resist the temptation to
design a new abstraction — mirror what's there.

## Common Pitfalls

### Pitfall 1: Model/index rebuilt on every `SparqlAdapter`/`NlPipeline` construction

**What goes wrong:** `runner.py`'s `run()` loop constructs a fresh `NlPipeline` (and
therefore a fresh `SparqlAdapter`) inside its `for case in corpus["cases"]:` loop — once
per one of 25 corpus cases. Production's `/nl-translate` route similarly constructs
`NlPipeline` fresh per HTTP request (per `pipeline.py`'s own docstring: "the resolver is
per-request"). If `few_shot_index()` builds a new `FewShotIndex`/`DenseRetriever` each
time, `SentenceTransformer(...)` reloads its weights and re-embeds the whole bank on every
single call — for the D-09 sweep that's 3 models × 3 arms × 3 runs × 25 cases = up to 675
redundant model loads, each costing real wall-clock seconds.
**Why it happens:** `SparqlAdapter.few_shot_index()` today (correctly) returns `None`
statelessly; the natural first instinct when populating it is to build the index inline
in the method body, which is fine for a `_NoopRetriever` (returns `[]` instantly) but
catastrophic for a model-backed retriever.
**How to avoid:** Memoize at module scope (Pattern 2 above) — build once, reuse across
every `SparqlAdapter` instance in the process.
**Warning signs:** the eval sweep taking minutes-to-hours longer than the token/latency
numbers alone would predict; repeated "Loading checkpoint shards" / model-load log lines
in a single sweep run.

### Pitfall 2: `gpt-5`/`gpt-5-mini` reject the hardcoded `temperature=0.1`

**What goes wrong:** `OpenAICompatibleClient.generate()` (`arango_sparql/nl2sparql/
client.py`, read this session) always sends `"temperature": self.temperature` in the
request body; `configs.yml`'s `_client_for()` factory never overrides it per-config. GPT-5
reasoning-family models return **HTTP 400** for any temperature value other than the
implicit default of `1` [MEDIUM confidence — web search of OpenAI community/docs, not
independently reproduced against a live endpoint this session]. Every gpt-5-mini/gpt-5 arm
in D-08's matrix will hard-fail at the first LLM call unless this is fixed.
**Why it happens:** the client was built and tested only against `gpt-4o-mini`, which
accepts arbitrary temperature.
**How to avoid:** add a small model-family check (e.g. `model.startswith(("gpt-5",
"o1", "o3", "o4"))`) that omits `temperature` from the request body for reasoning models
(or reads a `reasoning_effort` field from `configs.yml` instead). Extend `configs.yml`'s
provider block with an optional `temperature`/`reasoning_effort` key so this is
configuration, not another hardcoded branch.
**Warning signs:** every gpt-5-family case in the sweep failing with the SAME 400 error
before any translation logic runs — a clear "it's the request shape, not the model" signal.

### Pitfall 3: D-08's model ids may already be past their prime by the time this phase executes

**What goes wrong:** as of this research date (2026-07-21), OpenAI's *dated snapshots*
`gpt-5-2025-08-07` / `gpt-5-mini-2025-08-07` are mid-deprecation (announced 2026-06-11,
shutdown 2026-12-11), superseded by `gpt-5.5` / `gpt-5.4-mini` respectively [MEDIUM
confidence — WebFetch of OpenAI's deprecations page]. The **bare** aliases `gpt-5` and
`gpt-5-mini` (no date suffix) do not appear on that deprecation list and likely still
resolve to *some* current model, but which underlying snapshot they alias to may have
already shifted since D-08 was written, changing what "the flagship ceiling-effect stress
test" actually stress-tests.
**Why it happens:** the OpenAI model lineup moves faster than any single planning
session; D-08 named `gpt-5-mini`/`gpt-5` as of context-gathering time.
**How to avoid:** before running ANY part of the D-08 sweep, do a live, cheap check (e.g.
`GET /v1/models` or a 1-token completion) confirming both bare aliases resolve and noting
which dated snapshot they currently point to, and record that resolved id in the sweep's
provenance artifact (same discipline as `corpus_sha`). If either alias has been fully
retired, this is a **human decision point** (swap to `gpt-5.4-mini`/`gpt-5.5`), not
something an agent should silently substitute.
**Warning signs:** a 404 "model not found" instead of the temperature 400 above.

### Pitfall 4: `estimate_llm_cost_usd` has no pricing row for gpt-5/gpt-5-mini

**What goes wrong:** `arango_sparql/nl2sparql/cost.py`'s `_PRICING_PER_1K_TOKENS` table
(read this session) has no entries for any gpt-5-family model. `estimate_llm_cost_usd`
gracefully returns `0.0` for unknown `(provider, model)` pairs by design (per its own
docstring: "treat cost_usd=0.0 as 'unpriced' not 'free'") — so the sweep will run fine,
but every gpt-5-family `LLMCallRecord.cost_usd` will silently read `$0.00`.
**Why it happens:** the pricing table is manually maintained and hasn't been touched
since 2026-05-03 (per its own header comment).
**How to avoid:** add pricing rows for whichever exact model ids the sweep resolves to
(Pitfall 3) before running, so the sweep's cost accounting is meaningful — not required
for correctness of the pass-rate measurement itself, but needed if cost is reported
alongside the lift numbers.
**Warning signs:** a sweep report showing `$0.00` total cost for two of the three models.

### Pitfall 5: Extending `runner.py`/`configs.yml` for arm selection brushes the "out of scope" boundary

**What goes wrong:** CONTEXT.md's Phase Boundary marks "the harness runner contract (from
06.2)" as not-in-scope. But the 3-arm matrix (zero/dense/BM25) cannot be expressed through
`configs.yml`'s current schema (`provider.type`, `provider.model`, `judge`, `max_repairs`)
— there is no field controlling few-shot mode/k at all today. Some extension to
`runner.py`/`configs.yml` is unavoidable to satisfy NL-FEW-02's acceptance criterion
("eval report delta > 0 over the live baseline... via the Phase 6 harness").
**Why it happens:** 06.2 built the harness before few-shot existed as a concept; the
schema was never designed to select it.
**How to avoid:** interpret "runner contract" narrowly — as the `run(config_name) ->
Report` **call signature and judge semantics**, not "configs.yml is a frozen schema
forever." Add an **additive**, optional `few_shot: {mode: zero|dense|bm25, k: int}` block
to individual `configs.yml` entries and a correspondingly small, additive read in
`_client_for`/`run()` that threads it into `NlPipeline` construction. `run()`'s existing
zero-arg-besides-`config_name` signature, its `Report` return shape, and
`test_ci_gate_only_ever_runs_scripted`'s static guard (which only inspects which
CONFIG NAMES are invoked, not the schema) all stay intact. Flagged in Open Questions for
explicit planner/human confirmation given the CONTEXT.md wording.
**Warning signs:** none yet — this is a forward-looking flag, not an observed bug.

### Pitfall 6: `test_few_shot_index_is_none` will break the moment the seam is flipped

**What goes wrong:** `tests/nl2sparql/test_engine_adapter.py::TestSparqlAdapterSeams::
test_few_shot_index_is_none` (read this session, line 191-193) explicitly asserts
`adapter.few_shot_index() is None`. Once `SparqlAdapter.few_shot_index()` returns a
populated index, this test fails.
**Why it happens:** it was written in 06.1 specifically to lock in zero-shot
behavior-preservation; that lock must be deliberately released, not accidentally broken.
**How to avoid:** update this test in the same plan wave that flips the seam — replace it
with an assertion that `few_shot_index()` returns a `FewShotIndex` instance (and,
separately, that `TestVerdictReproduction.test_engine_reproduces_baseline_verdicts`
— which manually hardcodes `few_shot_k=0` in its own `NLQueryEngine` construction,
independent of `pipeline.py`'s default — continues to pass unmodified, since it does not
go through `NlPipeline`/`SparqlAdapter`'s new default at all).
**Warning signs:** this specific test failing is actually the CORRECT signal that the
flip landed; do not "fix" it by leaving `few_shot_index()` returning `None`.

## Code Examples

### Cosine-similarity retrieval without a hand-rolled distance function
```python
# Source: sbert.net official docs (CITED) — encode(..., normalize_embeddings=True)
# then plain dot product IS cosine similarity.
import numpy as np
scores = bank_embeddings @ query_embedding  # both L2-normalized
```

### Reusing the existing canonical judge for bank-disjointness (no new judge logic)
```python
# Source: tests/nl2sparql/eval/runner.py::_canonical (read this session, lines 288-293)
from tests.nl2sparql.eval.runner import _canonical
assert _canonical(bank_query) != _canonical(corpus_gold_query)  # alpha-equivalence-aware
```

### Model-family-conditional request shaping (the fix for Pitfall 2)
```python
# Illustrative — not present in the codebase; new code needed in client.py
_REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")

def _is_reasoning_model(model: str) -> bool:
    return model.lower().startswith(_REASONING_MODEL_PREFIXES)

# inside OpenAICompatibleClient.generate():
body: dict[str, Any] = {"model": self.model, "messages": messages}
if not _is_reasoning_model(self.model):
    body["temperature"] = self.temperature
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| Zero-shot NL→SPARQL prompt (today's `nl2sparql` state) | ≤3-shot dense-retrieval-augmented prompt | This phase | Per `.planning/BRIEF-nl-to-conceptual-sota.md` / `.planning/research/nl-to-sparql-sota.md` (both read this session): dense retrieval is the SOTA survey's #1-ranked win, up to **+21 F1** on LC-QuAD 2.0/QALD-9, evidence rated HIGH confidence in that survey; a corroborating data point in the same survey showed GPT-3.5 moving from 8% zero-shot to 45% execution accuracy with 10-shot examples |
| BM25 lexical retrieval as the only few-shot option | Dense retrieval primary, BM25 demoted to ablation/fallback | This phase (D-07) | Matches the SOTA survey's explicit ranking; BM25 remains valuable as the "does dense specifically matter" control arm |
| OpenAI `gpt-5`/`gpt-5-mini` dated 2025-08-07 snapshots | `gpt-5.5` / `gpt-5.4-mini` | Announced 2026-06-11, shutdown 2026-12-11 [MEDIUM — WebFetch, OpenAI docs] | Affects which exact model ids D-08's matrix should resolve to by the time the sweep actually runs (see Pitfall 3) |

**Deprecated/outdated:**
- Standalone `PromptBuilder._render_few_shot_section` / `_FEWSHOT_LIMIT` in `prompt.py`:
  reserved-but-dead code since Phase 06.1 re-pointed the pipeline onto `NLQueryEngine`.
  Do not resurrect it for this phase (SC2 forbids it explicitly).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | HF model id `sentence-transformers/all-MiniLM-L6-v2` and the specific commit hash `7dbbc90392e2f80f3d3c277d6e90027e55de9125` are the correct, current model + revision to pin | Standard Stack | Pinning a stale/wrong revision hash would make `SentenceTransformer(..., revision=...)` fail outright at load time — cheap to catch (immediate `ImportError`/`RepositoryNotFoundError`), but must be re-verified live against huggingface.co before committing the pin |
| A2 | `sentence-transformers>=3.0` is a reasonable floor version for the `[dense]` extra | Standard Stack / Packaging | If the floor is set too low, `revision=`/`local_files_only=` param behavior could differ from what's documented here; low risk since this session recommends capturing the *actual* installed version at run time rather than trusting the floor pin |
| A3 | `gpt-5`/`gpt-5-mini` reject non-default `temperature` with an HTTP 400 (not a silent ignore or a different error shape) | Common Pitfalls (Pitfall 2) | If the actual failure mode differs (e.g., a warning instead of a hard error, or a different param name), the recommended `OpenAICompatibleClient` fix would need adjusting — but the *need* for some model-family-conditional handling is well-corroborated across multiple independent sources found in this session |
| A4 | The bare aliases `gpt-5` and `gpt-5-mini` currently resolve to a live, working model (not yet fully retired) | Common Pitfalls (Pitfall 3) / Open Questions | If either alias 404s, D-08's matrix cannot run as literally specified and requires a human substitution decision before the sweep |
| A5 | `huggingface_hub` need not be pinned directly (transitive via `sentence-transformers` is sufficient) | Standard Stack (Supporting) | Low risk — if a specific `HF_HUB_OFFLINE` behavior requires a minimum `huggingface_hub` version, this would surface immediately as an `ImportError`/`TypeError` on first use, not silently |

## Open Questions (RESOLVED)

1. **Should `SparqlAdapter.few_shot_index()`'s PRODUCTION path request `mode="dense"`
   (hard-fail without torch) or `mode="auto"` (silently degrade)?**
   - RESOLVED: The PRODUCTION seam defaults to `mode="auto"` (graceful dense->BM25->no-op)
     per D-05 — wired in 07-03 Task 2 (`SparqlAdapter.few_shot_index` / `few_shot_mode="auto"`).
     The explicit `mode="dense"` hard-raise is reserved for the 07-04 eval sweep; the
     `.[dense]`-required-in-production caveat is documented in the `few_shot_index()` docstring
     (07-03 WARNING-3 note).
   - What we know: D-05 defines the two-tier chain at the `from_corpus_files()` API
     level; D-06 requires the EVAL SWEEP specifically to assert dense-mode via a hard
     construct-time check.
   - What's unclear: CONTEXT.md doesn't say which mode the **shipped SparqlAdapter**
     (used by real `/nl-translate` requests, not just the eval sweep) should request. If
     it requests `"auto"`, a deployment lacking the `[dense]` extra silently gets
     BM25/zero-shot in production — meaning the measured NL-FEW-02 lift wouldn't apply to
     that deployment, even though the code "has" few-shot wired.
   - Recommendation: default `SparqlAdapter` to `mode="dense"` matching the measured
     configuration exactly, and make the `[dense]` extra part of the service's REQUIRED
     install surface (not truly optional) if the measured lift is meant to apply in
     production — surface this explicitly to the user/planner as a decision, don't
     silently pick one.

2. **Does extending `configs.yml`'s schema with a `few_shot:` block violate the
   "harness runner contract... not in scope" boundary from CONTEXT.md?**
   - RESOLVED: No — 07-04 adds an ADDITIVE optional `few_shot: {mode, k}` config block plus a
     small additive read in `runner.py`; `run(config_name) -> Report` stays byte-identical and
     `scripted` stays the CI default, so the runner contract itself is unchanged (07-04 Task 2).
   - What we know: some extension is structurally unavoidable (Pitfall 5); the phase
     BRIEF explicitly praises the harness as "already model-agnostic... a new model = a
     new config block, no runner surgery" — implying config-block additions are
     sanctioned, but arm-selection is a new AXIS, not just a new model.
   - What's unclear: whether "runner contract" in CONTEXT.md meant the literal
     `run(config_name) -> Report` call signature (safe to keep additive changes under) or
     a broader "don't touch runner.py at all" intent.
   - Recommendation: keep `run()`'s signature and `Report`'s shape byte-identical; make
     the `few_shot:` config key optional (defaulting to today's zero-shot behavior when
     absent) so every existing config/test keeps working unchanged; confirm this
     interpretation with the user/planner before writing the plan.

3. **What exact model snapshot do the bare `gpt-5`/`gpt-5-mini` aliases resolve to as of
   the actual sweep-execution date** (which may be later than this research date)?
   - RESOLVED: Deferred to run time by design — the 07-04 gated sweep checkpoint (Task 4) runs a
     LIVE model-resolution check (`GET /v1/models` or a 1-token completion) BEFORE the sweep and
     records the resolved snapshot id in the D-04 provenance; a 404 is a human decision point,
     never a silent substitution.
   - What we know: as of 2026-07-21, dated 2025-08-07 snapshots are mid-deprecation
     (shutdown 2026-12-11); `gpt-5.4-mini`/`gpt-5.5` are the current named successors; the
     bare aliases were not found on the deprecation list.
   - What's unclear: OpenAI does not publicly document exactly which dated snapshot a
     bare alias currently points to, and this can change without notice.
   - Recommendation: the credentialed human running the sweep (mirroring 06.2's
     `checkpoint:human-action` precedent) should capture a `GET /v1/models` (or
     equivalent) response alongside the sweep, recording the resolved model id string
     into the provenance artifact — exactly as `corpus_sha` is captured today.

4. **Bank size / per-difficulty-class balance** (left to discretion by CONTEXT.md).
   - RESOLVED: Bank sized at ~18-24 examples (~3-5 per positive difficulty class) — authored in
     07-02 Task 1; the two-way disjointness + ontology-parity gate is 07-02 Task 2.
   - What we know: the eval corpus has 25 cases across ~6 pattern classes (basic BGP,
     OPTIONAL, aggregation, property-path/multi-hop, negatives). Only POSITIVE patterns
     belong in the bank (a retrieved "refusal" example has no gold query to demonstrate).
   - What's unclear: the exact number of examples needed per class for both BM25 and
     dense retrieval to have enough density to select good top-3 candidates; no
     benchmark in the SOTA survey measures this for an OWL/Turtle conceptual schema
     specifically (survey's own "Open questions" §2 flags domain-transfer uncertainty).
   - Recommendation (LOW confidence, this research's own heuristic, not externally
     sourced): author roughly 3-5 examples per non-refusal difficulty class (basic BGP,
     OPTIONAL, aggregation, property-path, multi-hop) — a bank of ~18-24 examples — large
     enough that `k=3` retrieval has real candidates to discriminate among, small enough
     to keep embedding cost and review burden low. Treat as a starting point to be
     revisited if the D-09 lift doesn't clear the noise floor.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `sentence-transformers` | `DenseRetriever` (explicit-mode + auto-mode dense path) | ✗ (not yet installed in either repo's env) | — | Explicit dense mode: none (hard `ImportError` by design, D-05). Auto mode: degrades to BM25 → noop |
| `torch` | Transitive via `sentence-transformers` | ✗ | — | Same as above (bundled with the extra) |
| `rank_bm25` | `BM25Retriever` (ablation arm) | ✓ (already a pinned dependency, confirmed in both `pyproject.toml` files) | `>=0.2.2` | — |
| `OPENAI_API_KEY`/`NL2SPARQL_API_KEY` | The live gpt-4o-mini/gpt-5-mini/gpt-5 sweep (D-07/D-08) | ✗ (must never be held by the agent — human-run, per 06.2's `checkpoint:human-action` precedent) | — | Scripted config stays the CI default; no fallback for the live sweep itself — it is inherently a human-gated step |
| Local HF model cache (`~/.cache/huggingface` or `HF_HOME`) | Offline reproducibility of the dense retriever (D-03) | Unknown at research time — must be pre-warmed once per machine that runs the gated dense path | — | First run downloads (~90MB for MiniLM); subsequent runs with `HF_HUB_OFFLINE=1` guarantee no network, satisfying D-03's offline-once-cached requirement |

**Missing dependencies with no fallback:**
- A real OpenAI API key for the live sweep — inherent to the phase's design (gated,
  human-run), not a gap to fix.

**Missing dependencies with fallback:**
- `sentence-transformers`/`torch` — auto-mode gracefully degrades; explicit-mode sweep
  requires them installed (by design, for measurement integrity).

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | `pytest>=8.0.0` (both repos) [VERIFIED: existing `pyproject.toml`, both repos] |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` — `arango-sparql-py` defines markers `integration`, `w3c`, `cross`, `eval`; no `dense`/`fewshot` marker exists yet |
| Quick run command | `uv run pytest -q -k "fewshot or dense"` (fast, no-network subset — see Wave 0 Gaps) |
| Full suite command | `RUN_EVAL=1 pytest -m eval -q` (existing eval marker; the new lift-sweep is a manual/gated script, NOT part of this suite — mirrors 06.2's own live-baseline precedent of a documented `python -c` invocation, not a pytest test) |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| NL-FEW-01 | `DenseRetriever` mirrors `BM25Retriever`'s lazy-import/hard-raise contract | unit | `pytest tests/test_nl_fewshot.py -k dense -x` (arango-query-core repo) | ❌ Wave 0 (extend existing file) |
| NL-FEW-01 | `SparqlAdapter.few_shot_index()` returns a populated `FewShotIndex`, not `None` | unit | `pytest tests/nl2sparql/test_engine_adapter.py -k few_shot -x` | ❌ Wave 0 (replace `test_few_shot_index_is_none`) |
| NL-FEW-01 | Retrieved examples appear in the engine-built `## Examples` section (not `prompt.py`'s) | unit/integration | `pytest tests/nl2sparql/test_pipeline.py -k few_shot -x` (or a new test module) | ❌ Wave 0 |
| NL-FEW-01 | Bank is disjoint from eval corpus (both normalized-text and canonical-algebra) | unit (committed invariant) | `pytest tests/nl2sparql/eval/test_fewshot_bank_disjoint.py -x` | ❌ Wave 0 |
| NL-FEW-01 | `DenseRetriever` retrieval quality (ranks a relevant example above an irrelevant one) via an injectable fake encoder | unit | `pytest tests/test_nl_fewshot.py -k dense_retrieval_ranks -x` | ❌ Wave 0 — see below on the injectable-encoder pattern |
| NL-FEW-02 | Dense few-shot run shows positive pass-rate delta over the 06.2 live baseline | manual/gated (not automatable in < 30s; requires live LLM calls) | documented `python -c` sweep invocation (mirrors 06.2 README precedent), `checkpoint:human-action` | ❌ Wave 0 — new README section + sweep script |
| (regression) | W3C DAWG coverage stays ≥ 96.4% | existing | `pytest -m w3c` | ✅ already exists |

### Sampling Rate

- **Per task commit:** the fast no-network subset — `pytest -k "fewshot or dense or engine_adapter" -q` — since real dense retrieval (model-backed) should NOT run in the default fast path (no network guarantee, D-03).
- **Per wave merge:** `RUN_EVAL=1 pytest -m eval -q` (existing gate) plus the new disjointness test plus a **manual, once-per-wave** dense-mode smoke check (construct `DenseRetriever` for real against the pre-warmed local cache, confirm it returns non-empty results) — this is the "requires local model cache" tier the offline-fixture question in Step 2's key-questions asks about.
- **Phase gate:** full existing suite green (`pytest -q -ra` + `RUN_EVAL=1 pytest -m eval -q` + `pytest -m w3c`) BEFORE the D-07/D-08/D-09 lift sweep is attempted; the lift sweep itself is the phase's OWN success signal (NL-FEW-02), gated and human-run, not part of the automated test suite.

### Wave 0 Gaps

- [ ] `tests/test_nl_fewshot.py` (arango-query-core) — needs `DenseRetriever` unit tests
      using an **injectable fake encoder** (a `Callable[[list[str]], np.ndarray]`-shaped
      stub passed into `DenseRetriever.__init__` in test-only code, or a
      `monkeypatch`-substituted `SentenceTransformer`) so retrieval-ranking LOGIC is
      tested on the fast, no-network CI path without requiring torch/model download at
      all — mirrors how `BM25Retriever`'s tests use the real `rank_bm25` (a pure-Python,
      no-network dependency) but the equivalent isn't possible for a model-backed
      retriever, so dependency injection is the substitute.
- [ ] `tests/nl2sparql/eval/fewshot_bank.yml` — the curated bank itself (does not exist).
- [ ] `tests/nl2sparql/eval/test_fewshot_bank_disjoint.py` — the D-02 gate (does not exist).
- [ ] A `dense`/`fewshot` pytest marker (or reuse `eval`) in both repos' `pyproject.toml`
      if the plan wants a distinct opt-in gate for tests that DO touch the real model
      (separate from the existing key-gated `eval` marker, since dense tests are
      network/cache-gated, not credentials-gated).
- [ ] Framework install: no new test framework needed — `pytest` + existing fixtures
      cover this; only new test FILES are the gap.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|----------------|---------|-------------------|
| V2 Authentication | No | Phase touches no auth surface |
| V3 Session Management | No | Phase touches no session surface |
| V4 Access Control | No | Phase touches no access-control surface |
| V5 Input Validation | Yes (narrow) | The bank YAML is loaded via `yaml.safe_load` (already the established pattern for `corpus.yml`/`configs.yml` — never raw `yaml.load`); no new validation surface beyond what `CorpusCase`-style pydantic gating already does for the eval corpus |
| V6 Cryptography | No | No new crypto surface; embedding vectors are not secrets |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|----------------------|
| A malicious/malformed bank entry smuggling an unparseable or adversarial SPARQL string into the few-shot prompt | Tampering | The bank is checked-in trusted repo data (same trust tier as `corpus.yml`), loaded via `yaml.safe_load` only — never accept a bank file from an untrusted/runtime source |
| Leaking a provider API key via a raw prompt/completion accidentally committed | Information Disclosure | Already covered by the existing README.md discipline ("NEVER commit raw prompts/completions... They stay in gitignored `reports/`") — this phase adds no new leak surface, but the new gated sweep script must follow the identical discipline |
| A `DenseRetriever` silently downloading a DIFFERENT (unpinned) model revision than intended, changing retrieval behavior undetectably between runs | Tampering (supply-chain adjacent) | D-04's pinned revision hash + version capture directly mitigates this — verify the pin matches the intended commit on huggingface.co before shipping |

No new STRIDE row is required in the repo's `docs/architecture/PRD.md` §8.6 threat matrix
(the transpiler/HTTP-surface threat model is untouched by this phase); if the planner
judges the bank-YAML-trust-boundary point above warrants a dedicated test, it can extend
`tests/security/test_*.py`'s existing pattern rather than introduce a new file.

## Sources

### Primary (HIGH confidence)
- `~/Desktop/arango-query-core/arango_query_core/nl/{fewshot,engine,seams,providers}.py` — read in full this session
- `~/Desktop/arango-query-core/pyproject.toml` — read in full this session
- `~/Desktop/arango-query-core/tests/test_nl_fewshot.py`, `tests/test_nl_engine.py` — read this session (test patterns)
- `arango_sparql/nl2sparql/{engine_adapter,pipeline,prompt,client,cost}.py` — read in full this session
- `arango_sparql/pyproject.toml`, `.cursor/rules/300-nl2sparql.mdc` — read this session
- `tests/nl2sparql/eval/{runner,configs.yml,README.md,baseline.json,corpus.yml,test_gold_transpilable.py,test_eval.py,test_judge.py}` — read this session
- `tests/nl2sparql/test_engine_adapter.py` — read this session (identified Pitfall 6)
- `.planning/{CONTEXT.md phases/07-*, REQUIREMENTS.md, ROADMAP.md, STATE.md, BRIEF-nl-to-conceptual-sota.md, research/nl-to-sparql-sota.md}` — read in full this session
- `pip index versions sentence-transformers / torch / rank_bm25` — run this session, direct registry query
- `slopcheck scan --pkg pypi <name> --json` — run this session for sentence-transformers, torch, rank_bm25, huggingface-hub — all `OK`

### Secondary (MEDIUM confidence)
- sbert.net official docs (`SentenceTransformer` class reference: `revision`, `local_files_only`, `cache_folder` params) — via WebSearch, official domain
- OpenAI deprecations page (`developers.openai.com/api/docs/deprecations`) — via WebFetch, confirming gpt-4o-mini bare-alias safety and gpt-5-family dated-snapshot deprecation timeline
- OpenAI Community forum + third-party docs on gpt-5 temperature/reasoning_effort incompatibility — via WebSearch, cross-referenced across `community.openai.com`, `learn.microsoft.com` (Azure OpenAI reasoning models doc), and a third-party bug report (`getzep/graphiti` issue #878) independently describing the same 400 error

### Tertiary (LOW confidence)
- Exact HF commit hash for `all-MiniLM-L6-v2` (`7dbbc90392e2f80f3d3c277d6e90027e55de9125`) — surfaced via WebSearch snippet only, NOT independently confirmed by fetching huggingface.co directly; **must be re-verified before pinning** (flagged as Assumption A1)
- Bank sizing heuristic (~3-5 examples per difficulty class) — this research's own reasoning, no external benchmark found for an OWL/Turtle conceptual-schema bank specifically

## Metadata

**Confidence breakdown:**
- Standard stack: MEDIUM — the sentence-transformers API surface is well-documented and stable, but the exact model revision hash needs live re-verification; package legitimacy is HIGH (slopcheck + registry clean)
- Architecture: HIGH — every pattern recommendation is grounded in code actually read this session (BM25's lazy-import contract, the engine's seam-call sites, the eval runner's per-case construction loop)
- Pitfalls: HIGH for Pitfalls 1, 5, 6 (directly observed in code); MEDIUM for Pitfalls 2, 3, 4 (corroborated by multiple independent external sources but not reproduced against a live API this session)

**Research date:** 2026-07-21
**Valid until:** 7 days for anything OpenAI-model-lineup-related (Pitfall 3, Open Question 3 — this space is moving fast, per this session's own findings); 30 days for the architecture/pitfall findings grounded in the (stable, slow-moving) codebase itself

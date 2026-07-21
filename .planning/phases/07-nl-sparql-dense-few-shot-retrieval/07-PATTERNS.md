# Phase 7: NL→SPARQL dense few-shot retrieval - Pattern Map

**Mapped:** 2026-07-21
**Files analyzed:** 11 (new + modified, across both repos)
**Analogs found:** 11 / 11

## File Classification

| New/Modified File | Repo | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|---|
| `arango_query_core/nl/fewshot.py` — add `DenseRetriever`, `mode=` param, `.retriever` property | arango-query-core | service (retriever) | transform (embed→rank) | `BM25Retriever` in the SAME file | exact (same file, same protocol) |
| `arango_query_core/nl/fewshot.py` — module-scope memoization helper | arango-query-core | utility | CRUD (cache) | NEW pattern (no existing analog); nearest precedent is `from_corpus_files` itself as the thing being cached | none — build fresh, follow research Pattern 2 |
| `arango_query_core/pyproject.toml` — `[dense]` extra | arango-query-core | config | — | `[nl]` / `[owl]` extras, same file | exact |
| `arango_sparql/nl2sparql/engine_adapter.py` — `SparqlAdapter.few_shot_index()` flip | arango-sparql-py | controller/adapter (seam impl) | request-response | `SparqlAdapter.validate()` / `.guardrails()` (same class, same file) | exact |
| `arango_sparql/nl2sparql/pipeline.py` — `NlPipeline.run()` `few_shot_k=0`→`3` + memoized-index wiring | arango-sparql-py | service (orchestrator) | request-response | same method, same file (one-line-ish flip in situ) | exact |
| `arango_sparql/nl2sparql/client.py` — `OpenAICompatibleClient.generate()` reasoning-model temperature guard | arango-sparql-py | service (HTTP client) | request-response | `OpenAICompatibleClient.generate()` itself (in-place conditional add) | exact |
| `arango_sparql/nl2sparql/cost.py` — pricing rows for gpt-5-family | arango-sparql-py | config/utility | transform | existing `_PRICING_PER_1K_TOKENS` table, same file | exact |
| `tests/nl2sparql/eval/fewshot_bank.yml` | arango-sparql-py | fixture/config (curated data) | batch | `tests/nl2sparql/eval/corpus.yml` | exact (same YAML shape family) |
| `tests/nl2sparql/eval/test_fewshot_bank_disjoint.py` (D-02) | arango-sparql-py | test (committed invariant) | batch | `tests/nl2sparql/eval/test_gold_transpilable.py` | exact |
| `tests/nl2sparql/eval/configs.yml` — new model entries + `few_shot:` block | arango-sparql-py | config | request-response | existing `configs.yml` (same file, additive entries) | exact |
| `tests/nl2sparql/eval/runner.py` — additive `few_shot` config read + `BaselineConfig` extension | arango-sparql-py | service (harness) | batch | same file's existing `_client_for` / `BaselineConfig` | exact |
| `tests/test_engine_adapter.py::test_few_shot_index_is_none` → replace | arango-sparql-py | test | request-response | same test class (`TestSparqlAdapterSeams`), same file | exact |
| `arango_query_core/tests/test_nl_fewshot.py` — extend with `DenseRetriever` unit tests | arango-query-core | test | transform | same file's existing BM25/no-op tests | exact |
| `tests/nl2sparql/eval/baseline.json` — dense/BM25 baseline artifact entries | arango-sparql-py | config (provenance artifact) | batch | existing `openai-gpt4o-mini` entry, same file | exact |

## Pattern Assignments

### `arango_query_core/nl/fewshot.py` — `DenseRetriever` (new class)

**Analog:** `BM25Retriever` in the same file (lines 47-87), plus `FewShotIndex.from_corpus_files` (lines 123-173), plus `Retriever` protocol (lines 35-39).

**Imports pattern** (module header, lines 22-29 — no top-level ML import):
```python
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)
```
`DenseRetriever` follows the identical top-of-module posture: no `sentence_transformers`/`numpy` import at module scope. The lazy import happens **inside `__init__`**, exactly like `rank_bm25` is imported inside `BM25Retriever.__init__` (never at module level) — this is what keeps `fewshot.py` import-safe without the `[dense]` extra installed.

**Lazy-import + hard-raise pattern** (copy exactly, lines 56-64):
```python
def __init__(self, examples: list[tuple[str, str]]) -> None:
    try:
        from rank_bm25 import BM25Okapi
    except ImportError as exc:
        raise ImportError(
            "BM25Retriever requires the 'rank_bm25' package. "
            "Install it with `pip install rank_bm25>=0.2.2` or "
            "`pip install 'arango-query-core[nl]'`."
        ) from exc
```
`DenseRetriever.__init__` mirrors this verbatim, substituting `sentence_transformers` / `SentenceTransformer` and the `[dense]` extra name in the install-hint string. This exact string shape (package name, `pip install <pkg>` OR `pip install 'arango-query-core[extra]'`) is the established convention — do not invent new wording.

**Core retrieve() pattern** (lines 71-87 — the ranking shape to mirror, cosine dot-product in place of BM25 scores):
```python
def retrieve(self, question: str, k: int = 3) -> list[tuple[str, str]]:
    if not self._examples or self._bm25 is None or k <= 0:
        return []
    tokens = _tokenize(question)
    if not tokens:
        return []
    scores = self._bm25.get_scores(tokens)
    ranked = sorted(
        range(len(self._examples)),
        key=lambda i: (-float(scores[i]), i),
    )
    out: list[tuple[str, str]] = []
    for idx in ranked[:k]:
        if float(scores[idx]) <= 0.0:
            continue
        out.append(self._examples[idx])
    return out
```
Copy the guard clauses (`not self._examples`, `k <= 0`), the `sorted(range(len(...)), key=lambda i: (-score, i))` tie-break-by-index idiom (deterministic ordering — critical for reproducible eval sweeps per D-04/D-09), and the `list[tuple[str, str]]` return shape. Cosine similarity via normalized-embedding dot product replaces BM25's `get_scores`; there is no natural "non-positive score" floor filter for cosine (BM25's `<= 0.0` skip has no direct cosine analog — omit that specific filter or clamp only on explicit planner decision, since a negative cosine score is still a meaningful ranking signal unlike BM25's IDF floor).

**`from_corpus_files` additive `mode=` parameter** (lines 123-173, esp. 165-173 — the degrade chain to extend):
```python
if not examples:
    return cls(_NoopRetriever(), examples=[])

try:
    retriever: Retriever = BM25Retriever(examples)
except ImportError as exc:
    logger.info("rank_bm25 not installed; FewShotIndex degrades to no-op: %s", exc)
    retriever = _NoopRetriever()
return cls(retriever, examples=examples)
```
Extend this exact try/except-log-degrade shape into the 3-way `mode` dispatch (`"dense"` hard-raises, `"bm25"` catches-and-degrades to noop, `"auto"` tries dense→bm25→noop) per RESEARCH.md's Pattern 1 code sample. Keep the `logger.info(...)` call on every degrade step — this is the established observability convention in this file (also used at line 142 for the PyYAML-missing branch).

**Public `.retriever` property (D-06 belt-and-suspenders):** `FewShotIndex` currently has no public accessor for the wrapped retriever (only `.examples`, lines 118-121). Add a `@property def retriever(self) -> Retriever: return self._retriever` alongside the existing `.examples` property (same style — a one-line read-only accessor over a private attribute) so the eval sweep's `isinstance(index.retriever, DenseRetriever)` guard never reaches into `_retriever` directly.

---

### `arango_query_core/nl/fewshot.py` — module-scope memoization (NEW, no existing in-repo analog)

**Source:** RESEARCH.md Pattern 2 (Pitfall 1 fix) — `functools.lru_cache` keyed on bank path + mode.
```python
from functools import lru_cache

@lru_cache(maxsize=4)
def _cached_few_shot_index(bank_path: str, mode: str) -> "FewShotIndex":
    return FewShotIndex.from_corpus_files([Path(bank_path)], mode=mode)
```
No precedent exists in either repo for this exact shape; it is new infrastructure, not a mirrored pattern. Place it in `fewshot.py` (engine-side, per RESEARCH's Architectural Responsibility Map — the cache is a property of "how you build an index cheaply," which is engine concern, not adapter concern) so both `SparqlAdapter` and Cypher's future adapter share one cache. `SparqlAdapter.few_shot_index()` in `engine_adapter.py` calls this cached function rather than constructing `FewShotIndex` inline — see next section.

---

### `arango_sparql/nl2sparql/engine_adapter.py` — `SparqlAdapter.few_shot_index()` flip

**Analog:** the seam's OWN docstring table (lines 136-144) plus the sibling seam methods `validate()` (lines 171-179) and `guardrails()` (lines 192-194) in the same class, for the established "the docstring table documents what each seam maps to" convention.

**Current state (to flip), lines 167-169:**
```python
def few_shot_index(self) -> None:  # seam 2
    # Zero-shot for behavior-preservation; Phase 7 populates the corpus.
    return None
```

**Docstring table to update** (lines 136-144 — keep the same ASCII-table format, just change the `few_shot_index` row's "Maps to" cell from `` ``None`` (zero-shot; Phase 7 wires the corpus)`` to the populated-index description):
```python
==========================  ============================================
Seam                        Maps to
==========================  ============================================
grammar_prompt_section``  :class:`PromptBuilder`'s system turn
few_shot_index``          ``None`` (zero-shot; Phase 7 wires the corpus)
validate``                :func:`arango_sparql.api.translate`
repair_hint``              :func:`format_repair_context`
guardrails``               allow-all (no tenant/write-op checks yet)
==========================  ============================================
```

**Constructor pattern to extend** (lines 158-160 — how the class currently takes its two collaborators):
```python
def __init__(self, *, resolver: SchemaResolver, ontology_ttl: str = "") -> None:
    self.resolver = resolver
    self.ontology_ttl = ontology_ttl
```
Add a third constructor param (e.g. `few_shot_index: FewShotIndex | None = None`) following this exact keyword-only, default-valued style — matching how `ontology_ttl` already defaults to `""`. The method body becomes:
```python
def few_shot_index(self) -> FewShotIndex | None:  # seam 2
    if self._few_shot_index is not None:      # explicit injection (tests, eval sweep)
        return self._few_shot_index
    return _cached_few_shot_index(str(BANK_PATH), self._few_shot_mode)
```
(per RESEARCH Pattern 2) — explicit constructor injection takes precedence over the memoized module-scope default, exactly the shape needed so `test_engine_adapter.py` and the eval sweep can inject a fake/scripted index without touching the real bank file.

---

### `arango_sparql/nl2sparql/pipeline.py` — `NlPipeline.run()` flip

**Analog:** the same method, same file (lines 120-142) — this is an in-place parameter change, not a new-file port.

**Current state to flip** (lines 135-142):
```python
bridge = EngineProviderBridge(self.client)
adapter = SparqlAdapter(resolver=self.resolver, ontology_ttl=self.ontology_ttl)
engine = NLQueryEngine(
    provider=bridge,
    adapter=adapter,
    few_shot_k=0,
    max_retries=self.repair_loop.max_repairs,
)
```
Change `few_shot_k=0` → `few_shot_k=3` (rule-300's ≤3-shot cap — do not exceed 3). If `SparqlAdapter`'s constructor gains a `few_shot_index=` param (see previous section), thread it through here too, following the exact keyword-arg style already used for `resolver=`/`ontology_ttl=`.

---

### `arango_sparql/nl2sparql/client.py` — `OpenAICompatibleClient.generate()` reasoning-model guard (Pitfall 2 fix)

**Analog:** the method itself, same file (lines 154-177) — additive conditional, not a new class.

**Current state** (lines 159-163):
```python
body = {
    "model": self.model,
    "messages": messages,
    "temperature": self.temperature,
}
```

**Fix pattern** (from RESEARCH.md Code Examples, matches this file's style — module-level constant + small predicate function, same convention as `_QUERY_KEYS`/`_tokenize` in `fewshot.py`):
```python
_REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")

def _is_reasoning_model(model: str) -> bool:
    return model.lower().startswith(_REASONING_MODEL_PREFIXES)

# inside generate():
body: dict[str, Any] = {"model": self.model, "messages": messages}
if not _is_reasoning_model(self.model):
    body["temperature"] = self.temperature
```
Place `_REASONING_MODEL_PREFIXES`/`_is_reasoning_model` near the top of `client.py` (module-level, private, `_`-prefixed) — matches the file's existing convention of no other module-level helpers currently, but is consistent with `fewshot.py`'s `_tokenize`/`_QUERY_KEYS` pattern in the sibling repo. This is a narrowly-scoped, in-place edit to `OpenAICompatibleClient.generate()` only — `AnthropicClient.generate()` (lines 219-255) is untouched (Anthropic's temperature semantics are unaffected).

---

### `tests/nl2sparql/eval/fewshot_bank.yml` (new curated bank)

**Analog:** `tests/nl2sparql/eval/corpus.yml` (lines 1-60 read) — same YAML shape family, same trust-tier comment header.

**Header/shape pattern to copy** (lines 1-21):
```yaml
# Seed corpus for the NL -> SPARQL eval harness (tests/nl2sparql/eval/runner.py).
#
# ...
# This file is checked into VCS as trusted repo data — no credentials belong here.

ontology: |
  @prefix : <http://ex.org/> .
  ...

cases:
  - name: people-with-names
    nl: "List all people along with their names."
    expected: |
      PREFIX : <http://ex.org/>
      SELECT ?s ?n WHERE { ?s a :Person ; :name ?n . }
```
`fewshot_bank.yml` should use the SAME trusted-repo-data comment-header convention, and reuse `corpus.yml`'s `ontology:` Turtle block VERBATIM (same conceptual schema — D-01 requires the bank cover "the same difficulty classes as the corpus"; sharing the ontology is what makes that possible without a second schema to maintain). Entry shape per `arango_query_core.nl.fewshot.FewShotIndex.from_corpus_files`'s expected format (its own docstring, lines 126-136):
```yaml
version: 1
examples:
  - question: "Find a person by name"
    query: 'MATCH (p:Person {name: "Tom Hanks"}) RETURN p'
```
Use `question:`/`query:` (the canonical keys, not the legacy `cypher`/`sparql`/`aql` spellings) since this is a fresh SPARQL-only bank.

---

### `tests/nl2sparql/eval/test_fewshot_bank_disjoint.py` (D-02 gate)

**Analog:** `tests/nl2sparql/eval/test_gold_transpilable.py` (read in full, 75 lines) — the established "committed invariant test, RUN_EVAL-gated" style.

**Header/gating pattern to copy** (lines 1-39):
```python
"""Authoring guard: every non-refusal corpus gold must be JUDGEABLE.
...
Key-free / no-network: mirrors ``test_eval.py``'s ``pytest.mark.eval`` +
``RUN_EVAL`` skip idiom so this stays off the default fast path. It only ever
touches ``corpus.yml``, the resolver, ``_canonical``, and the deterministic
``translate`` API — never a live provider.
"""

from __future__ import annotations

import os

import pytest

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver
from tests.nl2sparql.eval.runner import _canonical, _load_corpus

_RUN_EVAL = os.getenv("RUN_EVAL", "").strip().lower() not in ("", "0", "false", "no")

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(not _RUN_EVAL, reason="set RUN_EVAL=1 to run the NL eval gate"),
]
```
Note: `test_fewshot_bank_disjoint.py` could reasonably run WITHOUT the `RUN_EVAL` gate (it's key-free, pure-YAML + `_canonical` comparison, cheap) — but matching the repo's existing `eval`-marker convention for anything touching `runner.py`/`corpus.yml` internals is the safer, more consistent choice per CONTEXT.md D-02's "fits the repo's existing gate style."

**Reuse `_canonical` directly — do not write a second judge** (RESEARCH.md Code Examples, grounded in `runner.py` lines 288-293):
```python
def _canonical(sparql: str) -> str | None:
    try:
        algebra = parse_sparql(sparql).algebra
    except SparqlParseError:
        return None
    return _stable_repr(_alpha_normalize(algebra, {}))
```
Import `_canonical` and `_load_corpus` from `tests.nl2sparql.eval.runner` exactly as `test_gold_transpilable.py` does (line 30). The two-way disjointness assertion (normalized question text AND canonical algebra) is spelled out concretely in RESEARCH.md's Pattern 3 code sample — copy that structure, defining a local `_normalize_question` helper and loading `fewshot_bank.yml` via `yaml.safe_load` (never raw `yaml.load` — matches `_load_corpus`'s own `yaml.safe_load` call at `runner.py` line 134).

---

### `tests/nl2sparql/eval/configs.yml` — new model entries + `few_shot:` block

**Analog:** the existing `configs.yml` (full file, 30 lines) — additive entries in the same schema.

**Current shape to extend** (lines 16-30):
```yaml
configs:
  scripted:
    provider:
      type: scripted
    judge: canonical
    max_repairs: 2

  openai-gpt4o-mini:
    provider:
      type: openai
      model: gpt-4o-mini
    judge: canonical
    max_repairs: 2
```
Add sibling blocks for `gpt-5-mini` / `gpt-5`, and for each model × arm (zero/dense/bm25) combination per D-07/D-08, e.g. `openai-gpt4o-mini-dense`, `openai-gpt4o-mini-bm25`, etc. Each new block follows the identical `provider: {type, model}` / `judge` / `max_repairs` shape; add the new optional `few_shot: {mode: zero|dense|bm25, k: int}` key per RESEARCH.md's Pitfall 5 resolution — additive, defaults to today's zero-shot behavior when absent so every existing config keeps working unchanged. Keep the top-of-file comment block's "provider/judge configs consumed by `run(config_name)`" framing and the "credentials are NEVER stored here" security note (lines 1-14) — extend it to also state `few_shot` config never carries secrets.

---

### `tests/nl2sparql/eval/runner.py` — additive `few_shot` config read

**Analog:** the same file's `_client_for` factory (lines 172-185) and `run()` (lines 348-383) — the pattern of reading an optional `config[...]` key with a default, already used for `judge`/`max_repairs`.

**Existing precedent for reading an optional config key with a default** (lines 353-354):
```python
judge_name = config.get("judge", "canonical")
max_repairs = config.get("max_repairs", 2)
```
Add `few_shot_cfg = config.get("few_shot", {})`, `few_shot_mode = few_shot_cfg.get("mode", "zero")`, `few_shot_k = few_shot_cfg.get("k", 0)` following this EXACT `.get(key, default)` idiom — no new config-parsing abstraction. Thread `few_shot_mode`/`few_shot_k` into the `NlPipeline` construction inside the `for case in corpus["cases"]:` loop (lines 356-366) by either (a) constructing/passing a `SparqlAdapter(..., few_shot_index=...)` explicitly when `few_shot_mode != "zero"`, using the SAME `_cached_few_shot_index` module-scope cache the production `SparqlAdapter.few_shot_index()` default uses (critical — Pitfall 1 applies identically inside this loop, which runs 25× per sweep arm), or (b) exposing a `NlPipeline(..., few_shot_k=..., few_shot_index=...)` passthrough. Either way, **`run()`'s signature (`run(config_name: str) -> Report`) and `Report`'s shape stay byte-identical** per RESEARCH.md's Pitfall 5 recommendation — only the internal per-case construction gains new optional plumbing.

**`BaselineConfig` extension** (lines 110-125) — the model already has the three provenance fields Phase 7 needs (`model`, `temperature`, `corpus_sha`); D-04 asks for embedding-model id + revision + sentence-transformers version alongside these. Add optional fields the same way:
```python
class BaselineConfig(BaseModel):
    pass_rate: float = Field(ge=0.0, le=1.0)
    passed: int = Field(ge=0)
    total: int = Field(ge=1)
    cases: dict[str, bool]
    model: str | None = None
    temperature: float | None = None
    corpus_sha: str | None = None
    # Phase 7 additions — dense-run provenance (D-04), same optional-field style:
    embedding_model: str | None = None
    embedding_revision: str | None = None
    sentence_transformers_version: str | None = None
```

---

### `tests/nl2sparql/eval/baseline.json` — dense/BM25 baseline artifact entries

**Analog:** the existing `openai-gpt4o-mini` entry (lines 36-71) — same nested `configs.<name>` shape, same manual-fold-in discipline (never auto-regenerated in CI, per `README.md` §5).

```json
"openai-gpt4o-mini": {
  "pass_rate": 0.32,
  "passed": 8,
  "total": 25,
  "cases": { "...": true },
  "model": "gpt-4o-mini",
  "temperature": 0.1,
  "corpus_sha": "d3d3806"
}
```
New sibling entries (`openai-gpt4o-mini-dense`, `openai-gpt5-mini-dense`, etc.) copy this exact shape, adding the new `embedding_model`/`embedding_revision`/`sentence_transformers_version` fields defined in `BaselineConfig` above. Fold-in remains a MANUAL, human-reviewed step per the README's existing runbook (§5) — do not add code that writes `baseline.json` automatically.

---

### `tests/test_engine_adapter.py::test_few_shot_index_is_none` → replace

**Analog:** the exact test being replaced (lines 191-193, class `TestSparqlAdapterSeams`):
```python
def test_few_shot_index_is_none(self) -> None:
    adapter = SparqlAdapter(resolver=SchemaResolver.from_turtle(ONTOLOGY), ontology_ttl=ONTOLOGY)
    assert adapter.few_shot_index() is None
```
Replace with an assertion that `few_shot_index()` returns a `FewShotIndex` instance (per RESEARCH Pitfall 6). Follow the SAME construction style as the other seam tests in this class (`test_validate_good_query_is_ok`, `test_guardrails_allow_all`, lines 161-165, 186-189) — instantiate `SparqlAdapter` the same way, just assert on the new return type instead of `None`. Leave `TestVerdictReproduction.test_engine_reproduces_baseline_verdicts` (lines 253-281) untouched — it manually hardcodes `few_shot_k=0` in its own standalone `NLQueryEngine` construction (line 266), independent of `pipeline.py`'s new default, so it is unaffected by the flip.

---

### `arango_query_core/tests/test_nl_fewshot.py` — `DenseRetriever` unit tests

**Analog:** the existing BM25/no-op tests in the same file (full file, 102 lines) — `test_loads_canonical_query_key`, `test_retrieval_ranks_by_relevance`, `test_format_prompt_section_tags_fence_with_language`, `test_empty_or_missing_corpus_degrades_to_noop`.

**Corpus-fixture helper to reuse verbatim** (lines 11-14):
```python
def _write_corpus(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p
```

**Ranking-assertion pattern to mirror** (lines 57-73 — same test shape, injectable-encoder substitute for the real model per RESEARCH's Wave 0 Gaps):
```python
def test_retrieval_ranks_by_relevance(tmp_path: Path) -> None:
    corpus = _write_corpus(tmp_path, "c.yml", """
examples:
  - question: "count all movies released after a year"
    query: "Q_MOVIES"
  - question: "list every person and their friends"
    query: "Q_FRIENDS"
  - question: "total orders per customer segment"
    query: "Q_ORDERS"
""")
    index = FewShotIndex.from_corpus_files([corpus])
    top = index.retrieve("how many movies came out after 2000?", k=1)
    assert top and top[0][1] == "Q_MOVIES"
```
The equivalent `DenseRetriever` test must NOT construct a real `SentenceTransformer` (no network / no torch on the fast CI path per D-03). Per RESEARCH's Wave 0 Gaps, inject a fake encoder — either a constructor param accepting a `Callable[[list[str]], np.ndarray]` stub, or a `monkeypatch.setattr("sentence_transformers.SentenceTransformer", FakeModel)` substitution — so retrieval-ranking LOGIC (the `sorted(..., key=lambda i: (-score, i))` tie-break, the cosine dot product) is tested without the real dependency. Note: `BM25Retriever`'s test comment at lines 51-54 ("BM25's IDF is non-positive for terms appearing in ≥ half the corpus... corpora below ~3 documents retrieve nothing") is a BM25-specific caveat that does NOT apply to cosine similarity — do not carry that comment over verbatim, but DO keep the ≥3-example corpus convention for consistency across the file's fixtures.

**No-op/empty-corpus degrade test to mirror** (lines 96-101):
```python
def test_empty_or_missing_corpus_degrades_to_noop(tmp_path: Path) -> None:
    empty = _write_corpus(tmp_path, "empty.yml", "examples: []\n")
    index = FewShotIndex.from_corpus_files([empty, tmp_path / "missing.yml"])
    assert index.examples == []
    assert index.retrieve("anything") == []
    assert index.format_prompt_section("anything") == ""
```
Add an equivalent `mode="dense"` + missing-`sentence-transformers` test asserting the hard `ImportError` (D-05's explicit-mode contract) — this is new coverage with no direct existing analog in this file (the BM25 no-op test only covers the auto-degrade path, not the explicit hard-raise path); construct it by `monkeypatch`-hiding the `sentence_transformers` import (e.g. via `sys.modules["sentence_transformers"] = None` + `monkeypatch` or `pytest.MonkeyPatch.context()` around a fresh import) and asserting `pytest.raises(ImportError, match="sentence-transformers")`.

---

## Shared Patterns

### Lazy-import + two-tier degrade contract
**Source:** `arango_query_core/nl/fewshot.py::BM25Retriever.__init__` (lines 56-64) + `FewShotIndex.from_corpus_files` (lines 165-173)
**Apply to:** `DenseRetriever.__init__`, and the `mode=` dispatch branch in `from_corpus_files`.
```python
try:
    from rank_bm25 import BM25Okapi
except ImportError as exc:
    raise ImportError(
        "BM25Retriever requires the 'rank_bm25' package. "
        "Install it with `pip install rank_bm25>=0.2.2` or "
        "`pip install 'arango-query-core[nl]'`."
    ) from exc
```
Every new `Retriever` implementation added to `fewshot.py` follows this exact shape: lazy import inside `__init__`, hard-raise `ImportError` with a two-part install hint (bare-package pip install OR extras-qualified pip install), never a `try/except` swallow at construct time.

### Deterministic ranking tie-break
**Source:** `arango_query_core/nl/fewshot.py::BM25Retriever.retrieve` (lines 78-81)
**Apply to:** `DenseRetriever.retrieve`
```python
ranked = sorted(
    range(len(self._examples)),
    key=lambda i: (-float(scores[i]), i),
)
```
Sort by `(-score, original_index)` — never plain `-score` alone — so ties break deterministically by insertion order. This determinism matters doubly for Phase 7: D-09's noise-floor measurement assumes retrieval itself is reproducible across runs (the LLM is the only stochastic element), so a non-deterministic tie-break would contaminate the lift measurement with retrieval jitter, not just model jitter.

### YAML trust boundary — `yaml.safe_load` only, never `yaml.load`
**Source:** `arango_query_core/nl/fewshot.py::from_corpus_files` (implicit `import yaml` + `yaml.safe_load`, line 147) and `tests/nl2sparql/eval/runner.py::_load_corpus`/`_load_configs` (lines 134, 145)
**Apply to:** `fewshot_bank.yml` loading, both in `from_corpus_files`'s dense-mode path and in `test_fewshot_bank_disjoint.py`.
```python
data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
```
Every YAML load in this codebase uses `safe_load` uniformly — this is a security-domain convention (RESEARCH.md's ASVS V5 note: bank YAML is trusted-repo data loaded via `safe_load`, same trust tier as `corpus.yml`/`configs.yml`), not a per-file choice.

### Gated-not-CI live sweep discipline
**Source:** `tests/nl2sparql/eval/README.md` (full runbook, esp. §§2-3, 5-6) + `tests/nl2sparql/eval/test_eval.py` (`_RUN_EVAL` gate, lines 26-32)
**Apply to:** the entire D-07/D-08/D-09 lift-measurement sweep — new `configs.yml` entries, any new orchestration script, and `test_fewshot_bank_disjoint.py`.
```python
_RUN_EVAL = os.getenv("RUN_EVAL", "").strip().lower() not in ("", "0", "false", "no")

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(not _RUN_EVAL, reason="set RUN_EVAL=1 to run the NL eval gate"),
]
```
`scripted` stays the CI default; every live-model config (existing `openai-gpt4o-mini` and Phase 7's new dense/BM25/gpt-5-family entries) requires `RUN_EVAL=1` + `NL2SPARQL_API_KEY`, is run manually/nightly, and is folded into `baseline.json` via a MANUAL, human-reviewed copy — never auto-regenerated in CI. Applies verbatim to the new D-07/D-08/D-09 sweep; do not create a new gating convention.

### Provenance-capture-in-artifact convention
**Source:** `tests/nl2sparql/eval/baseline.json` lines 66-70 + `runner.py::BaselineConfig` (lines 110-125) + README.md §5 (lines 105-145)
**Apply to:** the new dense-mode baseline entries, `BaselineConfig`.
```json
"model": "gpt-4o-mini",
"temperature": 0.1,
"corpus_sha": "d3d3806"
```
D-04's requirement (embedding model id + HF revision + sentence-transformers version alongside `corpus_sha`) is a direct EXTENSION of this exact convention, not a new one — same manual fold-in ritual, same "optional field on `BaselineConfig`, appended alongside `model`/`temperature`/`corpus_sha`" shape.

### Committed invariant test (authoring-time gate, not runtime regression gate)
**Source:** `tests/nl2sparql/eval/test_gold_transpilable.py` (full file)
**Apply to:** `test_fewshot_bank_disjoint.py`
The docstring framing ("Authoring guard... fails CI at authoring time rather than masquerading as a model-quality regression") is the exact framing to reuse for the disjointness gate's own docstring — both are "a corpus-authoring correctness property, proven by a committed pytest test, not a convention."

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| Module-scope `_cached_few_shot_index` (`lru_cache`) in `fewshot.py` | utility | CRUD (cache) | Neither repo has an existing memoization-of-a-model-backed-index pattern; this is genuinely new infrastructure required to avoid Pitfall 1 (see RESEARCH.md Pattern 2). Planner should treat the `lru_cache` code sample in RESEARCH.md as the primary reference since no in-repo precedent exists. |
| New (gated) lift-sweep orchestration script (`lift_sweep.py` or documented `python -c` invocation) | script | batch | 06.2's live-baseline precedent is a documented one-liner `python -c "from tests.nl2sparql.eval.runner import run, write_report; ..."` (README.md line 84) rather than a standalone script file — RESEARCH.md's Recommended Project Structure flags this as either a new script OR an extended README section; no existing STANDALONE script file exists to mirror in either repo, only the README-embedded one-liner precedent. |
| `_is_reasoning_model` / gpt-5 pricing rows in `cost.py` | utility/config | transform | `cost.py`'s `_PRICING_PER_1K_TOKENS` table (RESEARCH.md Pitfall 4) has no existing per-model-family branching precedent to mirror beyond "add a new dict key" — genuinely new data, not a new pattern; low risk, mechanical addition. |

## Metadata

**Analog search scope:** `arango_query_core/nl/` (fewshot.py, engine.py, seams.py, providers.py), `arango_sparql/nl2sparql/` (engine_adapter.py, pipeline.py, client.py, cost.py, prompt.py), `tests/nl2sparql/` (test_engine_adapter.py), `tests/nl2sparql/eval/` (corpus.yml, configs.yml, baseline.json, runner.py, README.md, test_eval.py, test_gold_transpilable.py), `arango_query_core/tests/test_nl_fewshot.py`, both repos' `pyproject.toml`.
**Files scanned:** 18 files read in full or targeted sections (all cited above with line numbers); no additional Glob/Grep sweep was needed since RESEARCH.md's canonical-refs list already enumerated every file this phase touches with high confidence (HIGH-confidence architecture rating in RESEARCH.md, grounded in code read that session).
**Pattern extraction date:** 2026-07-21

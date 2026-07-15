# Phase 6: NL→SPARQL eval harness + seed corpus - Research

**Researched:** 2026-07-15
**Domain:** Evaluation harness engineering for an LLM NL→SPARQL pipeline (Python, pytest, YAML corpus, JSON/Markdown reporting, regression gating)
**Confidence:** HIGH (all core interfaces verified against this repo's shipped code; corpus/config/judge/baseline schemas are grounded design recommendations tagged accordingly)

## Summary

This phase fills a fully-stubbed eval harness (`tests/nl2sparql/eval/runner.py` — `run()` and `write_report()` raise `NotImplementedError`) and authors the data it consumes (`corpus.yml`, `configs.yml`, `baseline.json`). The **good news the planner needs to internalize**: every runtime piece the harness must orchestrate already ships and is verified in this repo. The `LLMClient` protocol, a working `ScriptedLLMClient` test double, the `NlPipeline` orchestrator, the deterministic `translate()` transpiler, and the `pyoxigraph` binding-comparison helpers all exist and are exercised by passing tests. The harness is **integration glue over shipped parts**, not new subsystem work. No new third-party dependency is required — `PyYAML` is already declared (`nl`, `dev` extras) and importable (6.0.2).

The single most important correction for the planner: **the shipped code does NOT match the aspirational names in `.cursor/rules/300-nl2sparql.mdc` or in the runner docstring.** Rule 300 and the phase success-criteria text reference a `providers.py` module with an `LLMProvider` protocol and a `ScriptedProvider`. **That module does not exist.** The real, shipped surface is `arango_sparql/nl2sparql/client.py` with the `LLMClient` Protocol, `OpenAICompatibleClient`, `AnthropicClient`, and `ScriptedLLMClient`. The harness MUST bind to the shipped names. Do not create `providers.py`; do not invent a `ScriptedProvider` class. When the phase text says "ScriptedProvider," read it as "`ScriptedLLMClient`."

Second critical constraint (from the orchestrator, verified on disk): **`references/` symlinks do not exist on this machine** — `references/arango-cypher-py/tests/nl2cypher/eval/runner.py`, which CLAUDE.md and the runner docstring say to mirror, is unreachable. Ground all design in this repo's own code and the runner stub's docstrings (which precisely describe intended behavior). Treat the docstring's *described behavior* as the spec, not the unreachable source file.

**Primary recommendation:** Implement `run(config_name)` as a loop that, per corpus entry, builds a `SchemaResolver.from_turtle(ontology)` + `NlPipeline`, feeds a per-config `LLMClient` (a fresh `ScriptedLLMClient` for the `scripted` config; a real `OpenAICompatibleClient` for provider sweeps), runs the NL through `pipeline.run(nl)`, and judges the resulting `outcome.sparql` against the gold via **rdflib algebra-level canonical comparison** (reusing `parse_sparql`) plus a "did the transpiler accept it" check (`outcome.aql` non-empty). Wire a single `@pytest.mark.eval` test that runs the `scripted` config and asserts its pass-rate ≥ `baseline.json`; add a CI job that sets `RUN_EVAL=1`. Give corpus entries an optional `scripted:` field distinct from `expected:` so the scripted config exercises the judge for real (including a deliberate near-miss) rather than trivially scoring 100%.

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| NL-EVAL-01 | Eval harness implemented — `runner.py::run()` + `write_report()` execute each corpus entry against each configured provider, emit JSON+Markdown; eval marker wired into CI | Standard Stack + Architecture Patterns (run loop, provider binding, report writer) + Pitfall on marker gating + Validation Architecture (CI job) |
| NL-EVAL-02 | Seed corpus authored — `corpus.yml` + `configs.yml` created, `baseline.json` checked in as regression gate; NL→SPARQL pass-rate is a tracked metric | Corpus/Config Schema section + Judging section + baseline.json schema + gitignore facts |

## Project Constraints (from CLAUDE.md and .cursor/rules)

These carry the same authority as locked decisions. Research does not recommend anything that contradicts them.

- **rdflib is the only SPARQL parser** [CITED: CLAUDE.md hard rule 1]. The judge's canonical comparison MUST use `rdflib` (via the existing `arango_sparql.translate.parser.parse_sparql`, which calls `parseQuery`→`translateQuery`). Never write a custom SPARQL comparator/normalizer.
- **Never ask the LLM for AQL** [CITED: rule 300 "Forbidden"]. The pipeline emits SPARQL; the deterministic transpiler emits AQL. The harness measures NL→SPARQL, judging on the SPARQL layer (with optional execution-equivalence via the transpiler/pyoxigraph).
- **No LLM calls from non-`eval`-marked tests** [CITED: rule 200 "Forbidden"]. The scripted CI path uses `ScriptedLLMClient` (no network). Any real-provider run stays behind the `eval` marker + `RUN_EVAL=1`.
- **No character-for-character AQL/query string comparison outside goldens; semantic equivalence is what matters** [CITED: rule 200 "Forbidden"]. This is the decisive argument against exact-string SPARQL matching as the judge (see Judging section).
- **`pyoxigraph` is the W3C ground truth** [CITED: CLAUDE.md hard rule 5]. Available for an optional execution-equivalence judging tier; helpers live in `tests/helpers/oxi.py` — do not call `pyoxigraph` directly from other files [CITED: rule 200].
- **Do not modify anything under `references/`** and **do not add top-level deps without updating `pyproject.toml` + `uv lock`** [CITED: CLAUDE.md "Off-limits"]. No new dep is needed for this phase.
- **Reports (`tests/**/eval/reports/`) must never be committed** [CITED: CLAUDE.md "Off-limits"]; already gitignored.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Corpus/config loading (`corpus.yml`, `configs.yml`) | Eval harness (test tier) | — | Test-only data; PyYAML parse in `runner.py` |
| Provider binding (scripted vs real) | Eval harness | `nl2sparql.client` | Harness selects; `client.py` supplies `ScriptedLLMClient`/`OpenAICompatibleClient` |
| NL→SPARQL generation + repair | `nl2sparql.pipeline.NlPipeline` | `nl2sparql.client`, `nl2sparql.prompt`, `nl2sparql.repair` | Already shipped; harness calls `pipeline.run(nl)` |
| SPARQL→AQL translation (accept/reject signal) | `arango_sparql.api.translate` | `translate.resolver`, `translate.parser` | Deterministic; pipeline invokes it; harness reads `outcome.aql` |
| Pass/fail judging | Eval harness | `translate.parser` (rdflib), `tests/helpers/oxi` (optional) | Canonical SPARQL comparison + optional execution equivalence |
| Report emission (JSON + Markdown) | Eval harness (`write_report`) | — | Writes under `reports/` (gitignored) |
| Regression gate | `@pytest.mark.eval` test + CI job | `baseline.json` | Test compares live pass-rate vs checked-in baseline |
| W3C non-regression (criterion 5) | `tests/w3c/analyze_coverage.py` | — | Unaffected by this phase; verify unchanged |

## Standard Stack

### Core (all already present — verified)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| PyYAML | 6.0.2 (declared `>=6.0.0` in `nl`+`dev` extras) | Parse `corpus.yml` / `configs.yml` | Already a dependency; standard YAML lib [VERIFIED: pyproject.toml + `python -c "import yaml"` → 6.0.2] |
| pytest | `>=8.0.0` (`dev`) | `eval` marker host for the CI gate test | Only test runner in repo [VERIFIED: pyproject.toml, rule 200] |
| rdflib | `>=7.0` (core dep) | Canonical SPARQL comparison in the judge via `parse_sparql` | Mandated parser; already the transpiler's front end [VERIFIED: pyproject.toml, parser.py imports `parseQuery`/`translateQuery`] |
| pyoxigraph | `>=0.3.22` (`dev`) | Optional execution-equivalence judging tier | W3C ground truth; helpers in `tests/helpers/oxi.py` [VERIFIED: pyproject.toml] (NOTE: not importable in local dev shell here, but CI installs `[dev,nl,service]`) |

### Supporting (in-repo modules the harness composes — verified)
| Module / symbol | Purpose | When to Use |
|-----------------|---------|-------------|
| `arango_sparql.nl2sparql.client.LLMClient` (Protocol: `provider: str`, `model: str`, `generate(messages)->LLMResponse`) | The "any provider" contract | Type the config→client factory against this |
| `arango_sparql.nl2sparql.client.ScriptedLLMClient(responses, *, provider, model, latency_ms)` | Canned-response test double; pops per call, replays last | The `scripted` config's per-case client |
| `arango_sparql.nl2sparql.client.OpenAICompatibleClient` / `AnthropicClient` / `get_default_client()` | Real providers (env-driven) | Nightly real-provider sweeps only |
| `arango_sparql.nl2sparql.NlPipeline(client=, resolver=, ontology_ttl=, max_repairs=)` → `.run(nl)->PipelineOutcome` | The unit under test | Once per corpus case |
| `arango_sparql.nl2sparql.models.PipelineOutcome` (`.sparql`, `.aql`, `.repaired`, `.warnings`, `.llm_call_records`, `.latency_ms`, `.cost_usd`) | Result envelope to judge/report | Read `.sparql` (actual) + `.aql` (accept signal) |
| `arango_sparql.translate.resolver.SchemaResolver.from_turtle(ttl)` | Build resolver from ontology Turtle | Per case (ontology can vary per entry) |
| `arango_sparql.api.translate(sparql, *, resolver, params=)` / `TranslateResult` | Deterministic transpile (accept/reject) | Inside execution-equivalence tier if used directly |
| `arango_sparql.nl2sparql.prompt.extract_sparql_from_response` | Fenced-block SPARQL extraction | Already applied inside the pipeline; the scripted response should wrap SPARQL in a ```sparql fence (see `_wrap` in test_pipeline.py) |
| `tests/helpers/oxi.py`: `load_store_from_string`, `oxi_bindings`, `assert_bindings_equal` | pyoxigraph load + binding bag-equality | Optional execution-equivalence judge tier |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| rdflib algebra canonical comparison (judge) | Exact string match on SPARQL | Simpler but brittle (whitespace, PREFIX order, variable spelling) and **forbidden by rule 200** ("no char-for-char comparison outside goldens"); reject |
| rdflib algebra canonical comparison | pyoxigraph execution-result equivalence | Strongest ("does it return the right answer") but requires per-case RDF `data` fixtures + ArangoDB-free store; recommend as an **optional per-case tier**, not the default, to keep the seed corpus small |
| Fresh `ScriptedLLMClient` per case | One shared client with a queue for the whole run | Per-case is clearer and avoids cross-case queue-drain coupling; recommend per-case construction |

**Installation:** None. No new packages. (Confirm the CI eval job installs `.[dev,nl,service]` so PyYAML + pyoxigraph + pytest are present — the existing `test` job already does.)

## Package Legitimacy Audit

No new external packages are introduced by this phase. All libraries used are already declared in `pyproject.toml` and predate this phase.

| Package | Registry | Status | Source Repo | Disposition |
|---------|----------|--------|-------------|-------------|
| PyYAML | PyPI | Existing dep (`nl`,`dev`); importable 6.0.2 | github.com/yaml/pyyaml | Pre-approved (no new install) |
| pytest | PyPI | Existing dep (`dev`) | github.com/pytest-dev/pytest | Pre-approved |
| rdflib | PyPI | Existing core dep | github.com/RDFLib/rdflib | Pre-approved |
| pyoxigraph | PyPI | Existing dep (`dev`) | github.com/oxigraph/oxigraph | Pre-approved |

**Packages removed due to slopcheck [SLOP] verdict:** none (no new packages evaluated).
**Packages flagged as suspicious [SUS]:** none.

## Architecture Patterns

### System Architecture Diagram

```
                      corpus.yml                configs.yml
                          │                          │
                   (PyYAML load)              (PyYAML load)
                          │                          │
                          └──────────┬───────────────┘
                                     ▼
                         runner.run(config_name)
                                     │
                     for each corpus entry (case):
                                     │
        ┌────────────────────────────┼───────────────────────────────┐
        ▼                            ▼                                 ▼
  build resolver            build LLMClient for config          build PromptBuilder
  SchemaResolver              scripted → ScriptedLLMClient        (inside NlPipeline)
  .from_turtle(ontology)      real     → OpenAICompatibleClient
        │                            │
        └──────────────┬─────────────┘
                       ▼
        NlPipeline(client, resolver, ontology_ttl, max_repairs).run(nl)
                       │
                       ▼
               PipelineOutcome(.sparql, .aql, .repaired, .warnings, .latency_ms)
                       │
                       ▼
               JUDGE(expected gold SPARQL, outcome)
              ┌────────┴─────────┐
              ▼                  ▼
   canonical algebra eq    (optional) pyoxigraph
   via parse_sparql         execution-result eq
   + outcome.aql != ""      when case has `data:`
              └────────┬─────────┘
                       ▼
                 CaseResult(name, expected, actual, passed, elapsed_ms)
                       │
                  collect into Report(config, cases) → .pass_rate
                       │
              ┌────────┴──────────┐
              ▼                   ▼
      write_report()      @pytest.mark.eval gate test
   JSON + Markdown under    run("scripted") → assert
   reports/ (gitignored)    pass_rate >= baseline.json
```

Trace of the primary use case: a corpus entry's `nl` question enters `run()`, is fed to `NlPipeline.run()` with a config-selected client, produces SPARQL, which the judge compares to the entry's gold `expected` → a `CaseResult`; all cases aggregate into a `Report` whose `pass_rate` is both reported (JSON/Markdown) and gate-checked against `baseline.json`.

### Recommended File Layout
```
tests/nl2sparql/eval/
├── __init__.py          # exists (empty)
├── runner.py            # EXISTS (stub) — implement run() + write_report() + judge + provider factory + loaders
├── corpus.yml           # NEW (checked in) — seed cases
├── configs.yml          # NEW (checked in) — provider/judge configs
├── baseline.json        # NEW (checked in) — regression gate
├── test_eval.py         # NEW — @pytest.mark.eval gate test running the `scripted` config
└── reports/             # gitignored — created at runtime by write_report()
```

### Pattern 1: Config → LLMClient factory (the "any provider" seam)
**What:** A small function mapping a config's `provider` block to an `LLMClient`. For `scripted`, the client is **per-case** (canned to that case's response); for real providers, one client is reused.
**When to use:** Inside `run(config_name)`.
**Example:**
```python
# Source: shape derived from arango_sparql/nl2sparql/client.py (VERIFIED interfaces)
def _client_for(config: dict, case: dict) -> LLMClient:
    p = config["provider"]
    if p["type"] == "scripted":
        canned = case.get("scripted", case["expected"])   # default: echo the gold
        return ScriptedLLMClient([_wrap_sparql(canned)], latency_ms=0)
    if p["type"] in ("openai", "openrouter"):
        return OpenAICompatibleClient(provider=p["type"], model=p.get("model"))
    if p["type"] == "anthropic":
        return AnthropicClient(model=p.get("model"))
    raise ValueError(f"unknown provider type {p['type']!r}")

def _wrap_sparql(sparql: str) -> LLMResponse:
    # The pipeline runs extract_sparql_from_response(); a fenced block is what a
    # real model emits. Mirror tests/nl2sparql/test_pipeline.py::_wrap.
    return LLMResponse(content=f"```sparql\n{sparql.strip()}\n```",
                       prompt_tokens=0, completion_tokens=0, total_tokens=0)
```

### Pattern 2: Canonical SPARQL judge (rdflib algebra, no DB)
**What:** Two SPARQL strings are equal iff their rdflib-translated algebra representations are equal. Reuse the transpiler's own front end so the judge and the transpiler agree on what "parseable" means.
**When to use:** Default judge for every case.
**Example:**
```python
# Source: arango_sparql/translate/parser.py (VERIFIED: parse_sparql wraps parseQuery+translateQuery)
from arango_sparql.translate.parser import parse_sparql
from arango_sparql.errors import SparqlError

def _canonical(sparql: str) -> str | None:
    try:
        return repr(parse_sparql(sparql).algebra)   # confirm attribute name in ParsedSparql
    except SparqlError:
        return None

def _judge_canonical(expected: str, outcome) -> bool:
    # Must translate cleanly (transpiler accepted it) AND match the gold's algebra.
    if not outcome.aql:                 # empty AQL == transpiler rejected / repair exhausted
        return False
    ce, ca = _canonical(expected), _canonical(outcome.sparql)
    return ce is not None and ce == ca
```
> [ASSUMED] The exact attribute exposed by `ParsedSparql` for the translated algebra needs a one-line confirmation during planning (parser.py names a `ParsedSparql` dataclass and mentions `PV`/`translateQuery`; the planner/executor should read `parse_sparql`'s return shape and pick the algebra field or a normalized string form). If a direct algebra `repr` proves unstable across rdflib internals, fall back to comparing `translateQuery` output serialized via rdflib's algebra `pprintAlgebra`/`translateAlgebra` — still rdflib, still no custom parser.

### Pattern 3: Optional execution-equivalence tier
**What:** When a corpus entry carries inline `data:` (Turtle), run both gold and generated SPARQL against a `pyoxigraph` store and bag-compare bindings.
**When to use:** High-value cases where "returns the same rows" is the real success bar; keep it opt-in to avoid authoring RDF for every seed entry.
**Example:**
```python
# Source: tests/helpers/oxi.py (VERIFIED helper names)
from tests.helpers.oxi import load_store_from_string, oxi_bindings
def _judge_execution(expected: str, actual: str, data_ttl: str) -> bool:
    store = load_store_from_string(data_ttl)
    return _bag(oxi_bindings(store, expected)) == _bag(oxi_bindings(store, actual))
```

### Anti-Patterns to Avoid
- **Creating `arango_sparql/nl2sparql/providers.py` or a `ScriptedProvider` class.** Rule 300's structure section is aspirational; the shipped surface is `client.py` + `ScriptedLLMClient`. Building the aspirational module is scope the phase does not call for and duplicates working code.
- **Chasing `references/arango-cypher-py/...`.** Those paths are unreachable on this machine (only a README exists under `references/`). Any task action that opens a `references/` file will fail.
- **Exact-string SPARQL matching as the judge.** Forbidden by rule 200 and brittle.
- **Committing `reports/`.** Gitignored already; keep it that way.
- **Letting the eval gate call a real provider in the per-PR CI job.** Scripted only in CI; real providers are nightly/manual.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| SPARQL parsing/normalization for the judge | A regex/AST normalizer | `arango_sparql.translate.parser.parse_sparql` (rdflib) | Mandated parser; guarantees judge agrees with transpiler on parseability |
| Provider abstraction | A new provider base class | `LLMClient` protocol + `ScriptedLLMClient`/`OpenAICompatibleClient` | Already shipped, tested, and protocol-checked |
| NL→SPARQL orchestration + repair loop | A bespoke translate loop in the harness | `NlPipeline.run()` | The pipeline already owns prompt build, LLM call, transpile, bounded repair, audit trail |
| RDF store for execution equivalence | Anything DB-backed | `tests/helpers/oxi.py` over `pyoxigraph` | In-process W3C ground truth; no Docker |
| YAML loading | Custom parser | `yaml.safe_load` | PyYAML already present; `safe_load` avoids arbitrary-object risk |

**Key insight:** The harness's entire job is to *drive* shipped components and *record* outcomes. Every temptation to "reimplement translation/comparison" collides with an existing, tested module or a project hard rule.

## Corpus / Config / Baseline Schema (recommended, grounded in what `run()` consumes)

> These are design recommendations tagged `[ASSUMED]` — they need user/planner confirmation. They are derived from what `NlPipeline`, `SchemaResolver`, and the `Report`/`CaseResult` dataclasses actually require.

### `corpus.yml` [ASSUMED]
```yaml
# Optional shared default ontology; entries may override.
ontology: |
  @prefix : <http://ex.org/> .
  @prefix owl: <http://www.w3.org/2002/07/owl#> .
  @prefix phys: <https://arango.solutions/phys#> .
  @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
  :Person a owl:Class ; phys:collectionName "Person" .
  :name a owl:DatatypeProperty ; rdfs:domain :Person ;
        rdfs:range <http://www.w3.org/2001/XMLSchema#string> .
cases:
  - name: people-with-names
    nl: "Find all people with their names"
    expected: |            # gold SPARQL (the judge's target)
      PREFIX : <http://ex.org/>
      SELECT ?s ?n WHERE { ?s a :Person ; :name ?n . }
    # scripted:            # OPTIONAL — what ScriptedLLMClient returns for the
    #   ...                # `scripted` config. Omit → defaults to `expected`.
    # ontology: |          # OPTIONAL per-case override of the shared ontology.
    # params: {limit: 10}  # OPTIONAL bind vars forwarded to translate().
    # data: |              # OPTIONAL Turtle → enables execution-equivalence judging.
```
Field rationale (each maps to a real call): `nl`→`pipeline.run(nl)`; `ontology`→`SchemaResolver.from_turtle` + `ontology_ttl`; `expected`→judge target; `scripted`→`ScriptedLLMClient` canned content; `params`→`translate(..., params=)`; `data`→`load_store_from_string`.

**Recommend including at least one deliberate near-miss case** (a `scripted:` that differs semantically from `expected:`) so the `scripted` config's pass-rate is < 100% — this proves the judge actually discriminates and makes `baseline.json` a meaningful, non-trivial number.

### `configs.yml` [ASSUMED]
```yaml
configs:
  scripted:                # the CI default — no network, deterministic
    provider: {type: scripted}
    judge: canonical       # canonical | execution
    max_repairs: 2
  openai-gpt4o-mini:       # nightly / manual real-provider sweep
    provider: {type: openai, model: gpt-4o-mini}
    judge: canonical
    max_repairs: 2
```
`run(config_name)` looks up `configs[config_name]`, iterates `corpus.cases`, builds client per Pattern 1, judges per `judge`.

### `baseline.json` [ASSUMED] — the regression gate
Store **both** the aggregate pass-rate (for the primary metric + gate) **and** the per-case verdicts (so a swap that keeps the same rate but breaks a different case is caught):
```json
{
  "generated_at": "2026-07-15T00:00:00Z",
  "configs": {
    "scripted": {
      "pass_rate": 0.9,
      "passed": 9,
      "total": 10,
      "cases": { "people-with-names": true, "deliberate-near-miss": false }
    }
  }
}
```
**Gate logic (in `test_eval.py`):** fail if `live.pass_rate < baseline.pass_rate - EPSILON` (aggregate regression), and optionally fail if any case that was `true` in baseline is now `false` (per-case regression). Only the `scripted` config is gated in CI (deterministic); real-provider baselines, if recorded, are informational until a nightly workflow lands (Phase 7 territory).

## Common Pitfalls

### Pitfall 1: Binding to non-existent `providers.py` / `ScriptedProvider`
**What goes wrong:** Import errors; wasted effort building a parallel provider module.
**Why it happens:** Rule 300 and the runner docstring describe an aspirational structure that was never built; the working code lives in `client.py`.
**How to avoid:** Import from `arango_sparql.nl2sparql.client` (`LLMClient`, `ScriptedLLMClient`, `OpenAICompatibleClient`). Grep confirms these are the exported names in `nl2sparql/__init__.py`.
**Warning signs:** `ModuleNotFoundError: providers` or `ImportError: ScriptedProvider`.

### Pitfall 2: ScriptedLLMClient queue drain across cases
**What goes wrong:** A single shared `ScriptedLLMClient` for the whole run replays its *last* response after the queue empties, so case N gets case (N-1)'s SPARQL.
**Why it happens:** `ScriptedLLMClient` pops until one remains, then replays it forever [VERIFIED: client.py `generate`].
**How to avoid:** Construct a fresh `ScriptedLLMClient` per case (Pattern 1). A repair-loop case needs ≥2 responses queued (first bad, then good) — but for a clean judge of NL→SPARQL, most scripted cases need exactly one.
**Warning signs:** All cases after the first score identically / wrong.

### Pitfall 3: The eval marker never actually runs in CI
**What goes wrong:** Success criterion 2 ("eval marker runs green in CI") silently unmet because the existing `test` job *excludes* `eval` (`-m "not integration and not w3c and not eval"`) and `eval` is gated behind `RUN_EVAL=1`.
**Why it happens:** The stub was wired to be skipped so imports compile without a real runner.
**How to avoid:** Add a **new CI job/step** that installs `.[dev,nl,service]`, sets `RUN_EVAL=1`, and runs `pytest -m eval`. The `eval`-marked test must also honor `RUN_EVAL` (skip when unset) so local `pytest` stays fast. See Validation Architecture.
**Warning signs:** CI green but no eval test ever executed (0 selected).

### Pitfall 4: Judge disagreeing with the transpiler on "parseable"
**What goes wrong:** A generated query the transpiler accepts is judged unparseable (or vice-versa) because the judge uses a different parser.
**Why it happens:** Hand-rolled normalization diverges from rdflib.
**How to avoid:** Judge via the same `parse_sparql` the transpiler uses; treat `outcome.aql == ""` as the authoritative "rejected" signal.

### Pitfall 5: Trivial 100% scripted pass-rate hides a broken judge
**What goes wrong:** If the scripted config always feeds back the gold SPARQL, every case passes regardless of judge correctness — `baseline.json` becomes `1.0` and proves nothing.
**How to avoid:** Author ≥1 case with a `scripted:` that is semantically wrong vs `expected:`, so the scripted pass-rate is intentionally < 1.0 and the judge's discrimination is exercised in CI.

### Pitfall 6: pyoxigraph absent locally
**What goes wrong:** Execution-equivalence tier import fails in a shell without the `dev` extra installed (confirmed: `pyoxigraph` not importable in this session's base interpreter).
**How to avoid:** Make the execution tier import lazy and gate it on the `eval` marker + `dev` install; the CI eval job installs `.[dev,...]`. Default judge (`canonical`) needs only rdflib (core dep) and works everywhere.

## Runtime State Inventory

This is a greenfield-within-tests phase (new files + a stub fill; no rename/migration). No stored data, live-service config, OS-registered state, secrets, or build artifacts carry a string being changed.
- **Stored data:** None — the harness is stateless; reports are ephemeral (gitignored).
- **Live service config:** None — no service/DB touched; `pyoxigraph` runs in-process on inline `data`.
- **OS-registered state:** None.
- **Secrets/env vars:** `RUN_EVAL` (new CI toggle, no secret); real-provider keys (`NL2SPARQL_API_KEY`/`OPENAI_API_KEY`/…) are read only on the non-CI real-provider path and are NOT needed for the scripted CI gate — verified by `get_default_client()` returning `None` without keys and by `ScriptedLLMClient` needing none.
- **Build artifacts:** None.

## Code Examples

### Reading the pipeline result the harness judges on
```python
# Source: arango_sparql/nl2sparql/pipeline.py + models.py (VERIFIED)
from arango_sparql.nl2sparql import NlPipeline
from arango_sparql.translate.resolver import SchemaResolver

resolver = SchemaResolver.from_turtle(ontology_ttl)
pipeline = NlPipeline(client=client, resolver=resolver,
                      ontology_ttl=ontology_ttl, max_repairs=config["max_repairs"])
outcome = pipeline.run(case["nl"], params=case.get("params"))
# outcome.sparql -> generated SPARQL (the CaseResult.actual)
# outcome.aql    -> "" iff transpiler rejected / repair exhausted (accept signal)
# outcome.repaired, outcome.warnings, outcome.latency_ms, outcome.cost_usd available for the report
```

### The CI gate test
```python
# Source: rule 200 marker semantics + pyproject markers (VERIFIED)
import os, json, pytest
from pathlib import Path
from tests.nl2sparql.eval.runner import run, EVAL_DIR

pytestmark = pytest.mark.eval

@pytest.mark.skipif(not os.getenv("RUN_EVAL"), reason="set RUN_EVAL=1 to run the NL eval gate")
def test_scripted_pass_rate_meets_baseline():
    report = run("scripted")
    baseline = json.loads((EVAL_DIR / "baseline.json").read_text())["configs"]["scripted"]
    assert report.pass_rate >= baseline["pass_rate"] - 1e-9
    # optional: assert no previously-passing case regressed
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Rule-300 aspirational `providers.py`/`LLMProvider`/`ScriptedProvider` | Shipped `client.py`/`LLMClient`/`ScriptedLLMClient` | Already the case in this repo | Harness binds to the shipped names |
| Legacy `_core.nl_to_sparql` stub | `NlPipeline.run()` | Already shipped | Harness drives `NlPipeline`, not the legacy stub (STATE.md notes the stub is dead) |

**Deprecated/outdated:**
- `references/` mirroring instruction for the eval runner: unreachable on this machine — treat the runner docstring's described behavior as the spec.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `corpus.yml` schema (fields `name/nl/expected/scripted/ontology/params/data`) | Corpus/Config Schema | Low — fields map 1:1 to real call args; adjust naming freely |
| A2 | `configs.yml` schema (`provider.type`, `judge`, `max_repairs`) | Corpus/Config Schema | Low — internal to the harness |
| A3 | `baseline.json` shape (aggregate + per-case) and gate logic | baseline.json | Medium — determines gate strictness; confirm with user whether per-case regression should also fail CI |
| A4 | Default judge = rdflib canonical algebra comparison; execution tier optional | Judging / Pattern 2 | Medium — if user wants execution-equivalence as the default bar, every seed case needs `data:` Turtle (more authoring) |
| A5 | Exact `ParsedSparql` algebra attribute for canonical `repr` | Pattern 2 | Low — one-line confirmation in planning; rdflib fallback exists |
| A6 | New CI job sets `RUN_EVAL=1` + installs `.[dev,nl,service]` and runs `pytest -m eval` | Validation Architecture | Low — mirrors existing `test` job shape |
| A7 | Include a deliberate near-miss case so scripted pass-rate < 1.0 | Pitfall 5 | Low — design choice that strengthens the gate |

## Open Questions

1. **Should the default judge be canonical-algebra or execution-equivalence?**
   - What we know: rule 200 forbids string matching; both rdflib-canonical and pyoxigraph-execution satisfy "semantic equivalence." Canonical needs no per-case data; execution needs `data:` Turtle per case.
   - Recommendation: default `canonical`, opt into `execution` per case via a `data:` field. Confirm with user during discuss/plan.
2. **Should per-case regression (a previously-passing case now failing) fail CI, or only aggregate pass-rate drop?**
   - Recommendation: record per-case verdicts in `baseline.json` regardless; make per-case failure a hard gate (catches silent swaps). Confirm.
3. **Do we record a real-provider baseline now, or defer to Phase 7?**
   - Recommendation: ship only the `scripted` baseline as the enforced gate this phase; real-provider sweeps + baselines are Phase 7 (few-shot lift) territory. The harness should support real configs but CI enforces scripted only.
4. **Exact `ParsedSparql` algebra field name** — resolve by reading `parse_sparql`'s return during planning (see A5).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| PyYAML | corpus/config loading | ✓ | 6.0.2 | — |
| rdflib | canonical judge (parse_sparql) | ✓ (core dep) | ≥7.0 | — |
| pytest | eval marker gate | ✓ (dev) | ≥8.0.0 | — |
| pyoxigraph | optional execution-equivalence tier | ✗ locally / ✓ in CI (`dev` extra) | ≥0.3.22 | Use canonical judge (default); execution tier lazy-imported behind `eval`+`dev` |
| LLM API key | real-provider sweeps only | n/a for CI scripted path | — | Scripted path needs no key (`ScriptedLLMClient`) |

**Missing dependencies with no fallback:** none for the scripted CI gate (the phase's success criteria).
**Missing dependencies with fallback:** `pyoxigraph` locally — only affects the optional execution tier; the default canonical judge and the whole scripted CI gate work without it.

## Validation Architecture

> nyquist_validation: no `.planning/config.json` exists → treated as enabled. Section included.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest ≥8.0.0 |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` (markers incl. `eval`) |
| Quick run command | `pytest tests/nl2sparql -q -m "not eval"` |
| Full suite command | `RUN_EVAL=1 pytest -m eval -q` (eval gate) + `pytest -m "not integration and not w3c and not eval" -q` (existing suite) |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| NL-EVAL-01 | `run()`/`write_report()` execute corpus×config, emit JSON+MD | unit/integration | `RUN_EVAL=1 pytest tests/nl2sparql/eval/test_eval.py -q` | ❌ Wave 0 (`test_eval.py`) |
| NL-EVAL-01 | eval marker green in CI with scripted provider | CI job | new CI job: `RUN_EVAL=1 pytest -m eval -q` | ❌ Wave 0 (ci.yml job) |
| NL-EVAL-02 | corpus/configs authored; numeric pass-rate reported | data + assertion | asserted inside `test_eval.py` (`report.pass_rate` numeric) | ❌ Wave 0 (`corpus.yml`,`configs.yml`) |
| NL-EVAL-02 | `baseline.json` enforced as gate | regression | `test_eval.py` compares live vs `baseline.json` | ❌ Wave 0 (`baseline.json`) |
| (criterion 5) | W3C query-eval coverage ≥ 96.4% unchanged | non-regression | `python tests/w3c/analyze_coverage.py` (or `pytest -m w3c`) | ✅ exists (`tests/w3c/analyze_coverage.py`) |

### Sampling Rate
- **Per task commit:** `pytest tests/nl2sparql -q -m "not eval"` (fast; existing pipeline/client tests stay green).
- **Per wave merge:** `RUN_EVAL=1 pytest -m eval -q` (the new gate) + existing default suite.
- **Phase gate:** eval gate green in CI with the scripted provider **and** `python tests/w3c/analyze_coverage.py` still ≥ 96.4% before `/gsd-verify-work`.

### Wave 0 Gaps
- [ ] `tests/nl2sparql/eval/test_eval.py` — the `@pytest.mark.eval` gate test (covers NL-EVAL-01/02)
- [ ] `tests/nl2sparql/eval/corpus.yml` — seed cases incl. ≥1 deliberate near-miss (NL-EVAL-02)
- [ ] `tests/nl2sparql/eval/configs.yml` — `scripted` + ≥1 real config (NL-EVAL-02)
- [ ] `tests/nl2sparql/eval/baseline.json` — scripted baseline (NL-EVAL-02)
- [ ] `.github/workflows/ci.yml` — new job/step: install `.[dev,nl,service]`, `RUN_EVAL=1`, `pytest -m eval -q` (NL-EVAL-01)
- [ ] Framework install: none — pytest/PyYAML/rdflib already present.

## Security Domain

> `security_enforcement` config absent → treated as enabled. This phase is test-tier only (no new HTTP surface, no user input at runtime), so most ASVS categories are N/A. The relevant risks are supply-chain and secret-handling in CI.

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | No auth surface added |
| V3 Session Management | no | — |
| V4 Access Control | no | — |
| V5 Input Validation | yes (mild) | `yaml.safe_load` (never `yaml.load`) for corpus/configs; treat corpus as trusted repo data |
| V6 Cryptography | no | No new crypto |
| V14 Config / Build | yes | CI must NOT expose provider API keys to the scripted eval job; scripted path needs none. Reports stay gitignored (no data leak to VCS). |

### Known Threat Patterns for this stack
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Untrusted YAML deserialization | Tampering / RCE | `yaml.safe_load` only [CITED: PyYAML docs] |
| Secret leakage via CI logs / committed reports | Information Disclosure | Scripted CI job uses no API key; `reports/` gitignored; rule 200 forbids LLM calls outside `eval` |
| Accidental network call in the "scripted" gate | — | Bind the scripted config to `ScriptedLLMClient` (no `requests` import path); assert `client.calls` recorded but no HTTP |

## Sources

### Primary (HIGH confidence — this repo's shipped code, read this session)
- `tests/nl2sparql/eval/runner.py` — stub contract: `CaseResult`, `Report.pass_rate`, `run(config_name)->Report`, `write_report(report)->(Path,Path)`, `EVAL_DIR/CORPUS_PATH/CONFIGS_PATH/REPORTS_DIR`.
- `arango_sparql/nl2sparql/client.py` — `LLMClient` protocol, `ScriptedLLMClient`, `OpenAICompatibleClient`, `AnthropicClient`, `get_default_client`.
- `arango_sparql/nl2sparql/pipeline.py` — `NlPipeline(client, resolver, ontology_ttl, max_repairs).run(nl, params=)`.
- `arango_sparql/nl2sparql/models.py` — `PipelineOutcome`, `LLMResponse`, `LLMCallRecord`.
- `arango_sparql/nl2sparql/prompt.py` — `extract_sparql_from_response`, fenced-block convention.
- `arango_sparql/api.py` + `arango_sparql/translate/resolver.py` — `translate()`, `TranslateResult`, `SchemaResolver.from_turtle`.
- `arango_sparql/translate/parser.py` — `parse_sparql` (rdflib `parseQuery`+`translateQuery`).
- `arango_sparql/errors.py` — error codes (`E_SPARQL_PARSE`, `E_SPARQL_UNSUPPORTED`, …).
- `tests/nl2sparql/test_pipeline.py`, `test_samples.py`, `test_client_factory.py` — ScriptedLLMClient usage patterns (`_wrap`, per-case construction, queue semantics).
- `tests/helpers/oxi.py` — `load_store_from_string`, `oxi_bindings`, `assert_bindings_equal`.
- `pyproject.toml` — markers (`eval` gated on RUN_EVAL), deps (PyYAML/pyoxigraph/rdflib/pytest).
- `.github/workflows/ci.yml` — existing `test` job excludes `eval`; install line `.[dev,nl,service]`.
- `.gitignore` line 66 — `tests/nl2sparql/eval/reports/` ignored.
- `.cursor/rules/200-testing.mdc`, `.cursor/rules/300-nl2sparql.mdc`, `CLAUDE.md` — project hard rules.
- `.planning/ROADMAP.md`, `.planning/REQUIREMENTS.md`, `.planning/STATE.md` — phase goal, NL-EVAL-01/02, decisions.

### Secondary (MEDIUM)
- Environment probes this session: `python -c "import yaml"` → 6.0.2 (present); `import pyoxigraph` → not importable locally (present in CI `dev` extra).

### Tertiary (LOW)
- None. No web sources were needed; all findings are grounded in local code (the phase is integration over shipped parts).

## Metadata

**Confidence breakdown:**
- Standard stack / interfaces: HIGH — every symbol verified by reading the shipped module.
- Architecture / run loop: HIGH — composed entirely from verified interfaces.
- Corpus/config/baseline schema: MEDIUM — design recommendations grounded in call requirements; tagged `[ASSUMED]`, need planner/user confirmation.
- Judging approach: MEDIUM — rdflib-canonical is the safe default and rule-compliant; execution-equivalence is an optional upgrade; exact algebra field needs a one-line confirm.
- Pitfalls / CI wiring: HIGH — derived from the actual marker gating and ci.yml.

**Research date:** 2026-07-15
**Valid until:** 2026-08-14 (stable — local code and pinned deps; re-check only if `nl2sparql` interfaces or `pyproject.toml` markers change)

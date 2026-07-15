# Phase 6: NL→SPARQL eval harness + seed corpus - Pattern Map

**Mapped:** 2026-07-15
**Files analyzed:** 6 (2 modify, 4 new)
**Analogs found:** 5 with matches / 6 (1 file — `baseline.json` — has no in-repo analog)

All analogs are in THIS repo. The `references/` symlinks are unreachable on this
machine (confirmed by RESEARCH.md); the runner docstring's mention of
`tests/nl2cypher/eval/runner.py` is NOT a citable source — treat the docstring's
described behavior as spec, and copy concrete structure only from the files below.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `tests/nl2sparql/eval/runner.py` (MODIFY — fill `run()`/`write_report()`) | test-harness / utility | batch (corpus×config → report) | self (dataclasses + module constants already present); composes `arango_sparql/nl2sparql/pipeline.py`, `client.py`, `translate/parser.py` | role-match (glue) |
| `tests/nl2sparql/eval/test_eval.py` (NEW) | test | request-response (assert gate) | `tests/nl2sparql/test_pipeline.py` (ScriptedLLMClient injection) + `test_samples.py` | exact |
| `tests/nl2sparql/eval/corpus.yml` (NEW) | config / fixture (data) | file-I/O (YAML load) | `tests/translate/bgp_select.yml` (`ontology:` + `cases:` shape) | role-match |
| `tests/nl2sparql/eval/configs.yml` (NEW) | config / fixture (data) | file-I/O (YAML load) | `tests/translate/bgp_select.yml` (top-level keyed map) | role-match (weak — no config-shaped YAML exists) |
| `tests/nl2sparql/eval/baseline.json` (NEW) | config / fixture (data) | file-I/O (JSON read) | NONE (see "No Analog Found") | none |
| `.github/workflows/ci.yml` (MODIFY — add eval job/step) | config | event-driven (CI) | `.github/workflows/ci.yml` `test` job (lines 38-56) | exact |

## Pattern Assignments

### `tests/nl2sparql/eval/runner.py` (fill `run()` + `write_report()`)

**Analog:** self (stub) + composed shipped modules. The stub already fixes the
public surface — do NOT rename: `EVAL_DIR`, `CORPUS_PATH`, `CONFIGS_PATH`,
`REPORTS_DIR`, `CaseResult(name, expected, actual, passed, elapsed_ms)`,
`Report(config, cases)` with `.pass_rate`, `run(config_name)->Report`,
`write_report(report, *, out_dir=REPORTS_DIR)->tuple[Path, Path]`.

**Module constants already defined** (`runner.py` lines 19-22) — reuse as-is:
```python
EVAL_DIR = Path(__file__).parent
CORPUS_PATH = EVAL_DIR / "corpus.yml"
CONFIGS_PATH = EVAL_DIR / "configs.yml"
REPORTS_DIR = EVAL_DIR / "reports"
```

**YAML load pattern** — copy from `tests/translate/test_translate_bgp_select_goldens.py` lines 26-40:
```python
import yaml
data = yaml.safe_load(GOLDEN_PATH.read_text())   # use yaml.safe_load, never yaml.load
ttl = data["ontology"]
for case in data["cases"]:
    ... case["name"], case["sparql"], ...
```
Apply the same `yaml.safe_load(PATH.read_text())` for both `CORPUS_PATH` and `CONFIGS_PATH`.

**Provider factory (Pattern 1) — bind to SHIPPED names** from
`arango_sparql/nl2sparql/client.py`. `ScriptedLLMClient.__init__` (lines 362-376) signature:
```python
ScriptedLLMClient(
    responses: list[LLMResponse | BaseException],
    *, provider: str = "openai", model: str = "gpt-4o-mini", latency_ms: int = 5,
)
```
CRITICAL — **construct a fresh `ScriptedLLMClient` per case** (client.py `generate`
lines 385-388 replays the LAST response forever once the queue drains; a shared
client leaks case N-1's SPARQL into case N). One canned response per clean case;
≥2 for a repair-loop case.

**Scripted response must be a fenced block** — copy `_wrap` from
`tests/nl2sparql/test_pipeline.py` lines 73-75 (the pipeline runs
`extract_sparql_from_response`, which expects a ```` ```sparql ```` fence):
```python
def _wrap(sparql: str) -> str:
    return f"Here you go:\n\n```sparql\n{sparql}\n```"
```
Wrap in an `LLMResponse` (`arango_sparql/nl2sparql/models.py` lines 52-67 — fields
`content`, `prompt_tokens=0`, `completion_tokens=0`, `total_tokens=0`, `cached_tokens=0`).

**Pipeline invocation** — `NlPipeline` is keyword-only
(`arango_sparql/nl2sparql/pipeline.py` lines 76-98):
```python
resolver = SchemaResolver.from_turtle(ontology_ttl)          # translate/resolver.py
pipeline = NlPipeline(client=client, resolver=resolver,
                      ontology_ttl=ontology_ttl, max_repairs=config["max_repairs"])
outcome = pipeline.run(case["nl"], params=case.get("params"))  # params is keyword-only
```
Read from `PipelineOutcome` (`models.py` lines 91-118): `outcome.sparql`
(→ `CaseResult.actual`), `outcome.aql` (`== ""` iff transpiler rejected / repair
exhausted — the authoritative accept signal), plus `outcome.latency_ms`,
`outcome.repaired`, `outcome.warnings`, `outcome.cost_usd`, `outcome.llm_calls`
for the report body.

**Judge (Pattern 2, canonical rdflib) — no string matching** (rule 200). Use the
transpiler's own front end `arango_sparql/translate/parser.py`. Confirmed return
shape (`parser.py` lines 33-78): `parse_sparql(query) -> ParsedSparql` with field
`.algebra` (the rdflib Algebra root). This resolves RESEARCH open question A5:
```python
from arango_sparql.translate.parser import parse_sparql
from arango_sparql.errors import SparqlParseError   # NOTE: SparqlParseError, not SparqlError

def _canonical(sparql: str) -> str | None:
    try:
        return repr(parse_sparql(sparql).algebra)
    except SparqlParseError:
        return None

def _judge_canonical(expected: str, outcome) -> bool:
    if not outcome.aql:                       # transpiler rejected / repair exhausted
        return False
    ce, ca = _canonical(expected), _canonical(outcome.sparql)
    return ce is not None and ce == ca
```
> Correction vs RESEARCH.md Pattern 2: the raised type is `SparqlParseError` (parser.py
> line 60 docstring + the `raise SparqlParseError(...)`), not a generic `SparqlError`.
> If `repr(algebra)` proves unstable, fall back to rdflib's algebra pretty-printer —
> still rdflib, still no custom comparator.

**Optional execution tier (Pattern 3)** — lazy-import
`tests/helpers/oxi.py`: `load_store_from_string(ttl)` (lines 31-37),
`oxi_bindings(store, sparql)` (lines 55-79), `assert_bindings_equal(exp, act)`
(lines 118-125, order-insensitive bag equality). Keep the import lazy and behind a
per-case `data:` field — `pyoxigraph` is absent in local dev (oxi.py lines 15-18
already guard `oxi is None`), present only in the CI `[dev]` extra.

**`write_report()`** — write JSON + Markdown under `REPORTS_DIR` (gitignored, see
Shared Patterns). No in-repo report-writer analog exists; keep it minimal: dump the
`Report` as JSON (pass_rate, per-case verdicts) and render a small Markdown table.
Return `(json_path, md_path)` per the stub signature.

---

### `tests/nl2sparql/eval/test_eval.py` (NEW — the `@pytest.mark.eval` gate)

**Analog:** `tests/nl2sparql/test_pipeline.py` (module structure, ScriptedLLMClient
posture) + `tests/nl2sparql/test_samples.py` (imports from
`arango_sparql.nl2sparql`).

**Marker + RUN_EVAL skip** — the `eval` marker is declared in `pyproject.toml`
line 68 (`"eval: NL->SPARQL evaluation harness; slow, gated behind RUN_EVAL=1"`).
Gate the test so local `pytest` stays fast:
```python
import os, json, pytest
from tests.nl2sparql.eval.runner import run, EVAL_DIR

pytestmark = pytest.mark.eval

@pytest.mark.skipif(not os.getenv("RUN_EVAL"), reason="set RUN_EVAL=1 to run the NL eval gate")
def test_scripted_pass_rate_meets_baseline():
    report = run("scripted")
    baseline = json.loads((EVAL_DIR / "baseline.json").read_text())["configs"]["scripted"]
    assert report.pass_rate >= baseline["pass_rate"] - 1e-9
    # optional per-case regression: any case true-in-baseline now false → fail
```

**Import surface** — mirror `test_pipeline.py` lines 26-31 / `test_samples.py`
line 14: import `NlPipeline`, `ScriptedLLMClient`, `LLMResponse` from the package
root `arango_sparql.nl2sparql` (these are re-exported; RESEARCH.md confirms
`nl2sparql/__init__.py` exports them). Do NOT import a `providers`/`ScriptedProvider`
module — it does not exist.

---

### `tests/nl2sparql/eval/corpus.yml` (NEW)

**Analog:** `tests/translate/bgp_select.yml` — same top-level `ontology: |` (Turtle
literal block) + `cases:` list-of-mappings shape. Reuse its exact ontology header
(lines 7-17: `@prefix :`, `owl:`, `rdfs:`, `phys:` with `phys:collectionName`) so
`SchemaResolver.from_turtle` resolves. The golden fixture uses per-case keys
`name` + `sparql` + `expected_aql`; the corpus swaps `sparql`→`nl` and
`expected_aql`→`expected` (gold SPARQL), and adds optional `scripted:`, per-case
`ontology:`, `params:`, `data:`.

Per-case field → real call mapping (all VERIFIED this session):
- `nl` → `pipeline.run(nl)` (pipeline.py line 93)
- `expected` → judge target (`_judge_canonical`)
- `scripted` (optional) → `ScriptedLLMClient` canned content; omit → default to `expected`
- `ontology` (optional per-case) / shared → `SchemaResolver.from_turtle` + `ontology_ttl`
- `params` (optional) → `pipeline.run(..., params=)` (pipeline.py line 97)
- `data` (optional Turtle) → `load_store_from_string` (oxi.py line 31), enables execution tier

**Author ≥1 deliberate near-miss** (a `scripted:` semantically ≠ `expected:`) so the
scripted pass-rate is intentionally < 1.0 — otherwise the gate proves nothing
(RESEARCH Pitfall 5).

---

### `tests/nl2sparql/eval/configs.yml` (NEW)

**Analog:** weak — no config-shaped YAML exists in the repo. Closest is
`bgp_select.yml`'s top-level keyed mapping. Structure per RESEARCH (A2): a top-level
`configs:` map keyed by config name; each has `provider: {type: ...}`,
`judge: canonical|execution`, `max_repairs: int`. The `scripted` config is the CI
default (`provider: {type: scripted}`, no network). Provider `type` values map to
the factory branches (`scripted`→`ScriptedLLMClient`; `openai`/`openrouter`→
`OpenAICompatibleClient`; `anthropic`→`AnthropicClient` — all in client.py). The
`run()` loader reads `configs[config_name]`.

---

### `.github/workflows/ci.yml` (MODIFY — add eval job/step)

**Analog:** the existing `test` job in the SAME file (lines 38-56). Copy its shape:
`runs-on: ubuntu-latest`, `actions/checkout@v4`, `actions/setup-python@v5`,
`pip install -e ".[dev,nl,service]"` (line 50). The one delta: set `RUN_EVAL=1` and
run `pytest -m eval` (invert the existing exclusion on line 56
`-m "not integration and not w3c and not eval"`):
```yaml
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev,nl,service]"
      - run: RUN_EVAL=1 pytest -m eval --tb=short -q
```
RESEARCH Pitfall 3: without this NEW job the eval marker is never selected (the
`test` job excludes it AND it is `RUN_EVAL`-gated), so the success criterion
"eval marker green in CI" is silently unmet. Scripted-only in CI — no API keys.

## Shared Patterns

### YAML loading (safe)
**Source:** `tests/translate/test_translate_bgp_select_goldens.py` lines 26-27
**Apply to:** `runner.py` loaders for both `corpus.yml` and `configs.yml`
```python
import yaml
data = yaml.safe_load(PATH.read_text())   # never yaml.load (V5 input-validation / RCE)
```

### ScriptedLLMClient injection (no network)
**Source:** `tests/nl2sparql/test_pipeline.py` lines 73-75, 94-95; client.py lines 347-392
**Apply to:** `runner.py` scripted-provider factory + `test_eval.py`
- Wrap gold/scripted SPARQL in a ```` ```sparql ```` fence (`_wrap`).
- Fresh client per case (queue-replay hazard, client.py lines 385-388).
- `client.calls` records messages for assertions (client.py line 376) — a scripted
  run must record calls but fire NO HTTP.

### rdflib canonical comparison (the judge)
**Source:** `arango_sparql/translate/parser.py` lines 33-78 (`parse_sparql` → `.algebra`)
**Apply to:** every case's default judge in `runner.py`
- Mandated parser (CLAUDE.md hard rule 1); guarantees judge and transpiler agree on
  "parseable". Catch `SparqlParseError` (from `arango_sparql.errors`).

### Reports stay gitignored
**Source:** `.gitignore` lines 64-66 (`tests/nl2sparql/eval/reports/`)
**Apply to:** `write_report()` — write only under `REPORTS_DIR`; `baseline.json`
lives one level up (in `EVAL_DIR`) and IS committed as the gate.

### pyoxigraph optional / lazy
**Source:** `tests/helpers/oxi.py` lines 15-18 (guards `oxi is None`)
**Apply to:** the optional execution tier in `runner.py` — lazy-import
`tests.helpers.oxi`; absent locally, present in CI `[dev]`.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `tests/nl2sparql/eval/baseline.json` | config/fixture | file-I/O | No checked-in JSON regression-baseline exists anywhere in the repo. `tests/schema/fixtures/*.export.json` are ArangoDB schema exports — unrelated shape/purpose. Planner should follow RESEARCH.md's proposed schema (aggregate `pass_rate`/`passed`/`total` + per-case `cases: {name: bool}` under `configs.scripted`), confirming with the user whether per-case regression is a hard gate (Open Question 2). The initial `pass_rate` must be authored to match the scripted corpus (< 1.0 given the deliberate near-miss). |
| `tests/nl2sparql/eval/configs.yml` | config/fixture | file-I/O | Weak analog only. No config-shaped YAML exists (all YAML in-repo is golden test-data with `ontology:`/`cases:`). Follow RESEARCH A2 schema; internal to the harness so low risk. |
| `write_report()` body | utility | file-I/O | No report-writer analog in-repo. Keep minimal JSON+Markdown; the sister-repo mirror is unreachable. |

## Metadata

**Analog search scope:** `tests/nl2sparql/`, `tests/translate/`, `tests/helpers/`,
`tests/schema/fixtures/`, `arango_sparql/nl2sparql/`, `arango_sparql/translate/`,
`.github/workflows/`, `pyproject.toml`, `.gitignore`
**Files scanned:** ~15 (6 read in full/targeted, plus grep across fixtures/config)
**Pattern extraction date:** 2026-07-15

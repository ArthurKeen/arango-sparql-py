# NL→SPARQL eval harness — reproducibility runbook

This directory holds the NL→SPARQL evaluation harness. It has two distinct
paths, and keeping them separate is the whole point of this runbook:

- **`scripted`** — the **no-network, key-free CI default**. Deterministic,
  gates `baseline.json`, never touches a provider. This is what CI runs.
- **`openai-gpt4o-mini`** — the **credentials-gated live-model baseline**.
  Run manually / nightly, out of band, with a real key in the environment.
  Its numbers are hand-folded into `baseline.json` after a human review.

The live baseline is the *measurable floor* Phase 7's few-shot lift is
proven against, so it must be reproducible from the documented steps below
(model + temperature + corpus revision), not a one-off number.

Files:

| File | Role |
|------|------|
| `corpus.yml` | Gold NL→SPARQL cases (positives + `expect_refusal` negatives). |
| `configs.yml` | Provider/judge configs (`scripted`, `openai-gpt4o-mini`). |
| `runner.py` | `run()`, `write_report()`, the canonical-algebra judge. |
| `baseline.json` | The **only** checked-in report artifact — the regression gate. |
| `test_eval.py` | The `@pytest.mark.eval` gate (behind `RUN_EVAL=1`). |
| `reports/` | **Gitignored** `write_report()` output (raw per-case JSON + Markdown). |

---

## 1. Setup

Install the repo plus the `nl` extra (rule 100 — use `uv`, not bare `pip`):

```bash
uv sync --extra nl        # pins arango-query-core (git ref), rdflib, rank_bm25
# or:  pip install -e '.[nl]'
```

---

## 2. The key-free CI default (scripted — no network)

This is what CI runs and what you should run before every commit. It needs
**no API key** and makes **no network call**:

```bash
RUN_EVAL=1 pytest -m eval -q
```

It runs `run("scripted")` against `baseline.json`: the aggregate pass_rate
must not regress, no baseline-passing case may regress, and every new corpus
case must pass before it is added to `baseline.json`. The scripted pass-rate
tests the **judge**, not the model — do not read it as model accuracy.

---

## 3. The credentials-gated LIVE sweep (openai-gpt4o-mini)

> **Pitfall 1 — the runner's live path reads `NL2SPARQL_API_KEY`, NOT
> `OPENAI_API_KEY`.** `runner._client_for` builds `OpenAICompatibleClient`
> with no explicit key, so it falls to `os.getenv("NL2SPARQL_API_KEY", "")`.
> The `OPENAI_API_KEY` fallback only exists in `get_default_client()`, which
> the runner does **not** use. A missing/blank `NL2SPARQL_API_KEY` posts an
> empty bearer and **401s loudly — it does not fall back or silently degrade.**

Export the key **into this shell only** (never commit it, never paste it back
into any file or chat):

```bash
export NL2SPARQL_API_KEY=sk-...          # your OpenAI key — this shell only
export NL2SPARQL_MODEL=gpt-4o-mini       # optional; configs.yml already pins the model
```

Capture the corpus revision you are measuring against (the `corpus_sha` to
pin into the baseline — a pass-rate without a corpus revision is meaningless):

```bash
git log -1 --format=%h -- tests/nl2sparql/eval/corpus.yml
```

Run the live sweep. It writes `reports/openai-gpt4o-mini.{json,md}` (both
**gitignored**) and prints the aggregate + per-case verdicts:

```bash
RUN_EVAL=1 NL2SPARQL_API_KEY=... python -c "from tests.nl2sparql.eval.runner import run, write_report; r=run('openai-gpt4o-mini'); write_report(r); print('pass_rate', r.pass_rate); [print(c.name, c.passed) for c in r.cases]"
```

Cost/latency magnitude: gpt-4o-mini at ≈$0.00015/1k input + $0.0006/1k
output, with `max_repairs=2` (up to 3 calls/case) over a ~25-case corpus, a
full sweep is **≈ 1–3 US cents and seconds-scale**. Cost is a non-issue; the
value is the reproducible number.

---

## 4. Headroom check (do this before promoting anything)

The printed `pass_rate` **must be meaningfully < 1.0**. Headroom is only
observable on the **live** config (the scripted rate tests the judge, not the
model — Pitfall 5). If the live run is at/near ceiling, the corpus lacks
headroom: a Phase-7 few-shot lift would be unmeasurable (Critical Failure
Mode 2). **Do not promote a near-ceiling live baseline** — harden the corpus
first, then re-sweep.

---

## 5. The MANUAL, human-reviewed fold-in into `baseline.json`

> **Pitfall 2 — `write_report()`'s schema ≠ `baseline.json`'s schema.**
> `write_report` emits a **flat** shape `{config, pass_rate, cases:[{name,
> passed, elapsed_ms}]}` into gitignored `reports/`. `baseline.json` is the
> **nested** regression gate `{configs: {name: {...}}}`. Promoting live
> numbers is therefore a **manual, human-reviewed** copy — the same
> discipline as goldens. **CI never auto-regenerates `baseline.json`.**

By hand, add a sibling `configs['openai-gpt4o-mini']` entry to
`baseline.json` (do **not** touch the `scripted` entry). Copy only the
aggregate `pass_rate` / `passed` / `total` and the per-case `{name: passed}`
verdicts from the run, and **add** the three reproducibility fields:

```json
{
  "configs": {
    "scripted": { "...": "unchanged" },
    "openai-gpt4o-mini": {
      "pass_rate": 0.xx,
      "passed": NN,
      "total": 25,
      "cases": { "people-with-names": true, "...": false },
      "model": "gpt-4o-mini",
      "temperature": 0.1,
      "corpus_sha": "<the SHA from step 3>"
    }
  }
}
```

- `model: "gpt-4o-mini"` and `temperature: 0.1` — `temperature` is hardcoded
  in `OpenAICompatibleClient` and `configs.yml` cannot override it today.
  gpt-4o-mini is **not** bit-deterministic even at low temperature
  (Pitfall 6), so recording model + temperature + `corpus_sha` is what makes
  the number interpretable on re-run.
- `corpus_sha` — the `git log` short SHA from step 3.

The entry is validated no-network by `BaselineConfig` (see
`test_live_baseline_companion_structural` in `test_eval.py`), which also
asserts `0.0 < pass_rate < 1.0` (headroom).

---

## 6. Discipline callouts (secret & payload hygiene)

- **NEVER commit a key or bearer token** to `corpus.yml`, `configs.yml`,
  `baseline.json`, this README, or anywhere else. Keys live only in the
  `NL2SPARQL_*` environment variables.
- **NEVER commit raw prompts/completions.** They embed the full ontology and
  could embed a mistakenly-pasted key. They stay in gitignored `reports/`.
  Only the aggregate numbers + model/temperature/corpus_sha cross into
  `baseline.json`.
- **`scripted` stays the CI default.** The live provider is reachable *only*
  via the non-`scripted` config, behind `RUN_EVAL=1` + a key, run manually.
  The default test path never hits the network (rule 200).
- **Never auto-regenerate `baseline.json` in CI** — the fold-in is always a
  reviewed human step.

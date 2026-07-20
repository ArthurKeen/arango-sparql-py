# Context Brief — Improve NL→Conceptual (NL→SPARQL) to SOTA

> **Purpose.** Seed context for the GSD phase(s) that improve the natural-language →
> conceptual-query (SPARQL) step to state of the art. Consumed by
> `gsd-phase-researcher` → `gsd-spec-phase` / `gsd-ai-integration-phase` →
> `gsd-plan-phase`. Owner: **PJ**. Directive from **Arthur**: *research the SOTA for
> NL→SPARQL, turn that research into a spec, then build to the spec — this guarantees
> it beats current Arango capability.* Report cadence: detail in this workstream,
> periodic results to the shared channel.

---

## 1. What this task is
Turn an English question into a **conceptual query serialized as SPARQL** — the
intermediate representation the Contextual Data Fabric (CDF) decomposes and routes.
"To SOTA" = measurably better than the current bootstrap NL→SPARQL layer, proven by a
**pass-rate delta over a real baseline**, without ever regressing the deterministic
SPARQL→AQL transpiler (this repo's sacred invariant).

## 2. Where it sits in CDF (the whole)
CDF is an **OBDA / Virtual Knowledge Graph** (`P = (O, M, S)`): an aligned master
ontology `O`, declarative mappings `M` (CSI → R2RML / OWL-Turtle), physical sources
`S`. Two building blocks — *Onto Extract* (build the ontology, build-time) and *Query*
(federate over it, live-path).

```
LIVE-PATH (the /federate endpoint):
  English question
     │  ◄── THIS TASK: NL → conceptual query (SPARQL IR)   [M5 WP D1/D2]
     ▼
  SPARQL IR
     │  M5 partition planner — split query graph by source
     ├─► relational leg: SPARQL→SQL via Ontop (R2RML mappings)
     └─► Arango leg:    SPARQL→AQL via this repo (arango-sparql-py)
     │  M6 — join partial results on AER canonical entity keys (e.g. account_id)
     ▼  M7 — grounded, cited envelope (cite the exact query, or refuse)
  answer
```

This task is the **front door of the live path**. If the SPARQL IR is wrong, the whole
downstream pipeline faithfully returns a wrong (but cited) answer — hence high leverage,
hence eval-gated. It maps to CDF **M5 work packages D1 (NL front-end) + D2 (eval)**, and
its eval harness seeds **M10 (Evaluation)**.

## 3. Current state (verified against code, 2026-07-20)

### 3a. Shared engine — `arango-query-core`
- A shared, **language-agnostic** NL→query engine carved out of `arango-cypher-py` so
  both transpilers pin one artifact. Repos: `ArthurKeen/arango-query-core` (public) and
  `arango-solutions/arango-query-core` (private mirror) — **byte-identical**, on PyPI
  v0.1.0.
- `arango_query_core.nl.engine.NLQueryEngine` = the **generate → validate → repair**
  loop + token accounting. Deliberately **minimal**: nl2cypher's richer stages (entity
  resolution, tenant prompt sections, Anthropic cache-splitting) have **not** migrated
  yet — they arrive "incrementally as the re-point lands."
- `arango_query_core.nl.seams.QueryLanguageAdapter` = the **five language-specific
  seams**: (1) grammar prompt, (2) few-shot corpus, (3) validator, (4) repair rules,
  (5) guardrails.

### 3b. SPARQL adapter — `arango_sparql/nl2sparql/` (THIS repo)
- **NOT yet re-pointed onto the shared engine** — it does not import `arango_query_core`
  or implement `QueryLanguageAdapter`. It is still a standalone **bootstrap** pipeline
  (`NlPipeline.run()`: `PromptBuilder → LLMClient → RepairLoop → api.translate()`),
  explicitly a "mirror of `arango_cypher.nl2cypher` adapted for SPARQL."
- **Zero-shot today.** `prompt.py` states BM25 few-shot, tenant guardrails, and
  entity-resolution hooks are deliberately out of scope for the bootstrap.

Seam status in `nl2sparql` today:

| Seam | Status |
|------|--------|
| 1. grammar prompt | ✅ exists (`_SYSTEM_PROMPT` + `PromptBuilder`) |
| 2. **few-shot corpus** | ❌ missing (zero-shot; `_FEWSHOT_LIMIT=3` reserved, unused) |
| 3. validator | ✅ exists (deterministic transpiler = validate; `E_SPARQL_*` codes) |
| 4. repair rules | ✅ exists (`RepairLoop` feeds translator error back to the LLM) |
| 5. **guardrails** | ❌ missing (no tenant/write-op checks) |

### 3c. Eval harness — `tests/nl2sparql/eval/` (GSD Phase 06, complete)
- `corpus.yml` — **6 toy cases** on a tiny ontology (Person/name/age/Order); all
  single-concept BGPs; includes one deliberate near-miss. **Not an accuracy benchmark.**
- `runner.py` — judge is **rdflib canonical-algebra comparison** (semantic, not string
  match) + an `aql != ''` accept signal.
- `configs.yml` — a `scripted` (no-network CI default) config **and** an
  `openai-gpt4o-mini` real-provider config.
- `baseline.json` — **scripted-only**, 5/6 (0.833); near-miss keeps it < 1.0 as a
  regression gate. **No live-model baseline has ever been captured.**

### 3d. Roadmap state (`.planning/ROADMAP.md`)
- Phases 1–3 + 6 complete. **Phase 7 already exists** = "NL→SPARQL few-shot index (BM25
  ≤3-shot feeding PromptBuilder, prove pass-rate lift)" — but it (a) assumes the
  *standalone* PromptBuilder, not the shared engine, and (b) is scoped narrower than
  "to SOTA." **No re-point phase exists.**

## 4. Shared engine vs. adapter (where changes land)
- **Adapter-side (`arango_sparql/nl2sparql`, SPARQL-only, low coordination):** the
  missing seams — few-shot corpus (2), guardrails (5) — plus grammar-prompt/corpus
  tuning. *Most near-term SOTA leverage is here.*
- **Engine-side (`arango-query-core`, shared, higher blast radius — helps Cypher too,
  coordinate with Arthur):** the unmigrated loop features (entity resolution, Anthropic
  cache-splitting), and any change to the generate→validate→repair loop itself.

## 5. Recommended phasing (amend the roadmap via `gsd-phase`, do not restructure)
1. **Re-point + real baseline** (new phase, insert before few-shot): wrap `nl2sparql` as
   a `QueryLanguageAdapter` on the shared engine (CDF WP D1); grow `corpus.yml` to real
   difficulty (OPTIONAL, aggregation, property paths, multi-hop, negative cases); run the
   `openai` config to capture a **genuine live-model baseline**.
2. **SOTA** (re-scope existing Phase 7 / add a phase): per Arthur — research SOTA →
   spec → build. Adapter-side gains first (few-shot/BM25 = seam 2, prompt/corpus, guardrails
   = seam 5), then escalate to engine-side features with Arthur if the numbers justify it.
   Every change gated by a **positive delta over the phase-1 baseline**.

## 6. Method (Arthur's, mapped to GSD)
- **Research SOTA for NL→SPARQL** → `gsd-phase-researcher` (and/or the `deep-research`
  skill / `research-prompt` for the survey). Cover: frame-IR / FRASE-style typed
  intermediate, few-shot exemplar selection, EXPLAIN/translate-grounded self-healing
  retry, schema-linking, constrained decoding, LLM-as-judge eval.
- **Turn research into a spec** → `gsd-spec-phase` (WHAT) or `gsd-ai-integration-phase`
  (AI-SPEC.md with eval strategy baked in — preferred, since this is an LLM system).
- Optionally `grill-me` the spec before executing.
- Phase 06's own RESEARCH.md (~40KB) already surveyed this space — harvest it first.

## 7. Cross-repo dependencies & risks
- **Dev from THIS repo** (`arango-sparql-py`): nl2sparql, the eval harness, corpus,
  baseline, and this GSD pipeline all live here. Install the shared engine editable
  (`pip install -e ../arango-query-core`) so engine edits flow when needed.
- **Seam-API stability risk:** `arango-query-core` publishes 0.1.0 "once the adapter API
  survives its first consumer — the `nl2cypher` re-point." **Cypher is the first
  consumer; SPARQL is the second.** Confirm the seam API has settled (via the Cypher
  re-point) before re-pointing SPARQL, or you re-point against a moving target.

## 8. Invariants / constraints
- **Never regress the deterministic SPARQL→AQL transpiler** (repo Core Value; W3C DAWG
  query-eval ~96.4%).
- **Eval-gated:** every claimed improvement = a measured pass-rate delta over
  `baseline.json`, judged by **canonical algebra** (never string match).
- `scripted` config stays the **no-network CI default**; live-provider runs are gated
  (`RUN_EVAL=1`) and must not require credentials in the repo.

## 9. Open questions to confirm with Arthur
1. **Ownership:** is the D1 re-point PJ's or Arthur's? Does SOTA work land in the
   **adapter** (this repo's roadmap) or the **shared engine** (query-core's own plan)?
2. **Seam-API stability:** has the `nl2cypher` re-point settled the `QueryLanguageAdapter`
   contract yet?
3. **Corpus authorship:** who authors the real (harder) eval corpus + gold SPARQL?

## 10. Key file pointers
- Shared engine: `arango-query-core/arango_query_core/nl/{engine,fewshot,providers,seams}.py`, `mapping.py`
- SPARQL adapter (to re-point): `arango_sparql/nl2sparql/{pipeline,prompt,client,repair,cost,models,samples}.py`
- Eval: `tests/nl2sparql/eval/{corpus.yml,configs.yml,runner.py,baseline.json}` + `reports/`
- Prior research: `.planning/phases/06-nl-sparql-eval-harness-seed-corpus/06-RESEARCH.md`
- CDF context: contextual-data-fabric `docs/architecture/module-05-federated-query-engine/` (ADR-0001, implementation-plan WP D1/D2)

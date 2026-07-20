# NL→SPARQL — State-of-the-Art Survey

> Deep-research report for improving the NL→SPARQL (natural-language → conceptual-query) layer.
> Method: fan-out web search (6 angles) → fetch 27 sources → adversarial 3-vote verification (25 claims → **24 confirmed, 1 refuted**) → synthesis.
> Generated 2026-07-20. Judge model of record for our eval: rdflib **canonical-algebra** equivalence (not string match). Sacred invariant: never regress the deterministic SPARQL→AQL transpiler.

---

## TL;DR — what beats a zero-shot bootstrap, ranked by (evidence × lift ÷ cost)

| # | Technique | Side | Lift (as reported) | Cost on our stack | Deterministic eval? | Confidence |
|---|-----------|------|--------------------|-------------------|---------------------|------------|
| 1 | **Dense/embedding few-shot retrieval** (replace/augment BM25 with a sentence-transformer index) | **Adapter** | up to **+21 F1** (LC-QuAD 2.0/QALD-9); beats fine-tuned SOTA on 3/4 KGQA | **Low** — we already have `FewShotIndex`; swap retriever | ✅ yes | **High** |
| 2 | **Coverage/diversity SET selection** (BSR / Set-BSR — pick a set that *covers* needed structures) | **Adapter** | +8–17 pts over cosine; beats trained selectors | **Medium** — add set-cover/BSR scorer | ✅ yes | Medium (transfer) |
| 3 | **Decoupled IRI resolution** (PGMR — LLM emits NL placeholders, memory module substitutes real IRIs) | Adapter/Engine | **~0% URI hallucination** (LC-QuAD 2.0) | Low–Med — placeholder prompt + deterministic label→IRI lookup | ✅ substitution is deterministic | **High** |
| 4 | **Skeleton/structure-based retrieval** (retrieve by de-semanticized question shape) | **Adapter** | qualitative; augments #1 | Medium — skeleton extractor + structural index | ✅ yes | Medium (text-to-SQL transfer) |
| 5 | **Agentic schema exploration** (SPINACH — iteratively explore ontology, build query like an expert) | **Engine** | **+38.1 F1** over best GPT-4 agent; SOTA QALD-7/9+/10 | Low–Med code; **needs a live model** | ❌ needs live model | **High** |
| 6 | **Frame-semantic IR** (FRASE — annotate question with FrameNet frames before generating) | Adapter\* | +11 acc / +15 F1 on **unseen-template** split | Higher — frame-SRL stage; \*gains measured under fine-tuning | frame step deterministic; gain not | High (result) / caveat (transfer) |
| 7 | **Unified compilable IR** (GraphQ IR — generate an IR, deterministically compile IR→SPARQL) | **Engine** | up to **+11%**, 91.7% EM (KQA Pro) | **High** one-time (build IR→SPARQL compiler) | ✅ compiler deterministic | High |
| 8 | **Query-graph / BGP-path reasoning** (TrackerQA — reason KB paths, then generate) | **Engine** | SOTA KQA Pro **95.32%** | **High** — needs trained GNN | needs trained model | High (take the *principle*) |
| — | **Grammar-constrained decoding** (GBNF/PICARD) | Engine | forces valid syntax | **Incompatible with API LLMs** (local-model-only) | n/a | High — **not usable for us** |

\* FRASE's numbers were obtained under QLoRA fine-tuning, so the exact lift is not promised for a zero-shot/adapter prompt-augmentation setup.

**Headline recommendation:** the fastest, best-evidenced, lowest-risk win is **#1 dense few-shot retrieval**, and it drops straight into our existing `FewShotIndex` (adapter-side, deterministic, we can measure it against `baseline.json` with the scripted provider). **#3 PGMR** (kill IRI hallucination) and **#2 Set-BSR** (structural coverage) are the natural next two, also adapter-side. Engine-side IR work (#7 GraphQ IR) is the biggest bet and aligns with our deterministic-transpiler ethos, but it's a large one-time build — defer until the cheap adapter wins are banked.

---

## Corroborating baseline: how big is the few-shot lift?

One fetched source (Spider4SPARQL-adjacent study) measured **GPT-3.5 at 8% zero-shot → 45% execution accuracy with 10-shot** in-context examples — a ~37-point swing from few-shot alone. This is the single strongest argument for prioritizing the few-shot family (#1/#2/#4) over everything else: the bootstrap is leaving most of its accuracy on the table simply by running zero-shot.

---

## Per-sub-question findings

### 1. Typed / frame intermediate representations
Three independent confirmations that an explicit IR beats direct SPARQL generation — **with gains concentrated exactly where a bare generator is weakest** (compositional / out-of-distribution / unseen structures):
- **FRASE** (frame-semantic annotation): +11 acc / +15 F1 on the Unknown-Template split; frames alone ~+22% on reformulated questions. [arXiv 2503.22144]
- **GraphQ IR** (unified compilable IR → SPARQL/Cypher/KoPL): up to +11%, **91.70% EM on KQA Pro** (which covers SPARQL). Largest lifts on OOD/low-resource. [arXiv 2205.12078, EMNLP'22]
- **TrackerQA** (self-supervised BGP-path reasoning → SPARQL): SOTA **95.32% on KQA Pro**. Motivation: direct generative SPARQL struggles to encode implicit logic in SPARQL syntax. [IP&M 2024]

**Verdict:** worth it, but the IR stage is engine-side and the biggest of the builds. GraphQ IR's "generate IR, deterministically compile to SPARQL" pattern is philosophically aligned with our deterministic transpiler. TrackerQA/FRASE need trained models — take the *design principle* (structure-before-syntax), not the implementation.

### 2. Few-shot exemplar selection beyond BM25
- **Dense retrieval (DFSL)**, all-mpnet-base-v2, cosine over question+entities+relations: **+21 F1** vs static few-shot; fine-tuning-free DFSL-MQ beats task-specific SOTA on 3/4 KGQA benchmarks. [KnowledgeNLP'25 / arXiv 2407.01409]
- **Coverage/diversity SET selection (BSR / Set-BSR)**: selecting a *set* that collectively covers needed output structures beats ranking examples independently — BSR +up to 8 pts on semantic parsing, Set-BSR +17% avg (up to 49% on splits), beating trained selectors EPR/CEIL. [ACL'23 2212.06800; EMNLP-F'23 2305.14907]
- **Skeleton/structure retrieval**: de-semanticize the question and retrieve by structural similarity; best used to *augment* dense retrieval (DAIL-SQL finds question+query hybrid wins). [NLPCC'23]

**Verdict:** this is the highest lift-per-cost family and all of it is adapter-side + deterministically evaluable. Ship dense retrieval first; layer Set-BSR coverage on top.

### 3. Schema-linking / ontology grounding
- **SPINACH** (agentic in-context schema exploration): dynamically explores large/incomplete schemas and builds SPARQL like a human expert — **+38.1 F1** over the best GPT-4 KBQA agent, SOTA on QALD-7/9+/10. Engine-side loop; composes with our generate→validate→repair; needs a live model. [EMNLP-F'24 2407.11417]
- **PGMR** (post-generation memory retrieval): LLM emits SPARQL with **NL placeholders for URIs**, then a non-parametric memory module retrieves + substitutes the correct ontology IRIs → **~0% URI hallucination**. The label→IRI lookup is deterministic and directly attacks our most likely failure mode (invented prefixes/IRIs). [arXiv 2502.13369, Feb 2025]

**Verdict:** PGMR is the cleanest, cheapest hallucination fix and synergizes with our translate-grounded validator. SPINACH is the higher-ceiling option once we're running a live model.

### 4. Self-healing / validator-grounded repair
**GAP (unmeasured in surviving evidence).** No confirmed claim quantified how much translate-grounded error-feedback retry lifts pass-rate, the optimal retry budget, or loop-guarding — even though our deterministic transpiler + rdflib validator *uniquely* enables it. One related data point (SchemaForge) attributed ~9.27 EA points to a two-stage verifier whose symbolic compile-check is analogous to our parse-or-fail gate. **This is greenfield we're positioned to measure and contribute** — keep the repair loop (see refuted item below) and instrument it.

### 5. Constrained / grammar-guided decoding
**GBNF constrained decoding is engine-side and local-model-only** (llama.cpp capability) — **not available through API LLMs**, so it's incompatible with an API-model bootstrap. [arXiv 2512.00948] The stronger claim that constrained decoding *removes the need for the repair loop* was **REFUTED** (see below).

### 6. Evaluation methodology
**GAP.** No surviving evidence directly compared canonical-algebra semantic-equivalence judging against execution-accuracy or LLM-as-judge, or catalogued their pitfalls. Our rdflib canonical-algebra choice is defensible but not externally benchmarked in this survey. (Note: Spider4SPARQL reports SOTA hitting ~92% on LC-QuAD 2.0 but only ~45% execution accuracy on harder queries — a reminder that easy benchmarks saturate and execution-graded difficulty matters when we harden our corpus.)

### 7. Benchmarks / datasets to seed a harder corpus
Primary sources surfaced: **QALD-9/10**, **LC-QuAD 2.0**, **KQA Pro** (SPARQL-covering, near-ceiling on i.i.d.), **Spider4SPARQL** (hard, execution-graded), and the **text2sparql.aksw.org 2025 challenge**. All are Wikidata/DBpedia KGQA, **not** an OWL/Turtle conceptual schema with no physical mapping — so use them to *seed difficulty patterns* (OPTIONAL, aggregation, property paths, multi-hop, negatives), not as drop-in corpora.

---

## Refuted (did NOT survive verification)
- **"GBNF two-step constrained generation eliminates the need for a validator-error feedback / repair loop"** — vote **1–2, refuted**. Takeaway: **keep the repair loop even if constrained decoding is ever added.** [arXiv 2512.00948]

## Open questions / caveats carried forward
1. **Domain transfer:** the strongest "beyond-BM25" selection results (Set-BSR, skeleton) are measured on text-to-SQL / semantic parsing, not NL→SPARQL — mechanism transfers, magnitudes may not.
2. **Schema transfer:** all schema-linking numbers (SPINACH, PGMR) are on Wikidata/DBpedia, not an OWL/Turtle conceptual ontology — treat as directional.
3. **"Up to" maxima:** DFSL's +21 F1 and GraphQ IR's +11% are best-case splits, not typical i.i.d. gains.
4. **FRASE gains assume fine-tuning** — not a proven zero-shot/adapter lift.
5. **Unmeasured for us (opportunity):** validator-feedback retry budget (Q4) and canonical-algebra vs execution vs LLM-judge (Q6) — both are things our stack can measure that the literature hasn't.
6. How do dense + Set-BSR + skeleton retrieval *combine* on a SPARQL corpus — compound or plateau?
7. Do IR gains hold on a conceptual OWL/Turtle ontology, and can an IR→SPARQL compiler be built without conflicting with the deterministic SPARQL→AQL transpiler?

---

## How this maps to our stack (adapter vs engine)
- **Adapter-side (this repo's `nl2sparql` shim — fast, deterministic, measurable now):** dense few-shot retrieval (#1), Set-BSR coverage (#2), skeleton retrieval (#4), PGMR placeholder→IRI resolution (#3), populating a real exemplar corpus.
- **Engine-side (shared `arango_query_core.nl` — bigger bets, benefit Cypher too):** SPINACH-style agentic exploration (#5), GraphQ-IR intermediate + compiler (#7), and *instrumenting the repair loop* (Q4 gap) which lives in `NLQueryEngine`.
- **Not usable:** GBNF constrained decoding (API-model incompatible).

## Sources (primary unless noted)
Dense few-shot: aclanthology.org/2025.knowledgenlp-1.5.pdf · arxiv.org/pdf/2407.01409 — Set/coverage: arxiv.org/abs/2212.06800 · arxiv.org/html/2305.14907 — Skeleton: link.springer.com/chapter/10.1007/978-981-99-7022-3_23 — FRASE: arxiv.org/html/2503.22144v1 — GraphQ IR: arxiv.org/pdf/2205.12078 — TrackerQA: sciencedirect.com/science/article/abs/pii/S0306457324001614 — SPINACH: aclanthology.org/2024.findings-emnlp.938 — PGMR: arxiv.org/html/2502.13369 — GBNF: arxiv.org/pdf/2512.00948 — Benchmarks/eval: arxiv.org/pdf/2309.16248 (Spider4SPARQL) · text2sparql.aksw.org/2025/challenge — IR taxonomy (vocabulary only, no experiments): arxiv.org/html/2604.10776

_Stats: 6 angles · 27 sources fetched · 119 claims extracted · 25 verified · 24 confirmed / 1 refuted · 110 agent calls._

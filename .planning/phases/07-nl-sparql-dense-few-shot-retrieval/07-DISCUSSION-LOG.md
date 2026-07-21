# Phase 7: NL→SPARQL dense few-shot retrieval - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-21
**Phase:** 7-NL→SPARQL dense few-shot retrieval
**Areas discussed:** Example-bank source, Embedding backend, Fallback & packaging, Lift-measurement design

---

## Example-bank source (leakage boundary)

| Option | Description | Selected |
|--------|-------------|----------|
| Separate curated bank | Dedicated few-shot bank disjoint from eval corpus, same difficulty classes | ✓ |
| Leave-one-out on eval | Pool = eval corpus minus scored query; reuses golds, academic setup | |
| Both: bank + LOO check | Curated bank shipped + LOO sanity cross-check | |

**User's choice:** Separate curated bank.
**Follow-up — disjointness guard:**

| Option | Description | Selected |
|--------|-------------|----------|
| CI test enforces disjointness | Committed test: bank ∩ eval = ∅ by normalized question AND canonical algebra (paraphrase-proof) | ✓ |
| Convention only | Separate file + README rule, no enforcing test | |
| You decide | Pick guard at planning | |

**Notes:** Disjointness must be structurally impossible, not just intended. Two-way check (question text + canonical algebra) so paraphrases/re-spelled golds can't leak.

---

## Embedding backend

| Option | Description | Selected |
|--------|-------------|----------|
| Local sentence-transformers | all-MiniLM-L6-v2 local; reproducible offline; pulls torch; loaded only in gated dense path | ✓ |
| API embeddings | OpenAI text-embedding-3-small; network+key; version drift | |
| Pluggable, default local | Embedder seam, ship local as default | |

**User's choice:** Local sentence-transformers.
**Follow-up — reproducibility:**

| Option | Description | Selected |
|--------|-------------|----------|
| Pin name + revision | Pin model name + HF revision + ST version; record in dense baseline next to corpus_sha | ✓ |
| Pin name only | Name + version range, revision floats | |
| You decide | Pick granularity at planning | |

**Notes:** Reframed the no-network tension — the dense lift sweep needs the live LLM anyway (compares vs 06.2 live baseline), so it's inherently a gated opt-in run like 06.2; CI's key-free scripted path never imports embeddings.

---

## Fallback & packaging

| Option | Description | Selected |
|--------|-------------|----------|
| dense→BM25→no-op chain | Try dense, fall back to BM25, then no-op | (folded into two-tier) |
| Dense-or-noop | Dense or straight to zero-shot | |
| Hard requirement | Raise ImportError if dense requested and ST missing | ✓ (as explicit-path tier) |

**User's choice:** Leaned "hard requirement" but asked to weigh trade-offs. Resolved to a **two-tier model**: explicit dense request → hard ImportError (measurement integrity); auto path → graceful dense→BM25→no-op (library safety) + a DenseRetriever-type assertion in the eval sweep. Confirmed ("Yes — two-tier + assert").

**Follow-up — packaging:**

| Option | Description | Selected |
|--------|-------------|----------|
| New [dense] extra | Dedicated extra; keeps [nl] torch-free | (Claude's discretion — leaning this) |
| Fold into [nl] | One extra, heavier default footprint | |
| You decide | Pick at planning per existing extras layout | ✓ |

**Notes:** Two-context tension (measurement wants loud failure; shared library wants graceful degrade) resolved by keying behavior on explicitness — mirrors the existing BM25Retriever precedent (`__init__` raises, `from_corpus_files` degrades).

---

## Lift-measurement design

| Option | Description | Selected |
|--------|-------------|----------|
| 3 arms: zero / dense / BM25 | Live config three ways; dense>zero (SC3), dense>BM25 (survey) | ✓ |
| 2 arms: zero vs dense | Satisfies SC3, no ablation | |
| You decide | Pick arms at planning | |

**User's choice:** 3 arms — and asked whether stronger models than gpt-4o-mini could be used ("we have access to better models").

**Follow-up — model matrix:** User clarified OpenAI-only key; wants the gpt-5 family.

| Option | Description | Selected |
|--------|-------------|----------|
| mini + gpt-5-mini + gpt-5 | 3 tiers, each full 3-arm; anchor keeps SC3 valid; tier spread tests lift-vs-model-strength | ✓ |
| mini + gpt-5 only | Two tiers, cheaper | |
| Let me adjust ids | Different ids | |

**Follow-up — nondeterminism:**

| Option | Description | Selected |
|--------|-------------|----------|
| N runs, delta > spread | N=3 runs/arm; lift valid iff dense−zero > max within-arm spread | ✓ |
| Single run, lowest temp | 1× run, cheapest, weaker claim | |
| You decide | Pick at planning | |

**Notes:** Explained the fixed-model constraint (a lift must hold the model constant across arms) and the headroom/ceiling caveat (strong models may saturate zero-shot → null lift, the exact failure mode 06.2 was built to avoid). Tier spread makes ceiling risk a *finding*, not a failure. Flags for planning: confirm exact gpt-5 ids against the OpenAI account; gpt-5 reasoning models may reject/ignore the hardcoded temperature=0.1.

---

## Claude's Discretion

- Packaging shape of the dense dependency (`[dense]` extra vs fold into `[nl]`) — leaning dedicated `[dense]`, pending arango-query-core's existing extras layout.
- Bank size / per-class balance; retrieval offline unit-test fixtures; similarity metric/normalization.

## Deferred Ideas

- Leave-one-out cross-check on the eval corpus (second disjointness signal) — not adopted.
- API embeddings as a pluggable alt-backend — rejected for reproducibility.
- Growing the eval corpus if gpt-5 saturates it — a future corpus phase, not Phase 7.
- Formal pluggable `Embedder` seam — future engine refinement.

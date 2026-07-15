# Proposal: extract a shared NL→query engine (`arango-query-core` NL layer)

Status: **Draft** (2026-07-15). Extends the `arango-query-core` shared-substrate
direction already acknowledged in PRD §12.3 and the v1.1 roadmap
("`arango-query-core` first stable release (consumed by both
`-cypher-py` and `-sparql-py`)").

## Problem — three engines are about to exist

1. `arango-cypher-py/arango_cypher/nl2cypher` — the **mature engine**
   (~7.5 k LOC): LLM provider abstraction, BM25 few-shot index, entity
   resolution, repair loop, cost/telemetry, tenant guardrails.
2. `arango_sparql/nl2sparql` — **scaffolding** (~1.6 k LOC); `_core.py`
   is an explicit stub whose TODO says "port the LLM provider
   abstraction, BM25 fewshot index, and tenant guardrail from
   `arango_cypher.nl2cypher`".
3. `contextual-data-fabric` M5 WP-D1 plans to **harvest** nl2cypher and
   "swap ~5 seams to emit SPARQL" — a third divergent copy.

Porting (2) and harvesting (3) independently forks the same engine
twice. The cheapest moment to extract is now, while (2) is a stub and
(3) has not started.

## Proposal

Promote the engine into the already-planned shared package rather than
minting a new one:

- **Package**: `arango-query-core` (new top-level distribution; today
  it exists only as a second package inside arango-cypher-py's wheel
  carrying `MappingBundle`). Add an `nl` subpackage:
  `arango_query_core.nl`.
- **What moves** (language-agnostic by inspection of nl2cypher):
  provider abstraction (`providers.py`), few-shot index (`fewshot.py`
  minus the Cypher corpus), repair-loop skeleton, cost accounting,
  schema-context summarization, tenant-guardrail *interface*, entity
  resolution.
- **What stays per language** (the ~5 seams WP-D1 already identified):
  1. target-grammar prompt section,
  2. few-shot corpus,
  3. syntax validator (rdflib parse vs. Cypher parser),
  4. repair rules keyed on validator errors,
  5. guardrail AST checks (`tenant_ast_cypher.py` ↔ a SPARQL-algebra
     analog).
- **Adapters**: `arango_cypher.nl2cypher` becomes a thin adapter over
  the core (extraction source, so its behavior is the reference);
  `arango_sparql.nl2sparql` is **implemented directly as the second
  adapter** — no interim port of the engine into this repo.
- **The fabric's WP-D1** consumes `arango-query-core[nl]` + the SPARQL
  adapter instead of forking, and pins it per CC-9 (pinned artifact,
  golden-set re-run on bump).

## Costs / risks

- One more versioned artifact under the fabric's pin-everything
  policy; every engine change is a release + 2–3 pin bumps. Mitigation:
  the core's API surface is the adapter seam list — small and stable.
- Tenant guardrails do not fully extract (AST validators are
  language-specific); the split is interface-in-core,
  implementation-in-adapter.
- Single-maintainer coordination overhead. Fallback (rejected but
  workable): keep `arango_query_core` inside the cypher wheel and have
  this repo depend on the cypher distribution for that package only —
  muddier: the fabric's SPARQL leg would transitively depend on the
  Cypher repo.

## Sequencing

1. Carve `arango-query-core` out of arango-cypher-py's wheel into its
   own repo/distribution (resolver-adjacent pieces already listed in
   PRD §12.3 can follow later; NL first since it has three consumers).
2. Re-point `nl2cypher` at the core; cypher-py's NL eval suite is the
   non-regression gate.
3. Implement `nl2sparql` as the SPARQL adapter (fills this repo's stub;
   its eval harness `RUN_EVAL=1` becomes the gate here).
4. Hand the fabric WP-D1 the core + adapter pins.

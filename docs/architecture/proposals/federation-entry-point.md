# Proposal: federation entry point + canonical-key contract (CDF M5 WP-C2)

Status: **Accepted & implemented** (2026-07-15) — shipped as
`arango_sparql.partition.translate_partition` (re-exported from
`arango_sparql.api`). The four open questions were resolved per the
recommendations below; the M5 planner can still renegotiate any of
them before pinning (CC-9), since the contract has one consumer today.

Decisions as implemented:

1. **Wire shape** = sub-SELECT string (shape 1). The algebra fast
   path (shape 2) is deferred until a measured need.
2. **Canonical key** = subject IRI, projected as the variable's own
   result column (`canonical_key_columns[var] == var`); no
   `phys:canonicalKeyField` annotation exists.
3. **Seed pushdown** = supported now: `seed_bindings` rows (rdflib
   terms or SPARQL-JSON binding dicts) are appended as a trailing
   `VALUES` clause — grammar-legal after the solution modifiers — so
   the seeds constrain the query inside ArangoDB. Values travel as a
   bind variable; IRIs are validated and literals escaped before the
   text splice.

   *Fabric alignment (confirmed by the CDF side, 2026-07-16):* this
   is exactly the **bind-join** mechanism CC-11/FR-13 (resource
   guardrails & admission control) specifies — the planner ships the
   small side's canonical keys into the Arango leg so the join
   constraint executes inside the database instead of hauling the
   large side across the wire. The M5 plan records this leg's
   guardrails story as satisfied by this contract.
4. **`as_of`** = executor-stamped; `PartitionProvenance.as_of` is
   `None` at translate time.

Test signal: `tests/translate/test_translate_partition.py` (contract
unit tests incl. hostile-seed escaping) and
`tests/cross/test_partition_cross.py` (pyoxigraph parity for a seeded
partition, plus a two-leg federation test asserting
partition + pushdown + engine-join == whole-query evaluation).

## Why

`contextual-data-fabric` ADR-0001 selects this repo as the owned
SPARQL→AQL leg of its federated query engine, and its M5
implementation plan (WP-C2) requires an entry point that accepts a
**query-graph partition** (an algebra sub-tree / sub-SELECT scoped to
the Arango-resident sources), not only a full SPARQL string, and that
returns **canonical entity keys** the engine can join across sources.
Today `arango_sparql.api.translate(sparql, resolver=…)` takes a full
query string and returns `(aql, bind_vars)` — no partition entry, no
key contract, no provenance stamp.

The companion WP-C1 (evaluation-correctness gate) landed in
`WP-BE-EVALGATE`; this proposal covers the remaining C2 surface.

## Proposed API (library-level; the fabric embeds us as a library)

```python
from arango_sparql.api import translate_partition

result = translate_partition(
    partition,                 # PartitionSpec — see below
    resolver=resolver,         # SchemaResolver / CSI-derived MappingBundle
    canonical_keys={"?person": "aer:Person"},   # var → AER entity type
)
```

### `PartitionSpec` — three accepted shapes, one normalized form

1. **Sub-SELECT string** (v1 wire shape, lowest coupling): a
   syntactically complete `SELECT … WHERE { … }` produced by the M5
   planner's partitioner. Internally we parse with `rdflib` exactly as
   `translate` does today. This is the recommended v1 contract: it
   keeps the planner↔leg boundary serializable, replayable, and
   provider-agnostic (Ontop's SQL leg consumes the same shape).
2. **rdflib algebra node** (in-process fast path): the planner already
   holds a translated algebra tree; skipping re-parse avoids the
   string round-trip. Accepted but not required for v1.
3. **`VALUES`-seeded partition**: shape 1 or 2 plus an optional
   `seed_bindings: list[dict[var, term]]` — the engine pushes bindings
   from an earlier leg into this partition (semi-join pushdown). We
   emit it as an AQL `FOR row IN @seed` + equality filters, reusing
   the existing `ToMultiSet`/VALUES emitter.

### `PartitionResult`

```python
@dataclass
class PartitionResult:
    aql: str
    bind_vars: dict[str, Any]
    projected_vars: list[str]          # SPARQL var names, declaration order
    canonical_key_columns: dict[str, str]  # var → result-object field holding the key
    provenance: PartitionProvenance    # see below
    schema_warnings: list[dict]        # same surface as TranslateResult
```

- **Canonical keys.** For every var listed in `canonical_keys`, the
  emitter projects an extra result field carrying the join key. v1
  policy: the key is the subject IRI (`_uri` / RPT `subject_uri`) —
  the same identity the AER (entity-resolution) layer keys on. If the
  fabric's AER instead requires its own key attribute (e.g.
  `doc.aer_key`), the attribute name comes in through the
  `MappingBundle` physical mapping (`phys:canonicalKeyField`, new
  annotation) — resolver plumbing, not emitter surgery.
- **Provenance.** `PartitionProvenance` carries
  `{source: str, query_text: str (the partition SPARQL), aql: str,
  source_objects: list[str] (collections read), as_of: datetime}` —
  the per-leg stamp M5/M7 (FR-5/11/12) attach to the grounded
  envelope. `source_objects` falls out of the builder's
  `bind_collection` registry for free.
- **Partial failure (CC-5/FR-11).** `translate_partition` raises the
  existing typed errors (`UnsupportedSparqlError`, …). The *executor*
  wraps execution failures as a declared-failed leg; that stays in
  M5, not here — but our error codes are the machine-readable reason
  it reports.

## Non-goals (stay in M5)

- Cross-source join planning and execution (the engine joins legs on
  the canonical keys we return).
- `SERVICE`-based federation inside a partition (the planner never
  hands us a partition containing `SERVICE`).
- Result streaming and envelope assembly (M7).

## Open questions for the M5 co-design

1. Is the sub-SELECT string the v1 wire shape, or does the planner
   want to hand us algebra directly from day one?
2. Key contract: is "subject IRI" acceptable for P1, or does AER
   mint surrogate keys that must round-trip through a physical
   attribute? (Determines whether `phys:canonicalKeyField` lands now.)
3. Seed-binding pushdown: required for P1 or a v1.1 optimization?
4. Does M5 want `as_of` stamped at translate time or execute time?
   (We propose execute time — translation is pure; the executor owns
   the clock.)

## Test plan

- Golden tests for partition translation incl. seeded VALUES
  (`tests/translate/partition.yml`).
- Cross-validation: partition + seed vs. pyoxigraph evaluating the
  equivalent full query.
- A two-leg fake-federation integration test: partition A's keys fed
  as partition B's seeds, joined in Python, compared against
  pyoxigraph on the union dataset.

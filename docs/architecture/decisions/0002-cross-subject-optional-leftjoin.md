# ADR-0002: Cross-subject `OPTIONAL` (LeftJoin) emitter — design options

- **Status:** **Partially resolved.** Problem 2 (OPTIONAL-rebind inside
  MINUS) is **shipped** (2026-06-02). Problem 1 **Option A** (RPT-native
  cross-subject OPTIONAL) is **shipped** (2026-06-02) — golden-pinned and
  binding-validated against pyoxigraph
  (`tests/cross/test_optional_crosssubject_cross.py`). Problem 1
  **Options B/C** (Document/PG emulation and full multi-model
  `_uri → collection` resolution) remain **deferred to post-v1.0**
  (travel with the SPARQL-federation slice; see PRD §3.1 slice-priority
  table).
- **Date:** 2026-05-28 (Problem 2 + Problem 1 Option A resolved 2026-06-02)
- **Owner:** arango-sparql-py
- **Related code:** `arango_sparql/translate/visitor.py::visit_LeftJoin`
  (the remaining `UnsupportedSparqlError` rejection branches),
  `arango_sparql/translate/optional_crosssubject.py` (the shipped
  Option A emitter),
  `arango_sparql/translate/variable_predicates.py` (the `ATTRIBUTES`
  fan-out carve-out Options B/C would inherit),
  `arango_sparql/translate/resolver.py::SchemaResolver` (the `?o → collection`
  resolution seam any PG emulation needs)
- **Supersedes:** —
- **Superseded by:** —

## Context

Four W3C DAWG query-evaluation tests remain XFAIL behind
`visit_LeftJoin`'s two defensive rejections, and they split into **two
semantically distinct problems** that happen to share a visitor method:

### Problem 1 — cross-subject OPTIONAL (`csv-tsv-res/tsv02`, `json-res/jsonres02`)

```sparql
SELECT * WHERE { ?s ?p ?o  OPTIONAL { ?o ?p2 ?o2 } } ORDER BY ?s ?p ?o ?p2 ?o2
```

The OPTIONAL's subject `?o` **is** bound by the required side, but only
as a *value* (the object of the first triple), not as a document the
translator has opened a `FOR` over (`var_to_doc_alias`). The OPTIONAL
body additionally uses a **variable predicate** (`?p2`). Current code
rejects this at `visit_LeftJoin`:

> `OPTIONAL whose subject is not already bound by the required side is
> not yet supported (cross-subject LEFT JOIN needs a subquery emitter)`

### Problem 2 — OPTIONAL re-binds a variable, inside MINUS (`negation/full-minuend`, `negation/part-minuend`)

```sparql
SELECT ?a ?b ?c {
  ?a :p1 ?b ; :p2 ?c
  MINUS {
    ?d a :Sub
    OPTIONAL { ?d :q1 ?b }   # ?b already bound by the required side
    OPTIONAL { ?d :q2 ?c }
  }
}
```

Inside the MINUS subpattern, `?b`/`?c` are already in scope (seeded from
the outer required side by the MINUS child visitor), so the OPTIONAL
"re-binds" them. Current code rejects:

> `OPTIONAL re-binds variable ?b that's already bound by the required side`

This is **not** a storage-model problem — it's a scope/semantics problem
about how an OPTIONAL that mentions an already-bound variable should
behave (per SPARQL §18.2.5.2 it acts as a *conditional add*: bind if the
optional triple matches with the existing value, otherwise leave the row
unchanged — i.e. a compatibility test, not a fresh binding).

### The decisive insight: difficulty is **storage-model-dependent**

The "which collection does `?o` range over?" question — the thing that
makes Problem 1 hard — **only exists in the flattened document models.**
Per the three read patterns the translator implements (`mapping.py`
`EntityStyle` / `RelationshipStyle`):

| Model | Is cross-subject OPTIONAL hard? | Why |
|---|---|---|
| **RPT** (rows in a `triplesCollection`) | **No — trivial & spec-correct** | `?o ?p2 ?o2` is a plain left-join scan: `FOR t IN @@triples FILTER t.<subject_uri> == <o>`. The variable predicate `?p2` is just `t.<predicate>` — no fan-out, no collection ambiguity. RPT *is* a quad/triple table; this is what it's for. |
| **PG** (one doc collection per class) | **Yes — genuinely ambiguous** | `?o` is a URI with no class annotation. To read `?o`'s triples you must first find *which collection* holds the doc whose `_uri == ?o`, then fan `?p2` over `ATTRIBUTES(doc)`. Multi-collection PG needs either a UNION over all collections or a `_uri → collection` index. |
| **LPG** (`LABEL` shared collection + discriminator) | **Mostly like PG** | Same `_uri → which collection` problem; the discriminator narrows it only if `?o`'s type is known, which it isn't here. |
| **Default `Document`** (W3C harness, permissive) | **Tractable but lossy** | Everything lives in one `Document` collection, so "which collection" collapses to a single answer — but the variable predicate inherits the existing `variable_predicates` carve-out (`?p2` binds to the *attribute name*, a string, not the predicate IRI), so it would *translate* but be a **live-execution XFAIL**, exactly like the 27 variable-predicate cases already tracked that way. |

**This is why the W3C number (95.7 %, measured 100 % on the flattened
`Document` model) and the "right" design pull in different directions:**
closing `tsv02`/`jsonres02` *in the harness* means the Document/PG
emulation (Option B), but the *clean, spec-faithful* implementation is
the RPT one (Option A) — which the harness never exercises.

## Considered options (Problem 1)

### Option A — RPT-native left-join only; reject flattened models — **SHIPPED (2026-06-02)**

Implement cross-subject OPTIONAL **only** when the OPTIONAL's subject
resolves to RPT-backed data. Emit the standard AQL left-join-via-subquery
idiom against the triples collection:

```aql
LET _opt = (FOR t IN @@triples
            FILTER t.<subject_uri> == <o_expr>
            RETURN { p2: t.<predicate>, o2: t.<object_value> })
FOR _row IN (LENGTH(_opt) > 0 ? _opt : [null])
  // bind ?p2 = _row.p2, ?o2 = _row.o2  (both null when no match)
```

- **Pro:** Spec-correct, including the variable predicate; multi-row
  OPTIONAL preserved; no carve-out. Reuses the `[null]`-padding
  left-join pattern AQL already needs for any optional.
- **Pro:** Reversible — leaves Options B/C addable later.
- **Con:** **Moves the W3C number by 0** — the harness is Document/PG,
  not RPT. Pure correctness investment, no coverage headline.
- **Con (resolved):** the original "ships untested end-to-end" worry no
  longer holds — the interpreter's row-list subquery + `[null]`-pad
  FOR-inline executor (added with this slice) gives Option A full
  binding cross-validation, not goldens only.

#### What shipped (2026-06-02)

`arango_sparql/translate/optional_crosssubject.py` implements exactly
the idiom above. `visit_LeftJoin` detects the RPT cross-subject case —
single-triple OPTIONAL body, no inner FILTER, subject bound in
`var_to_expr` but **not** in `var_to_doc_alias`, and `var_to_rpt_class`
non-empty (so a triples collection + column overrides are known) — and
routes to the emitter; everything else still hits the structured
rejection. Emission is `LET <opt> = (FOR doc IN @@triples FILTER
doc.<subject> == <o> [FILTER doc.<predicate> == @p] RETURN {…})` followed
by `FOR <row> IN (LENGTH(<opt>) > 0 ? <opt> : [null])`, binding each new
variable to `<row>.f<i>`. The `[null]` pad is what makes it a LEFT join;
multiple matches fan out (correct multiset OPTIONAL). A variable
predicate `?p2` projects the predicate column directly — the spec-
correct IRI binding the `Document` model cannot express. Non-variable
objects (existence tests) and multi-triple / filtered OPTIONAL bodies
remain refused with typed errors (future slices).

Verification: byte-for-byte goldens
(`tests/translate/test_translate_optional_crosssubject_goldens.py`) for
the variable- and fixed-predicate shapes plus rejection tests, and
binding parity against pyoxigraph over a shared RPT triples store
(`tests/cross/test_optional_crosssubject_cross.py`) covering fan-out,
single-match, and no-match→null-pad. Per the storage-model table this
moves the W3C harness number by **0** (the harness runs Document/PG, not
RPT) — `tsv02`/`jsonres02` stay XFAIL until Option B/C or federation
ships; this slice is a pure spec-faithfulness investment for real RPT
deployments.

### Option B — Default/single-collection emulation (the W3C-moving option)

Implement for the default-collection / `Document` model: open a
correlated subquery over the default collection filtered on
`doc._uri == <o_expr>`, fan the variable predicate `?p2` over
`ATTRIBUTES(doc)`, and left-join-pad with `[null]`.

- **Pro:** Closes `tsv02`/`jsonres02` in the harness (+0.8 pp).
- **Con:** Inherits the `variable_predicates` carve-out — `?p2` binds to
  the attribute *name* string, not the predicate IRI — so the two tests
  become **live-execution XFAILs** (translate-OK, execute-wrong), moving
  the gap rather than closing it. We'd be buying a translation-coverage
  point we already know is semantically lossy.
- **Con:** True multi-collection PG (more than one class collection) is
  still unsolved — it silently uses only the default collection.

### Option C — Full multi-model with `_uri → collection` resolution

Add a resolver capability that maps a subject URI to its collection
(either a maintained `_uri → collection` lookup, or a UNION fan-out over
all class collections), then dispatch: RPT → Option A path, PG/LPG →
resolved-collection subquery, default → Option B path.

- **Pro:** The only option that is correct across *all* models.
- **Con:** Largest scope by far; needs a new resolver index/contract and
  a UNION-cost story (O(N collections) per cross-subject OPTIONAL absent
  an index). Disproportionate for 2 W3C tests.

## Considered options (Problem 2 — OPTIONAL-rebind-in-MINUS) — **RESOLVED**

This is model-independent. The fix is in how the MINUS child visitor and
`visit_LeftJoin` treat an OPTIONAL object variable that is **already
bound**: instead of rejecting, emit a *conditional equality* — the
optional triple matches iff `doc.<attr> == <existing-binding>` (or the
edge target equals it), and on no-match the row is preserved with the
existing binding intact (SPARQL §18.2.5.2 conditional-add semantics).
The risk is getting the interaction with MINUS's own
compatibility-removal semantics right; it needs its own goldens and,
ideally, pyoxigraph cross-validation because the truth table
(`full-minuend` vs `part-minuend` differ only in whether the outer side
also has the OPTIONALs) is subtle.

### What shipped (2026-06-02)

The subtlety turned out to have **two** parts, both of which the
implementation now handles:

1. **Conditional add (§18.2.5.2).** `_BindingState.optional_rebind_sink`
   switches `visit_LeftJoin` out of "reject re-bind" mode while inside a
   MINUS probe. Each re-binding optional triple emits a compatibility
   FILTER `(<inner> == null || <outer> == null || <inner> == <outer>)`
   and records `(var, inner_value, outer_bound)` in the sink — it does
   **not** create a fresh binding.
2. **Disjoint-domain exemption (§8.3.4).** A MINUS inner row only removes
   an outer row when the two share **at least one bound** variable. When
   every shared variable is bound by an optional (so a `?d` matching
   nothing would otherwise vacuously delete every outer row),
   `_translate_probe` adds an *overlap* guard — the OR of
   `(<inner> != null && <outer> != null && <inner> == <outer>)` across
   the sink. If any shared variable is bound by a *required* inner
   triple, the child already FILTERs equality on it, so overlap is
   guaranteed and the guard is omitted.

Verification is exactly what this section asked for: golden AQL
(`minus_optional_single_rebind`, `minus_optional_two_rebinds`) **plus**
binding parity against pyoxigraph on the real W3C data
(`tests/cross/test_minus_optional_cross.py`, run under a PG ontology so
`?a a :Min` / `?d a :Sub` resolve to type-filtered FOR loops — the
permissive Document model drops those filters). The probe shape is now
executable by the AQL-subset interpreter (correlated `LET = LENGTH((…))`
+ scalar `RETURN`), which also retro-fitted cross-validation onto the
previously goldens-only MINUS / EXISTS translations
(`tests/cross/test_minus_exists_cross.py`).

## Decision

**Problem 2 is done (2026-06-02).** It was taken first — out of the
original sequencing order — because it is model-independent, closes two
real W3C tests with no lossiness, and needs no collection-resolution
dependency. A key fact had also changed since this ADR was written: the
AQL-subset interpreter learned to execute the MINUS / EXISTS probe shape
(`LET = LENGTH((…))`), so Problem 2 could be **binding-cross-validated**
rather than golden-pinned only — and that same capability removes
Option A's biggest con below ("ships untested end-to-end").

**Problem 1 Option A is done (2026-06-02).** It was taken next because
the interpreter capability added for Problem 2 (correlated subqueries)
generalised cheaply to the row-list + `[null]`-pad shape, removing
Option A's only real blocker ("ships untested end-to-end"). It is the
spec-faithful core the other two options layer onto, and shipping it now
means RPT deployments get correct cross-subject OPTIONAL while leaving
Options B/C addable later.

**Problem 1 Options B/C stay deferred to post-v1.0, bundled with the
SPARQL-federation slice:**

1. **Option B** only if a W3C-coverage headline is explicitly wanted and
   we accept logging `tsv02`/`jsonres02` as live-execution XFAILs (the
   honest accounting, consistent with the variable-predicate carve-out).
2. **Option C** only if/when multi-collection PG cross-subject queries
   become a real corpus need.

Rationale for deferring B/C: closing `tsv02`/`jsonres02` *in the harness*
requires the Document/PG emulation, which is known to be semantically
lossy (the variable predicate binds an attribute name, not an IRI), so
it would move the gap rather than close it. The §3.1 *coverage* bar is
already cleared by 71 pp, and the §3.1 *ratio* sub-clause can only be
fixed by shipping federation regardless (closing these would *raise* the
federation bucket's share, not lower it — see PRD §3.1 note). With the
spec-faithful RPT path now landed, the remaining sub-paths are neither
cheap nor non-lossy, so the right move is to pivot to workstreams with
clear, non-lossy wins (NL→SPARQL, executor, UI).

## Consequences

### Positive
- RPT deployments now get spec-correct cross-subject OPTIONAL, including
  variable predicates, golden-pinned and pyoxigraph-cross-validated.
- The remaining rejection branches in `visit_LeftJoin` keep raising
  **structured, greppable** `UnsupportedSparqlError`s (no silently-wrong
  AQL) for the PG/LPG/default cross-subject cases, and the XFAIL ledger
  keeps the harness gap visible.
- The per-model analysis is captured, so whoever picks up Options B/C
  doesn't re-derive the "which collection" problem from scratch.

### Negative
- W3C query-eval coverage moved 95.7 % → 96.4 % when Problem 2 shipped
  (`full-minuend` + `part-minuend`); Option A moves it by **0** (the
  harness runs Document/PG, not RPT), so `tsv02`/`jsonres02` stay XFAIL
  until Option B/C or federation ships.

### Neutral
- The ADR began as a design record only; Problem 2 (2026-06-02) and
  Problem 1 Option A (`optional_crosssubject.py` + the interpreter's
  row-list/`[null]`-pad executor, 2026-06-02) both landed under it.

## References
- SPARQL 1.1 §18.2.5.2 (translation of `OPTIONAL` / `LeftJoin`),
  §17.4.1 (filter/compatibility semantics)
- W3C DAWG cases: `csv-tsv-res/tsv02`, `json-res/jsonres02`,
  `negation/full-minuend`, `negation/part-minuend`
- `mapping.py` `EntityStyle` / `RelationshipStyle` (the model enumeration
  this analysis is keyed to)
- ADR-0001 (per-document `_graph`) — the precedent for layout-uniform vs
  layout-specific translation decisions
- PRD §3.1 slice-priority table and the §3.1 ratio-sub-clause note

---
name: sparql-to-aql
description: Port SPARQL→AQL translation behavior from the legacy `arango-sparql` Foxx service into Python rdflib Algebra visitors with parameterized AQL builder calls. Use when implementing or fixing any SPARQL construct (BGP, OPTIONAL, FILTER, UNION, MINUS, GRAPH, regex, aggregates, property paths, etc.) in `arango_sparql/` or extending the AQL query builder.
---

# SPARQL → AQL Porting

This skill is the deterministic recipe for adding or fixing a SPARQL
construct in `arango-sparql-py`. It exists because the translation
behavior is non-obvious and must match the legacy Foxx service's
semantics — never invented from scratch.

## Inputs you will work with

- **Legacy source of truth**: `references/arango-sparql/src/lib/`
  - `aql-translator.js` — top-level dispatch by query type.
  - `pgt-translator.js` — Property Graph Topology translation rules.
  - `rpt-translator.js` — RDF Property Triple translation rules.
  - `filter-translator.js` — FILTER → AQL boolean expression mapping.
  - `triple-constructor.js` — CONSTRUCT clause helpers.
  - `aql-query-builder.js` — fluent AQL builder API to mimic.
  - `uri-resolver.js`, `uri-hasher.js` — URI ↔ collection/key mapping.
- **Python target**: `arango_sparql/translate/`
  - `parser.py` — wraps `rdflib.plugins.sparql.parser.parseQuery` +
    `algebra.translateQuery`.
  - `visitor.py` — the Algebra visitor (one `visit_<NodeType>` per
    Algebra op).
  - `builder.py` — the parameterized AQL builder.
  - `resolver.py` — `SchemaResolver` over the in-memory OWL graph.
  - `errors.py` — `SparqlError` hierarchy.

## Workflow

Copy this checklist into your scratchpad and tick items as you go:

```
Task progress:
- [ ] 1. Identify the SPARQL construct and its Algebra node name
- [ ] 2. Read the legacy JS translator for that construct
- [ ] 3. Locate (or create) the visitor method
- [ ] 4. Implement using the AQL builder (no string concat)
- [ ] 5. Add a golden test
- [ ] 6. Add a pyoxigraph cross-validation test (when applicable)
- [ ] 7. Run pytest and ruff
```

### 1. Identify the Algebra node

Run this in a scratch shell or a unit test to see the exact Algebra
node name `rdflib` produces for your SPARQL fragment:

```python
from rdflib.plugins.sparql.parser import parseQuery
from rdflib.plugins.sparql.algebra import translateQuery
print(translateQuery(parseQuery("SELECT ?s WHERE { ?s ?p ?o OPTIONAL { ?s :name ?n } }")).algebra)
```

Common Algebra node names:

| SPARQL                 | Algebra node                         |
| ---------------------- | ------------------------------------ |
| Triple pattern (BGP)   | `BGP`                                |
| `SELECT ?x ...`        | `Project` over `... `                |
| `OPTIONAL { ... }`     | `LeftJoin`                           |
| `FILTER (...)`         | `Filter`                             |
| `UNION`                | `Union`                              |
| `MINUS`                | `Minus`                              |
| `GRAPH ?g { ... }`     | `Graph`                              |
| `LIMIT` / `OFFSET`     | `Slice`                              |
| `ORDER BY`             | `OrderBy`                            |
| `DISTINCT`             | `Distinct`                           |
| `BIND (... AS ?x)`     | `Extend`                             |
| `GROUP BY` + aggregates| `Group` + `AggregateJoin`            |
| Property paths         | `Path` (transitive: `MulPath`, …)    |
| `VALUES`               | `ToMultiSet` / `values`              |
| `SERVICE`              | `Service` (raise `UnsupportedSparql` until implemented)|

### 2. Read the legacy JS

Find the matching method in the legacy translator:

```bash
rg -n "OPTIONAL|LeftJoin|leftJoin" references/arango-sparql/src/lib/
```

Read it end-to-end before writing Python. Pay attention to:

- which AQL pattern it emits (FOR / FILTER / LET / RETURN structure),
- how it threads bind variables,
- what edge cases (empty BGPs, unbound vars in projection) it handles,
- which model branch it lives in (`pgt-translator.js` for Property
  Graph Topology, `rpt-translator.js` for RDF Property Triples).

### 3. Locate or create the visitor method

The visitor lives in `arango_sparql/translate/visitor.py`. Method
naming is **mechanical**:

```python
class AlgebraVisitor:
    def visit(self, node):
        method = getattr(self, f"visit_{node.name}", self.visit_unknown)
        return method(node)

    def visit_BGP(self, node): ...
    def visit_LeftJoin(self, node): ...   # OPTIONAL
    def visit_Filter(self, node): ...
    def visit_unknown(self, node):
        raise UnsupportedSparqlError(
            code="E_UNSUPPORTED_NODE",
            message=f"SPARQL Algebra node {node.name!r} is not implemented",
        )
```

### 4. Implement against the AQL builder

Never concatenate AQL. The builder handles aliasing, bind variable
naming, and clause ordering.

```python
# inside visit_LeftJoin (OPTIONAL)
inner = self.visit(node.p1)            # required side
opt = self.visit(node.p2)              # optional side
self.builder.left_join(inner, opt, on=self._shared_vars(node.p1, node.p2))
```

For literals, always go through the builder's bind-variable API:

```python
self.builder.bind(value=literal.toPython())  # returns "@p7" (or similar)
```

Refuse the temptation to write `f"FILTER {alias}.name == '{value}'"` —
that path is forbidden by `.cursor/rules/100-backend-python.mdc`.

### 5. Add a golden test

Create or extend a YAML golden file alongside the test:

```
tests/translate/test_translate_optional_goldens.py
tests/translate/optional.yml
```

`optional.yml` shape (mirror Cypher's golden YAML):

```yaml
- name: simple_optional
  sparql: |
    SELECT ?s ?n WHERE { ?s a :Person OPTIONAL { ?s :name ?n } }
  expected_aql: |
    FOR doc0 IN @@person
      LET opt0 = (FOR doc1 IN @@person_name FILTER doc1._from == doc0._id RETURN doc1.value)[0]
      RETURN { s: doc0._id, n: opt0 }
  expected_bind_vars:
    "@person": "Person"
    "@person_name": "Person_name"
```

Goldens are reviewed by humans. Never auto-regenerate them in CI.

### 6. Cross-validate with pyoxigraph (when semantics matter)

For any construct where W3C semantics are non-trivial (FILTER NOT
EXISTS, SUM with empty groups, regex flags, datatype coercion, …),
add a `tests/cross/` test:

```python
@pytest.mark.cross
def test_optional_matches_oxigraph(oxi_store, arango_db, mapping):
    sparql = "SELECT ?s ?n WHERE { ?s a :Person OPTIONAL { ?s :name ?n } }"
    expected = oxi_bindings(oxi_store, sparql)
    actual = run_via_arango(arango_db, mapping, sparql)
    assert_bindings_equal(expected, actual)
```

`assert_bindings_equal` is order-insensitive for SELECT and
set-equality for ASK/CONSTRUCT.

### 7. Run the full check

```bash
uv run ruff check .
uv run pytest -q -ra
uv run pytest -q -m cross  # only when integration env is up
```

## Worked example: porting `OPTIONAL`

1. **Algebra node**: `LeftJoin`.
2. **Legacy JS**: `references/arango-sparql/src/lib/pgt-translator.js`,
   search for `translateLeftJoin` / `OPTIONAL`. Note it emits a
   `LET opt = (FOR ... RETURN ...)[0]` subquery and uses null-coalescing
   in the projection.
3. **Visitor**: add `visit_LeftJoin(self, node)` to
   `arango_sparql/translate/visitor.py`.
4. **Builder**: extend with `builder.left_join(required, optional, on)`
   that emits the `LET ...[0]` pattern. Bind vars stay parameterized.
5. **Golden**: `tests/translate/optional.yml` with one minimal case and
   one nested-OPTIONAL case.
6. **Cross**: `tests/cross/test_optional.py` over a 5-triple toy
   dataset loaded into both `pyoxigraph` and ArangoDB.

## When you are stuck

- Algebra node name unknown? Print the algebra (Step 1) — `rdflib`
  exposes the canonical name on the node.
- Two legacy translators emit different AQL for the same SPARQL?
  Branch by `modelType` (`PGT` vs `RPT`) the same way the legacy
  `aql-translator.js` does — there is no single "right" emission.
- Behavior under-specified? Add an `xfail` cross test against
  `pyoxigraph` and ship the case file before the fix; that pins the
  W3C-correct expectation as a regression target.

## Forbidden

- ANTLR. Custom parsers. Hand-concatenated AQL. Inlined literals.
- "Fixing" a golden by editing it to match buggy output. The golden
  *is* the spec — change the implementation, not the spec.
- Skipping the legacy JS read in Step 2. The semantics live there.

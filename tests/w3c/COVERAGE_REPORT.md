# W3C SPARQL 1.1 DAWG coverage — measured

> Methodology: translation-only dry run (`python tests/w3c/analyze_coverage.py`). Each query is parsed and (for evaluation tests) handed to `arango_sparql.api.translate`. A scenario passes when:
>
> * **Syntax (positive)** — `rdflib` accepts the query;
> * **Syntax (negative)** — `rdflib` raises a `SparqlParseError` (the test deliberately ill-formed);
> * **Query evaluation** — the visitor produces non-empty AQL without raising `UnsupportedSparqlError`.

Low query-evaluation coverage is *expected* in v0 and tracks our progress as visitor methods are ported from `references/arango-sparql/src/lib/`.

## Headline numbers

| Category | Total | Pass | Fail | Xfail | Skip | Coverage |
| -------- | -----:| ----:| ----:| -----:| ----:| --------:|
| Syntax (positive) | 63 | 63 | 0 | 0 | 0 | 100.0% |
| Syntax (negative) | 43 | 29 | 0 | 14 | 0 | 67.4% |
| Query evaluation | 253 | 38 | 0 | 215 | 0 | 15.0% |

## Out-of-scope test types (counted, not run)

| Test type | Total | Reason |
| --------- | -----:| ------ |
| `mf:CSVResultFormatTest` | 3 | SPARQL 1.1 Update / Protocol / Service-Description / CSV result-format are not v0 targets — the transpiler ports query semantics first. |
| `mf:NegativeUpdateSyntaxTest11` | 13 | SPARQL 1.1 Update / Protocol / Service-Description / CSV result-format are not v0 targets — the transpiler ports query semantics first. |
| `mf:PositiveUpdateSyntaxTest11` | 42 | SPARQL 1.1 Update / Protocol / Service-Description / CSV result-format are not v0 targets — the transpiler ports query semantics first. |
| `mf:ProtocolTest` | 34 | SPARQL 1.1 Update / Protocol / Service-Description / CSV result-format are not v0 targets — the transpiler ports query semantics first. |
| `mf:ServiceDescriptionTest` | 3 | SPARQL 1.1 Update / Protocol / Service-Description / CSV result-format are not v0 targets — the transpiler ports query semantics first. |
| `mf:UpdateEvaluationTest` | 93 | SPARQL 1.1 Update / Protocol / Service-Description / CSV result-format are not v0 targets — the transpiler ports query semantics first. |

## Top XFAIL reasons

| Count | Reason | Implication |
| -----:| ------ | ----------- |
| 47 | `UnsupportedSparql: variable predicates (?p) require multi-collection UNION; not yet supported` | port the corresponding visitor method |
| 14 | `SchemaResolution: class IRI 'http://www.w3.org/2002/07/owl#Restriction' is not declared owl:Class ...` | port the corresponding visitor method |
| 14 | `rdflib accepted invalid query` | port the corresponding visitor method |
| 13 | `UnsupportedSparql: SPARQL Algebra node 'ToMultiSet' is not implemented yet (see .cursor/skills/spar...` | port the corresponding visitor method |
| 10 | `UnsupportedSparql: SPARQL Algebra node 'Graph' is not implemented yet (see .cursor/skills/sparql-to...` | port the corresponding visitor method |
| 8 | `SchemaResolution: class IRI 'http://www.w3.org/2002/07/owl#DatatypeProperty' is not declared owl:C...` | port the corresponding visitor method |
| 7 | `UnsupportedSparql: SPARQL Algebra node 'Minus' is not implemented yet (see .cursor/skills/sparql-to...` | port the corresponding visitor method |
| 7 | `UnsupportedSparql: unsupported triple shape: subject=URIRef, predicate=MulPath, object=Variable` | port the corresponding visitor method |
| 6 | `UnsupportedSparql: SPARQL Algebra node 'ConstructQuery' is not implemented yet (see .cursor/skills/...` | port the corresponding visitor method |
| 5 | `UnsupportedSparql: object term type 'BNode' is not supported in triple (rdflib.term.Variable('x'), ...` | port the corresponding visitor method |
| 4 | `UnsupportedSparql: FILTER expression node 'Builtin_LANGMATCHES' is not yet supported (see reference...` | port the corresponding visitor method |
| 4 | `UnsupportedSparql: unsupported triple shape: subject=URIRef, predicate=SequencePath, object=Variabl...` | port the corresponding visitor method |
| 3 | `SchemaResolution: class IRI 'http://example.org/x/c' is not declared owl:Class in the ontology` | port the corresponding visitor method |
| 3 | `AqlEmit: query has no FOR clause; every BGP/SELECT translation needs at least one` | port the corresponding visitor method |
| 3 | `UnsupportedSparql: FILTER expression node 'Builtin_REPLACE' is not yet supported (see references/ar...` | port the corresponding visitor method |

## How to reproduce

```bash
python tests/w3c/analyze_coverage.py            # print
python tests/w3c/analyze_coverage.py --write    # update this file
pytest -q tests/w3c -m w3c                      # full pytest run
RUN_INTEGRATION=1 python tests/w3c/analyze_coverage.py --live --write
                                                # include live execution row
```

End-to-end (live ArangoDB) coverage is computed by re-running with `--live` after `RUN_INTEGRATION=1` is set; without it the live row is omitted so the report stays reproducible without Docker.

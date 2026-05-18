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
| Query evaluation | 253 | 83 | 0 | 170 | 0 | 32.8% |

## Out-of-scope test types (counted, not run)

| Test type | Total | Reason |
| --------- | -----:| ------ |
| `mf:CSVResultFormatTest` | 3 | SPARQL 1.1 Update / Protocol / Service-Description / CSV result-format are not v0 targets — the transpiler ports query semantics first. |
| `mf:NegativeUpdateSyntaxTest11` | 13 | SPARQL 1.1 Update / Protocol / Service-Description / CSV result-format are not v0 targets — the transpiler ports query semantics first. |
| `mf:PositiveUpdateSyntaxTest11` | 42 | SPARQL 1.1 Update / Protocol / Service-Description / CSV result-format are not v0 targets — the transpiler ports query semantics first. |
| `mf:ProtocolTest` | 34 | SPARQL 1.1 Update / Protocol / Service-Description / CSV result-format are not v0 targets — the transpiler ports query semantics first. |
| `mf:ServiceDescriptionTest` | 3 | SPARQL 1.1 Update / Protocol / Service-Description / CSV result-format are not v0 targets — the transpiler ports query semantics first. |
| `mf:UpdateEvaluationTest` | 93 | SPARQL 1.1 Update / Protocol / Service-Description / CSV result-format are not v0 targets — the transpiler ports query semantics first. |

## XFAIL implication summary

Each XFAIL is bucketed by what fixing it would require — this distinguishes real roadmap gaps from artefacts of the translation-only harness, which runs every query against an empty resolver (`SchemaResolver.from_turtle('', default_collection='Document')`).

| Bucket | Count | Implication |
| ------ | -----:| ----------- |
| `algebra` | 126 | port the corresponding visitor method |
| `schema` | 44 | harness artefact (empty resolver); will pass against a populated ontology |
| `rdflib` | 14 | rdflib parser disagreement; out of scope here |

## Top XFAIL reasons

| Count | Bucket | Reason | Implication |
| -----:| ------ | ------ | ----------- |
| 14 | `schema` | `SchemaResolution: class IRI 'http://www.w3.org/2002/07/owl#Restriction' is not declared owl:Class ...` | harness artefact (empty resolver); will pass against a populated ontology |
| 14 | `rdflib` | `rdflib accepted invalid query` | rdflib parser disagreement; out of scope here |
| 11 | `algebra` | `UnsupportedSparql: SPARQL Algebra node 'Graph' is not implemented yet (see .cursor/skills/sparql-to...` | port the corresponding visitor method |
| 8 | `schema` | `SchemaResolution: class IRI 'http://www.w3.org/2002/07/owl#DatatypeProperty' is not declared owl:C...` | harness artefact (empty resolver); will pass against a populated ontology |
| 7 | `algebra` | `UnsupportedSparql: SPARQL Algebra node 'Minus' is not implemented yet (see .cursor/skills/sparql-to...` | port the corresponding visitor method |
| 5 | `algebra` | `UnsupportedSparql: object term type 'BNode' is not supported in triple (rdflib.term.Variable('x'), ...` | port the corresponding visitor method |
| 5 | `algebra` | `UnsupportedSparql: FILTER expression node 'Builtin_EXISTS' is not yet supported (see references/ara...` | port the corresponding visitor method |
| 5 | `algebra` | `UnsupportedSparql: transitive property paths (':p*') are not yet supported` | port the corresponding visitor method |
| 4 | `algebra` | `UnsupportedSparql: subject term type 'BNode' is not supported` | port the corresponding visitor method |
| 4 | `algebra` | `UnsupportedSparql: CONSTRUCT without a template is not supported` | port the corresponding visitor method |
| 4 | `algebra` | `UnsupportedSparql: FILTER expression node 'Builtin_LANGMATCHES' is not yet supported (see reference...` | port the corresponding visitor method |
| 4 | `algebra` | `UnsupportedSparql: transitive property paths (':p+') are not yet supported` | port the corresponding visitor method |
| 4 | `algebra` | `UnsupportedSparql: alternative property paths (':p|:q') are not yet supported` | port the corresponding visitor method |
| 4 | `algebra` | `UnsupportedSparql: SPARQL Algebra node 'ServiceGraphPattern' is not implemented yet (see .cursor/sk...` | port the corresponding visitor method |
| 3 | `algebra` | `UnsupportedSparql: FILTER expression node 'Builtin_IF' is not yet supported (see references/arango-...` | port the corresponding visitor method |

## How to reproduce

```bash
python tests/w3c/analyze_coverage.py            # print
python tests/w3c/analyze_coverage.py --write    # update this file
pytest -q tests/w3c -m w3c                      # full pytest run
RUN_INTEGRATION=1 python tests/w3c/analyze_coverage.py --live --write
                                                # include live execution row
```

End-to-end (live ArangoDB) coverage is computed by re-running with `--live` after `RUN_INTEGRATION=1` is set; without it the live row is omitted so the report stays reproducible without Docker.

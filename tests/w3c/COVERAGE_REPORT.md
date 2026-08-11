# W3C SPARQL 1.1 DAWG coverage — measured

> Methodology: translation-only dry run (`python tests/w3c/analyze_coverage.py`). Each query is parsed and (for evaluation tests) handed to `arango_sparql.api.translate`. A scenario passes when:
>
> * **Syntax (positive)** — `rdflib` accepts the query;
> * **Syntax (negative)** — `rdflib` raises a `SparqlParseError` (the test deliberately ill-formed);
> * **Query evaluation** — the visitor produces non-empty AQL without raising `UnsupportedSparqlError`.
> * **Live execution** — the translated AQL was run against a real ArangoDB and the bindings matched the W3C-expected `.srx` results.
> * **Live storage profile** — `document_edge`. Profiles are measured independently; their denominators must not be merged.

Query-evaluation coverage measures translation acceptance; live coverage separately measures storage and execution fidelity. The profiles below deliberately keep those signals distinct.

## Headline numbers

| Category | Total | Pass | Fail | Xfail | Skip | Coverage |
| -------- | -----:| ----:| ----:| -----:| ----:| --------:|
| Syntax (positive) | 63 | 63 | 0 | 0 | 0 | 100.0% |
| Syntax (negative) | 43 | 29 | 0 | 14 | 0 | 67.4% |
| Query evaluation | 253 | 244 | 0 | 9 | 0 | 96.4% |
| Live execution (document_edge) | 191 | 124 | 0 | 67 | 0 | 64.9% |

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

Each XFAIL is bucketed by what fixing it would require — this distinguishes real roadmap gaps (``algebra``) from out-of-our-hands rdflib disagreements (``rdflib``). The translation-only harness runs every query against a permissive empty resolver (`SchemaResolver.from_turtle('', default_collection='Document', permissive_class_resolution=True)`), so unknown class IRIs degrade to the default collection rather than masking algebra gaps behind schema XFAILs.

| Bucket | Count | Implication |
| ------ | -----:| ----------- |
| `algebra` | 9 | port the corresponding visitor method |
| `schema` | 0 | real schema-resolution failure even under permissive mode (should be 0 — investigate any non-zero count) |
| `rdflib` | 14 | rdflib parser disagreement; out of scope here |

## Top XFAIL reasons

| Count | Bucket | Reason | Implication |
| -----:| ------ | ------ | ----------- |
| 14 | `rdflib` | `rdflib accepted invalid query` | rdflib parser disagreement; out of scope here |
| 4 | `algebra` | `UnsupportedSparql: SPARQL Algebra node 'ServiceGraphPattern' is not implemented yet (see .cursor/sk...` | port the corresponding visitor method |
| 2 | `algebra` | `UnsupportedSparql: OPTIONAL whose subject is not already bound by the required side is not yet supp...` | port the corresponding visitor method |
| 2 | `algebra` | `SparqlParse: failed to parse SPARQL: maximum recursion depth exceeded` | port the corresponding visitor method |
| 1 | `algebra` | `UnsupportedSparql: OPTIONAL whose body is 'ServiceGraphPattern' (not a plain BGP) is not yet suppor...` | port the corresponding visitor method |

## Live-execution divergences

| Count | Test ID | Divergence reason |
| -----:| ------- | ----------------- |
| 6 | _(see test)_ | `OWL DL reasoning required` |
| 1 | _(see test)_ | `language-tag matching ('name'@en) — loader flattens lang tags; AQL has no notion of xml:lang` |
| 1 | _(see test)_ | `RDFS entailment required` |
| 1 | _(see test)_ | `RDF literal-form distinction (plain vs xsd:string)` |
| 1 | _(see test)_ | `RDFS subPropertyOf / domain entailment required` |
| 1 | _(see test)_ | `RDFS subPropertyOf transitivity entailment required` |
| 1 | _(see test)_ | `RDFS subClassOf / Resource entailment required` |
| 1 | _(see test)_ | `RDFS subClassOf reflexivity entailment required` |
| 1 | _(see test)_ | `RDFS member / ContainerMembershipProperty entailment required` |

## How to reproduce

```bash
python tests/w3c/analyze_coverage.py            # print
python tests/w3c/analyze_coverage.py --write    # update this file
pytest -q tests/w3c -m w3c                      # full pytest run
RUN_INTEGRATION=1 python tests/w3c/analyze_coverage.py --live --profile document_edge --write
                                                # canonical live baseline
RUN_INTEGRATION=1 python tests/w3c/analyze_coverage.py --live --profile rpt
                                                # separate RPT discovery
```

Live-execution numbers are scoped to the translatable subset (cases that the visitor accepts today). They surface AQL ↔ SPARQL semantic divergences caught against a real ArangoDB.

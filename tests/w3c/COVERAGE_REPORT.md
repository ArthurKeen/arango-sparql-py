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
| Query evaluation | 253 | 233 | 0 | 20 | 0 | 92.1% |

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
| `algebra` | 20 | port the corresponding visitor method |
| `schema` | 0 | real schema-resolution failure even under permissive mode (should be 0 — investigate any non-zero count) |
| `rdflib` | 14 | rdflib parser disagreement; out of scope here |

## Top XFAIL reasons

| Count | Bucket | Reason | Implication |
| -----:| ------ | ------ | ----------- |
| 14 | `rdflib` | `rdflib accepted invalid query` | rdflib parser disagreement; out of scope here |
| 4 | `algebra` | `UnsupportedSparql: SPARQL Algebra node 'ServiceGraphPattern' is not implemented yet (see .cursor/sk...` | port the corresponding visitor method |
| 2 | `algebra` | `UnsupportedSparql: FILTER references unbound variable ?nova; the BGP never bound it. Are you missin...` | port the corresponding visitor method |
| 2 | `algebra` | `UnsupportedSparql: OPTIONAL whose subject is not already bound by the required side is not yet supp...` | port the corresponding visitor method |
| 2 | `algebra` | `UnsupportedSparql: OPTIONAL re-binds variable ?b that's already bound by the required side` | port the corresponding visitor method |
| 2 | `algebra` | `SparqlParse: failed to parse SPARQL: maximum recursion depth exceeded` | port the corresponding visitor method |
| 1 | `algebra` | `UnsupportedSparql: FILTER expression node 'Function' is not yet supported (see references/arango-sp...` | port the corresponding visitor method |
| 1 | `algebra` | `UnsupportedSparql: FILTER references unbound variable ?z; the BGP never bound it. Are you missing a...` | port the corresponding visitor method |
| 1 | `algebra` | `UnsupportedSparql: FILTER expression has no .name attribute: str` | port the corresponding visitor method |
| 1 | `algebra` | `UnsupportedSparql: FILTER expression node 'Builtin_TIMEZONE' is not yet supported (see references/a...` | port the corresponding visitor method |
| 1 | `algebra` | `UnsupportedSparql: FILTER references unbound variable ?m; the BGP never bound it. Are you missing a...` | port the corresponding visitor method |
| 1 | `algebra` | `UnsupportedSparql: FILTER references unbound variable ?g; the BGP never bound it. Are you missing a...` | port the corresponding visitor method |
| 1 | `algebra` | `UnsupportedSparql: nested property path 'MulPath' inside MulPath (':p*') is not supported` | port the corresponding visitor method |
| 1 | `algebra` | `UnsupportedSparql: OPTIONAL whose body is 'ServiceGraphPattern' (not a plain BGP) is not yet suppor...` | port the corresponding visitor method |

## How to reproduce

```bash
python tests/w3c/analyze_coverage.py            # print
python tests/w3c/analyze_coverage.py --write    # update this file
pytest -q tests/w3c -m w3c                      # full pytest run
RUN_INTEGRATION=1 python tests/w3c/analyze_coverage.py --live --write
                                                # include live execution row
```

End-to-end (live ArangoDB) coverage is computed by re-running with `--live` after `RUN_INTEGRATION=1` is set; without it the live row is omitted so the report stays reproducible without Docker.

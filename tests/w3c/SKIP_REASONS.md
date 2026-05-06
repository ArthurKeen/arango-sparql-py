# W3C SPARQL 1.1 DAWG skip log

This file records every W3C test we **intentionally** bypass and why.
The harness (`tests/w3c/runner.py`) loads this file at collection time
and excludes any case whose IRI appears in the **first column** of the
table below. A skip here means the harness will not run the test at
all — distinct from `pytest.xfail`, which still parameterizes the case
so we notice when it starts passing.

## Format

* The test IRI in column 1 must be the absolute IRI from the manifest
  (e.g. `http://www.w3.org/2009/sparql/docs/tests/data-sparql11/aggregates/manifest#agg01`).
* Column 2 is the human-readable `mf:name` (for grep-ability).
* Column 3 is the rationale — link a tracking issue, a bug in upstream
  rdflib / pyoxigraph, or a project decision (e.g. "v0 scope excludes
  SPARQL Update").
* Column 4 names the engineer who owns chasing the skip down. Skips
  without an owner are stale by definition; CI may eventually fail on
  them.

The parser is intentionally loose — header rows, the placeholder
`_(none yet)_` row, and any row whose first cell does not look like an
absolute IRI are ignored.

## When to add a row here vs. xfail

| Situation | Use |
| --------- | --- |
| Translator hits `UnsupportedSparqlError` | **xfail** in the test (no skip needed) — it's tracked in `COVERAGE_REPORT.md`. |
| `rdflib` itself can't parse a syntactically valid SPARQL 1.1 query | **xfail** in `test_w3c_syntax_tests.py` (already handled). |
| Test depends on a feature explicitly out of v0 scope (Update, Service Description, Protocol) | Out-of-scope test types are already filtered by `runner.OUT_OF_SCOPE_TYPES`; **no skip row needed**. |
| Test is **broken in the upstream corpus** (bad expected results, references missing data) | Add a skip row pointing at the upstream bug. |
| Test would loop / OOM / wedge the harness | Add a skip row immediately and open a tracking issue. |

## Skipped tests

| Test IRI | Test name | Skip reason | Owner |
| -------- | --------- | ----------- | ----- |
| _(none yet)_ |           |             |       |

# Deferred Items — Phase 06 (nl-sparql-eval-harness-seed-corpus)

## Pre-existing environment gap: `arangodb-schema-analyzer` not installed locally

**Discovered during:** 06-01, verifying `pytest tests/nl2sparql -q -m "not eval"` per the
plan's `<verification>` section.

**Observation:** `arangodb_schema_analyzer` (the declared upstream hard dependency,
`pyproject.toml` pin `>=0.6.1,<0.7.0`) is not importable in this local dev shell.
Every `tests/nl2sparql/*` module errors at collection time with
`AnalyzerStartupGuardError: SCHEMA_ANALYZER_REQUIRED=true but arangodb-schema-analyzer
is not installed`.

**Scope determination:** Out of scope for this plan. 06-01 only adds two new YAML data
files (`corpus.yml`, `configs.yml`) — no Python code, no new imports. The collection
failure is a pre-existing local-environment condition (the package is declared but not
`pip install`-ed in this shell), unrelated to and unaffected by this plan's changes.
Installing it is excluded from Rule 3 auto-fix (package-manager installs require
explicit handling) and is not this plan's responsibility to remediate.

**Verification performed instead:** Ran the plan's own automated checks directly
(`yaml.safe_load` + `arango_sparql.translate.parser.parse_sparql` + `SchemaResolver.from_turtle`
against `corpus.yml`), all of which import cleanly without touching the schema-analyzer
startup guard. Both pass.

**Action:** Not fixed here. Flagging for whoever next needs a green
`pytest tests/nl2sparql -q` locally: `pip install 'arangodb-schema-analyzer>=0.6.1,<0.7'`
or set `SCHEMA_ANALYZER_REQUIRED=false` per the guard's own error message.

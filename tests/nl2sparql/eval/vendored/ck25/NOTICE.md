# CK25 (eccenca/ck25-dataset) — Attribution Notice

This directory vendors data derived from the **CK25** corporate-domain
NL→SPARQL benchmark, used as this repo's corporate-domain relevance anchor
(D-03, `07.1-CONTEXT.md`).

- **Title:** CK25 dataset (`ck25-dataset`)
- **Source:** https://github.com/eccenca/ck25-dataset
- **Commit:** `cb928b2f201e4bdbbde9a1cd0653152779736395` (fetched 2026-07-22)
- **License:** [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) —
  see the upstream repository's `LICENSE` file.

## Files vendored

- `raw/questions.yml` — verbatim copy of `questions.yml` (50 English
  question → gold-SPARQL pairs, each with a `classes:`/`properties:`
  feature manifest).
- `raw/prod-inst-schema.ttl` — a **schema-only** extract of
  `graphs/prod-inst.ttl` (the upstream file is a hybrid schema+instance
  Turtle document): every `owl:Class`/`owl:ObjectProperty`/
  `owl:DatatypeProperty` declaration and its `rdfs:domain`/`rdfs:range`
  axioms, taken from the file's header through its last vocabulary
  (`owl:AnnotationProperty`) declaration, verbatim and unmodified.

## Changes made

- **Instance data removed.** The upstream `prod-inst.ttl` also contains real
  instance triples (employees, departments, hardware, suppliers, BOMs,
  prices, ...); none of that instance data is vendored here (per this
  phase's Open Question 2 — canonical-only judging, no execution-tier data
  for CK25 this phase). Only the schema/vocabulary portion of the file is
  kept, copied byte-for-byte from the upstream source.
- **No other content changes.** `questions.yml` is vendored verbatim
  (no edits).

## Downstream use

`convert_ck25.py` (this directory) converts `raw/questions.yml` +
`raw/prod-inst-schema.ttl` into `corpus.yml` (a `phys:`-annotated ontology
plus the surviving D-06-filtered cases) for this repo's NL→SPARQL eval
harness (`tests/nl2sparql/eval/runner.py`). See `filter_log.md` for the
kept/dropped audit trail.

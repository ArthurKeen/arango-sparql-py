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
- `raw/prod-inst.ttl` — the **full** upstream `graphs/prod-inst.ttl`
  (951,747 bytes / 26,903 triples), copied verbatim and unmodified at the
  pinned commit above. This is the hybrid schema+instance Turtle document
  in its entirety — `raw/prod-inst-schema.ttl` is a strict subset of it.

## Changes made

- **Instance data restored (Phase 07.2 reverses 07.1's removal).** 07.1
  vendored only the schema portion of `prod-inst.ttl` and explicitly noted
  that instance data (employees, departments, hardware, suppliers, BOMs,
  prices, ...) was not vendored (canonical-only judging). Phase 07.2
  reverses that decision (D-02/NL-EVAL-05): the full instance graph is now
  vendored as `raw/prod-inst.ttl` so the execution-based answer-set judge
  has real data to run gold and candidate SPARQL against. The schema-only
  `raw/prod-inst-schema.ttl` extract is kept alongside it, unchanged, for
  the ontology build.
- **No other content changes.** `questions.yml` is vendored verbatim
  (no edits).

## Downstream use

`convert_ck25.py` (this directory) converts `raw/questions.yml` +
`raw/prod-inst-schema.ttl` into `corpus.yml` (a `phys:`-annotated ontology
plus the surviving D-06-filtered cases) for this repo's NL→SPARQL eval
harness (`tests/nl2sparql/eval/runner.py`). See `filter_log.md` for the
kept/dropped audit trail.

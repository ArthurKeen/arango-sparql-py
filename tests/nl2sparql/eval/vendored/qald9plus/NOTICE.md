# NOTICE — QALD-9-plus (English, DBpedia)

**Dataset:** QALD-9-plus (DBpedia English subset)
**Source repository:** https://github.com/KGQA/QALD_9_plus
**Vendored commit:** `b9fb0380902e6935efc9505170bc3652e8c242c1` (fetched 2026-07-22)
**Vendored files:** `data/qald_9_plus_train_dbpedia.json`, `data/qald_9_plus_test_dbpedia.json`
**License:** CC-BY-4.0 (Creative Commons Attribution 4.0 International)
**License text:** https://github.com/KGQA/QALD_9_plus/blob/main/LICENSE
**License summary:** https://creativecommons.org/licenses/by/4.0/

## Attribution

QALD-9-plus is produced by the KGQA research group as an improved,
multilingual re-release of the QALD-9 benchmark (Question Answering over
Linked Data). This directory vendors the English-language, DBpedia-target
subset of the combined `train` + `test` splits.

If you use this data, please cite the QALD-9-plus dataset and repository:
`github.com/KGQA/QALD_9_plus`.

## Changes made

The two upstream JSON files (`qald_9_plus_train_dbpedia.json`,
`qald_9_plus_test_dbpedia.json`) were pruned to an English-only extract
before vendoring, per this project's NL-BENCH-06 minimal-vendoring
requirement:

- Kept, per question: `id`, the **first** `language == "en"` question
  string, and `query.sparql` (the gold SPARQL query).
- Removed every non-English question paraphrase (the upstream files carry
  question strings in ~10 additional languages per entry).
- Removed the `answers` result-binding blobs entirely (not needed by this
  repo's SPARQL-vs-SPARQL canonical-algebra judge).

No other content was modified. The pruned extracts live at
`raw/qald_9_plus_train_dbpedia_en.json` and
`raw/qald_9_plus_test_dbpedia_en.json`.

This is a further, D-06-filtered derivative of the pruned extracts —
see `filter_log.md` for the kept/dropped audit trail and `corpus.yml` for
the final adopted case set.

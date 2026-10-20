---
title: "SPARQL Witness: finding transpiler bugs by generating data that makes two engines disagree"
status: "draft spec for a lab spike (P.J., 2026-10-19)"
inspired_by: "SQLWitness — An and Rigger, Efficient Dialect-Agnostic SQL Equivalence Refutation, SIGMOD 2026, arXiv:2610.22442"
---

# SPARQL Witness

## The problem

The library translates SPARQL to AQL. A translation is correct only if both queries return the
same answers on **every** dataset. Today that is checked on fixed data: the W3C conformance files and
hand-written fixtures (pyoxigraph as the reference, `tests/cross/`, helpers in `tests/helpers/oxi.py`
and `tests/helpers/aql_interp.py`). Translation bugs often appear only on particular data — a missing
optional value, a duplicate, an empty group, a value of an unexpected type — that fixed fixtures do
not contain. Recent examples found by accident: `COUNT(?o)` over a variable the query never binds
returning 0 (the lab's review-M1), and the ORDER BY/LIMIT class of defect.

## The idea (adapted from SQLWitness)

Proving two queries equivalent is undecidable in general; proving them **different** needs one
dataset on which their results differ — a *witness*. Generate small datasets designed to exercise
the query's own conditions, load each into both engines, run the SPARQL on pyoxigraph and the
translated AQL on ArangoDB, and compare. Every witness reported is genuine by construction: the
difference was observed. No model, no human labels.

## Design

1. **Query-aware candidate pools.** For each variable and property the query touches, build a small
   pool of values from: the literals in the query (and their neighbours: boundaries of FILTER
   comparisons, equal / just above / just below), absence (the triple missing — SPARQL's unbound, the
   document's missing field), duplicates, multiple values for one property, mixed datatypes
   (`xsd:integer` vs `xsd:decimal` vs plain string), language tags, and IRIs vs literals.
2. **Shape-aware generation.** Generate subjects per `rdf:type` the query names, routed to
   collections exactly as the cross tests do, respecting the mapping's keys and edge collections so
   every dataset is valid for the AQL side. Keep datasets tiny (tens of triples) so a witness is
   readable.
3. **Coverage-guided feedback.** Estimate which of the query's patterns, FILTER branches, OPTIONAL
   arms, groups and aggregates each dataset exercises (count matching solutions per pattern on
   pyoxigraph). Bias generation toward unexercised parts; stop a pair when coverage stops growing
   (the paper's early-termination estimator, simplified).
4. **Comparison.** Compare solution multisets after the existing normalisation (`normalize_oxi_row`,
   null handling); compare order only when the query has ORDER BY over a total order. A difference is
   classified as: **witness** (a translation bug), **unsupported** (the translator refuses or the
   feature is outside the supported subset — not a bug), or **harness** (a normalisation gap in the
   comparison itself — fix the harness, never count it as a bug).
5. **Shrinking.** Reduce each witness to the smallest dataset that still shows the difference, and
   emit it as a ready-to-run regression test in the `tests/cross/` style (data file + query +
   expected reference bindings).

## Seed queries

In order: the W3C conformance queries the translator supports; the SPARQL keys of the CDF lab's
accepted question sets (`fact-v0`, which are generated, executable SPARQL); CK25's gold queries;
then queries mutated from these (drop a FILTER, swap a comparison, add OPTIONAL, add ORDER BY/LIMIT,
add COUNT/GROUP BY over an optional variable).

## Engines

pyoxigraph is the reference. The AQL side runs first on the in-process interpreter (fast, no
server), then on a real ArangoDB (the local docker estate) for every candidate witness, since only a
difference on the real engine counts.

## How we judge the spike (free truth)

- **Rediscovery:** does it independently find the known unbound-`COUNT` defect without being told?
- **Yield:** witnesses per 100 seed queries, unsupported rate, harness-difference rate (target 0 after
  fixes), and time per pair (the paper reports about 0.12 s; ours will be slower across two engines).
- **Quality:** every reported witness reproduces on real ArangoDB and shrinks to a dataset a person
  can read in a minute.

## Out of scope for the spike

Federated CDF queries (a later variant: CDF's planner against a single pyoxigraph store over Forge
data); proving equivalence; fixing the bugs found — witnesses become tickets or regression tests,
fixes are separate work.

## Why it matters now

It turns "we found this transpiler bug by luck" into a search; it gives every library change a
data-driven regression check; and if the SPARQL engine moves to Oxigraph (Arthur's Rust study), it is
the tool that shows the move preserves behaviour.

"""Leakage gate for the curated few-shot bank (Phase 7 Plan 02, D-01/D-02/B2).

``fewshot_bank.yml`` supplies the question -> gold-SPARQL exemplars the
dense/BM25 few-shot retrievers rank over. This module is the committed,
after-the-fact proof that the bank does NOT contaminate the held-out eval
``corpus.yml``:

1. ``test_bank_disjoint_from_eval_corpus`` -- THREE-way disjointness:
   normalized question text, canonical algebra (alpha-equivalence aware, via
   the existing ``runner._canonical`` judge), and a canonical-algebra
   SKELETON (concrete literals/URIs abstracted) so neither a paraphrase, a
   re-spelled-but-equivalent gold, nor a numerically-nudged near-clone
   (``:age 30`` vs ``:age 40``) can smuggle a corpus case into the bank.
2. ``test_bank_similarity_ceiling`` -- a cosine SIMILARITY CEILING (< 0.95)
   using the same pinned embedding model the dense retriever uses, so a
   near-clone paraphrase can never clear the gate and hand the model a
   template. Skips (does not fail) when the dense stack is not installed --
   this plan is independent of 07-01/07-03's dense-stack sync.
3. ``test_every_bank_gold_parses`` -- a bank exemplar that cannot parse is a
   broken few-shot example.
4. ``test_bank_ontology_matches_corpus`` -- WARNING 4: the duplicated
   ``ontology:`` Turtle block cannot silently drift between the two files.

A collision surfaced by this gate is a signal to RE-AUTHOR the offending
bank item, never to nudge a literal to dodge the check (B2).

Key-free / mostly no-network: reuses ``runner._canonical``/``_load_corpus``
(the existing canonical-algebra judge) and mirrors ``test_gold_transpilable.py``'s
``pytest.mark.eval`` + ``RUN_EVAL`` skip idiom. Only the similarity-ceiling
test touches a (locally cached, pinned-revision) sentence-transformers model,
and it degrades to a skip rather than a hard failure when that stack is
absent.
"""

from __future__ import annotations

import os
import re
import statistics
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.nl2sparql.eval.runner import _canonical, _load_corpus

# Same "off" semantics as test_eval.py: treat "", "0", "false", "no" as off.
_RUN_EVAL = os.getenv("RUN_EVAL", "").strip().lower() not in ("", "0", "false", "no")

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(not _RUN_EVAL, reason="set RUN_EVAL=1 to run the NL eval gate"),
]

EVAL_DIR = Path(__file__).parent
BANK_PATH = EVAL_DIR / "fewshot_bank.yml"
REPORTS_DIR = EVAL_DIR / "reports"

# Cosine similarity ceiling (B2, committed literal): with all-MiniLM-L6-v2,
# genuine paraphrases / near-duplicate questions land at cosine >= 0.95. A
# nearest bank neighbour below this ceiling for EVERY eval-corpus question
# proves the bank item is a materially different question, not a reworded
# corpus clone that would leak a template through dense retrieval.
_SIMILARITY_CEILING = 0.95

_LITERAL_RE = re.compile(r"Literal\([^)]*\)")
_URI_RE = re.compile(r"(?:rdflib\.term\.)?URIRef\('[^']*'\)")


def _normalize_question(q: str) -> str:
    return re.sub(r"[^\w\s]", "", q.strip().lower())


def _skeleton(sparql: str) -> str | None:
    """Canonical algebra with concrete literals/URIs abstracted to placeholders.

    Builds on ``_canonical`` (already alpha-equivalence-aware over
    variables) and additionally regex-blanks every ``Literal(...)`` value to
    ``?LIT`` and every quoted ``URIRef('...')`` triple-position token
    (class/predicate references) to ``?URI``, so structure alone remains:
    ``:age 30`` and ``:age 40`` collapse to the SAME skeleton and must
    therefore be rejected as a collision.

    Property-path predicates (rdflib's ``Path`` objects, e.g.
    ``Path(<http://ex.org/knows> / <http://ex.org/name>)``) are NOT matched
    by the quoted ``URIRef('...')`` pattern, so property-path bank/corpus
    items keep their real predicate identity -- appropriate, since the
    bank's property-path examples deliberately reuse the same small set of
    graph edges (``:knows``/``:placed``) the corpus does, and only the
    concrete operator + predicate combination (not a blanked placeholder)
    distinguishes one path query from another.
    """
    canon = _canonical(sparql)
    if canon is None:
        return None
    canon = _LITERAL_RE.sub("?LIT", canon)
    canon = _URI_RE.sub("?URI", canon)
    return canon


def _load_bank() -> dict[str, Any]:
    return yaml.safe_load(BANK_PATH.read_text(encoding="utf-8")) or {}


def _bank_examples() -> list[dict[str, str]]:
    return _load_bank().get("examples", [])


def _positive_corpus_cases() -> list[dict[str, Any]]:
    """Every corpus case WITHOUT ``expect_refusal`` -- the only cases with
    gold SPARQL to compare against (refusal cases carry a rationale, not a
    gold query the bank could possibly leak)."""
    corpus = _load_corpus()
    return [c for c in corpus["cases"] if not c.get("expect_refusal")]


def test_bank_disjoint_from_eval_corpus() -> None:
    """The bank shares NO case with the eval corpus, proven THREE ways.

    (1) normalized question text, (2) canonical algebra (alpha-equivalence
    aware), and (3) canonical-algebra SKELETON (literals/URIs abstracted) --
    so neither a paraphrased question, a re-spelled-but-equivalent gold, nor
    a numerically-nudged near-clone can smuggle a corpus case into the
    retrieval pool (D-02, B2).
    """
    corpus_cases = _positive_corpus_cases()
    bank = _bank_examples()

    corpus_questions = {_normalize_question(c["nl"]) for c in corpus_cases}
    bank_questions = {_normalize_question(e["question"]) for e in bank}
    overlap_q = corpus_questions & bank_questions
    assert not overlap_q, f"bank questions overlap eval corpus (normalized text): {overlap_q}"

    corpus_canon = {_canonical(c["expected"]) for c in corpus_cases}
    corpus_canon.discard(None)
    bank_canon = {_canonical(e["query"]) for e in bank}
    bank_canon.discard(None)
    overlap_c = corpus_canon & bank_canon
    assert not overlap_c, f"bank gold SPARQL overlaps eval corpus (canonical algebra): {overlap_c}"

    corpus_skel = {_skeleton(c["expected"]) for c in corpus_cases}
    corpus_skel.discard(None)
    bank_skel = {_skeleton(e["query"]) for e in bank}
    bank_skel.discard(None)
    overlap_s = corpus_skel & bank_skel
    assert not overlap_s, (
        "bank gold SPARQL overlaps eval corpus at the SKELETON level "
        f"(literals/URIs abstracted -- e.g. `:age 30` vs `:age 40`): {overlap_s}"
    )


def test_every_bank_gold_parses() -> None:
    """A bank exemplar that cannot parse is a broken few-shot example."""
    for example in _bank_examples():
        assert _canonical(example["query"]) is not None, (
            f"bank gold for question {example['question']!r} does not parse via rdflib"
        )


def test_bank_ontology_matches_corpus() -> None:
    """WARNING 4: the duplicated ``ontology:`` Turtle block cannot silently drift."""
    corpus = _load_corpus()
    bank = _load_bank()
    assert bank.get("ontology") == corpus.get("ontology"), (
        "fewshot_bank.yml's `ontology:` block has drifted from corpus.yml's -- "
        "keep them byte-identical (WARNING 4)"
    )


def test_bank_similarity_ceiling() -> None:
    """B2 near-clone gate: no eval-corpus question sits too close to any bank item.

    Skips (does not fail) when the dense stack (``sentence-transformers`` +
    the 07-01 pinned embedding model constants in
    ``arango_query_core.nl.fewshot``) is not importable -- this plan is
    independent of the engine work (07-01) and 07-03's ``uv sync --extra
    dense``; the ceiling becomes an ACTIVE gate from Wave 2 onward, at the
    latest before the 07-04 lift is recorded.

    Also RECORDS the nearest-neighbor bank<->corpus similarity distribution
    (min/median/max cosine + top-5 closest pairs) to stdout and to a
    gitignored ``reports/fewshot_similarity.md`` so a reviewer can rule out
    memorization -- Plan 04's sweep report surfaces this distribution
    alongside the measured lift.
    """
    try:
        from arango_query_core.nl.fewshot import (
            DEFAULT_DENSE_MODEL_ID,
            DEFAULT_DENSE_REVISION,
        )
        from sentence_transformers import SentenceTransformer
    except ImportError:
        pytest.skip(
            "dense stack not installed (sentence-transformers / "
            "arango_query_core.nl.fewshot) -- similarity ceiling becomes "
            "active once the dense extra is synced (07-03)"
        )

    corpus_questions = [c["nl"] for c in _positive_corpus_cases()]
    bank_questions = [e["question"] for e in _bank_examples()]

    model = SentenceTransformer(DEFAULT_DENSE_MODEL_ID, revision=DEFAULT_DENSE_REVISION)
    corpus_emb = model.encode(corpus_questions, normalize_embeddings=True)
    bank_emb = model.encode(bank_questions, normalize_embeddings=True)

    nearest: list[tuple[float, str, str]] = []
    for cq, cvec in zip(corpus_questions, corpus_emb, strict=True):
        scores = bank_emb @ cvec
        best_idx = int(scores.argmax())
        nearest.append((float(scores[best_idx]), cq, bank_questions[best_idx]))

    cosines = [n[0] for n in nearest]
    max_cos = max(cosines)
    min_cos = min(cosines)
    median_cos = statistics.median(cosines)
    closest = sorted(nearest, key=lambda n: -n[0])[:5]

    print(
        f"\nNearest-neighbor bank<->corpus cosine distribution: "
        f"min={min_cos:.4f} median={median_cos:.4f} max={max_cos:.4f} "
        f"ceiling={_SIMILARITY_CEILING}"
    )
    print("Top-5 closest (cosine, corpus question, bank question) pairs:")
    for cos, cq, bq in closest:
        print(f"  {cos:.4f}  corpus={cq!r}  bank={bq!r}")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_lines = [
        "# fewshot_bank.yml <-> corpus.yml nearest-neighbor similarity",
        "",
        f"min={min_cos:.4f} median={median_cos:.4f} max={max_cos:.4f} "
        f"ceiling={_SIMILARITY_CEILING}",
        "",
        "| cosine | corpus question | nearest bank question |",
        "|---|---|---|",
    ]
    for cos, cq, bq in closest:
        report_lines.append(f"| {cos:.4f} | {cq} | {bq} |")
    (REPORTS_DIR / "fewshot_similarity.md").write_text("\n".join(report_lines) + "\n")

    assert max_cos < _SIMILARITY_CEILING, (
        f"a bank item sits too close (cosine={max_cos:.4f} >= {_SIMILARITY_CEILING}) "
        f"to an eval-corpus question -- possible near-clone leakage: {closest[0]}"
    )

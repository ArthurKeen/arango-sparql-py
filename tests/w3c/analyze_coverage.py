#!/usr/bin/env python3
"""Translation-only W3C SPARQL 1.1 DAWG coverage analyzer.

Mirrors :mod:`references.arango_cypher_py.tests.tck.analyze_coverage` —
walks every manifest under ``tests/w3c/data/``, attempts to parse and
translate each test case, and prints a Markdown coverage table plus a
short list of the most common skip reasons.

Run::

    python tests/w3c/analyze_coverage.py
    python tests/w3c/analyze_coverage.py --write   # rewrite COVERAGE_REPORT.md

No live ArangoDB is required — this is the upper-bound translation
coverage. End-to-end coverage will be lower until the AQL executor
lands behind the ``integration`` marker.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import warnings
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# rdflib emits noisy ``logger.warning`` lines about borderline-valid IRIs
# while parsing the W3C corpus (e.g. ``http://example/c:d\?``). They are
# not actionable here — quiet them so the Markdown output isn't polluted
# when ``--write`` is not used.
logging.getLogger("rdflib").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=UserWarning, module=r"rdflib(\..*)?")

# Allow ``python tests/w3c/analyze_coverage.py`` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from arango_sparql.api import translate
from arango_sparql.errors import (
    AqlEmitError,
    SchemaResolutionError,
    SparqlParseError,
    UnsupportedSparqlError,
)
from arango_sparql.translate.parser import parse_sparql
from arango_sparql.translate.resolver import SchemaResolver
from tests.w3c.runner import (
    NEG_SYNTAX_11,
    OUT_OF_SCOPE_TYPES,
    POS_SYNTAX_11,
    QUERY_EVAL,
    W3CTestCase,
    collect_cases,
    w3c_corpus_root,
)

# What the harness treats as a "pass":
#  - QueryEvaluationTest: translation produced non-empty AQL.
#  - PositiveSyntaxTest11: rdflib parsed the query.
#  - NegativeSyntaxTest11: parser raised SparqlParseError.
#
# Anything else is an XFAIL bucket (still counted, but tracked so the
# coverage report makes the gap visible).


@dataclass
class CategoryStats:
    total: int = 0
    passed: int = 0
    xfailed: int = 0
    failed: int = 0
    skipped: int = 0
    xfail_reasons: Counter = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.xfail_reasons is None:
            self.xfail_reasons = Counter()

    @property
    def coverage(self) -> float:
        return (self.passed / self.total * 100.0) if self.total else 0.0


def _empty_resolver() -> SchemaResolver:
    """Build a permissive :class:`SchemaResolver` for the translation-
    only coverage analyzer.

    The harness intentionally runs every query against an empty
    ontology — its purpose is to measure the *visitor*'s coverage,
    not the operator's schema-mapping discipline. The
    ``permissive_class_resolution=True`` flag lets unknown class IRIs
    degrade to the default ``Document`` collection rather than
    raising, mirroring how :meth:`SchemaResolver.resolve_property`
    already handles unmapped property IRIs. This converts the bulk
    of the historical ``schema`` XFAIL bucket into measurable
    translation PASSes; the ``algebra`` bucket continues to track
    real roadmap gaps.
    """
    return SchemaResolver.from_turtle(
        "",
        default_collection="Document",
        permissive_class_resolution=True,
    )


def _read(case: W3CTestCase) -> str | None:
    if case.query_path is None or not case.query_path.is_file():
        return None
    return case.query_path.read_text(encoding="utf-8")


def _classify_query_eval(case: W3CTestCase) -> tuple[str, str]:
    query = _read(case)
    if query is None:
        return "skipped", "missing query file"
    try:
        result = translate(query, resolver=_empty_resolver())
    except UnsupportedSparqlError as exc:
        return "xfailed", f"UnsupportedSparql: {_short(exc)}"
    except SchemaResolutionError as exc:
        return "xfailed", f"SchemaResolution: {_short(exc)}"
    except AqlEmitError as exc:
        return "xfailed", f"AqlEmit: {_short(exc)}"
    except SparqlParseError as exc:
        return "xfailed", f"SparqlParse: {_short(exc)}"
    if not result.aql:
        return "failed", "empty AQL"
    return "passed", ""


def _classify_positive_syntax(case: W3CTestCase) -> tuple[str, str]:
    query = _read(case)
    if query is None:
        return "skipped", "missing query file"
    try:
        parse_sparql(query)
    except SparqlParseError as exc:
        return "xfailed", f"rdflib parse failure: {_short(exc)}"
    return "passed", ""


def _classify_negative_syntax(case: W3CTestCase) -> tuple[str, str]:
    query = _read(case)
    if query is None:
        return "skipped", "missing query file"
    try:
        parse_sparql(query)
    except SparqlParseError:
        return "passed", ""
    return "xfailed", "rdflib accepted invalid query"


def _short(exc: Exception) -> str:
    s = str(exc)
    return s[:80] + ("..." if len(s) > 80 else "")


# ---------------------------------------------------------------------------
# XFAIL-reason → "implication bucket" classifier
# ---------------------------------------------------------------------------
#
# The same Markdown report has historically used a single "port the
# corresponding visitor method" implication line for *every* XFAIL
# bucket. That is misleading: roughly half of the W3C DAWG XFAILs the
# harness reports are not algebra gaps but artefacts of the fact that
# ``analyze_coverage`` runs every query against an EMPTY resolver
# (``SchemaResolver.from_turtle("", default_collection="Document")``).
# Queries that name owl:Restriction / owl:DatatypeProperty / arbitrary
# ad-hoc test classes therefore fail at schema-resolution time even
# though the visitor is perfectly capable of translating them when
# given the matching ontology.
#
# Categorising each reason as one of four buckets lets the PRD §13.5
# tracker — and the next person picking a slice — distinguish:
#
#   * ``algebra``  — real roadmap gap; porting a visitor method moves
#                    the W3C pass-count directly.
#   * ``schema``   — harness artefact; would pass against a populated
#                    resolver. Roadmap fix is either to enhance the
#                    harness (per-fixture mini-ontologies) or to
#                    accept these as a permanent measurement floor.
#   * ``rdflib``   — rdflib parser disagrees with the W3C grammar
#                    (negative-syntax tests). Out of our hands short
#                    of forking rdflib.
#   * ``other``    — uncategorised; surface for manual triage.
#
# Mapping is by substring match on the truncated reason string the
# Counter already keys on. The substrings are deliberately short and
# stable (they live in ``arango_sparql/errors.py`` and the visitor's
# ``UnsupportedSparqlError`` messages).


BUCKET_ALGEBRA = "algebra"
BUCKET_SCHEMA = "schema"
BUCKET_RDFLIB = "rdflib"
BUCKET_OTHER = "other"

_BUCKET_RULES: tuple[tuple[str, str], ...] = (
    # rdflib disagreements (negative-syntax XFAILs) come first because
    # the substring ``rdflib`` could otherwise be matched by a future
    # algebra error that mentions rdflib in its message.
    ("rdflib accepted invalid query", BUCKET_RDFLIB),
    ("rdflib parse failure", BUCKET_RDFLIB),
    # Schema-resolution failures are the empty-resolver artefacts —
    # the visitor never got to the algebra step.
    ("SchemaResolution:", BUCKET_SCHEMA),
    # Everything else under ``UnsupportedSparql:`` / ``AqlEmit:`` /
    # ``SparqlParse:`` is, by construction, a real algebra-or-emit
    # gap the roadmap can close.
    ("UnsupportedSparql:", BUCKET_ALGEBRA),
    ("AqlEmit:", BUCKET_ALGEBRA),
    ("SparqlParse:", BUCKET_ALGEBRA),
)


def _bucket(reason: str) -> str:
    """Classify an XFAIL reason string into one of four buckets.

    The mapping is intentionally substring-based and stable: every
    reason the harness emits is prefixed with the error class
    (``UnsupportedSparql:`` / ``SchemaResolution:`` / etc.) or — for
    negative-syntax tests — the fixed phrase ``rdflib accepted invalid
    query``. Adding a new error class downstream requires a one-line
    update here, surfaced by ``test_bucket_classifier``.
    """
    for needle, bucket in _BUCKET_RULES:
        if needle in reason:
            return bucket
    return BUCKET_OTHER


_BUCKET_IMPLICATION: dict[str, str] = {
    BUCKET_ALGEBRA: "port the corresponding visitor method",
    BUCKET_SCHEMA: (
        "real schema-resolution failure even under permissive mode "
        "(should be 0 — investigate any non-zero count)"
    ),
    BUCKET_RDFLIB: "rdflib parser disagreement; out of scope here",
    BUCKET_OTHER: "uncategorised — triage manually",
}


def _classify(case: W3CTestCase) -> tuple[str, str]:
    if case.test_type == QUERY_EVAL:
        return _classify_query_eval(case)
    if case.test_type == POS_SYNTAX_11:
        return _classify_positive_syntax(case)
    if case.test_type == NEG_SYNTAX_11:
        return _classify_negative_syntax(case)
    if case.test_type in OUT_OF_SCOPE_TYPES:
        return "skipped", f"out-of-scope test type: {case.test_type}"
    return "skipped", f"unknown test type: {case.test_type}"


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _heading(title: str) -> list[str]:
    return ["", title, "=" * len(title)]


def _format_markdown(
    by_category: dict[str, CategoryStats],
    live_stats: CategoryStats | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# W3C SPARQL 1.1 DAWG coverage — measured")
    lines.append("")
    lines.append(
        "> Methodology: translation-only dry run "
        "(`python tests/w3c/analyze_coverage.py`). Each query is parsed "
        "and (for evaluation tests) handed to "
        "`arango_sparql.api.translate`. A scenario passes when:"
    )
    lines.append(">")
    lines.append("> * **Syntax (positive)** — `rdflib` accepts the query;")
    lines.append(
        "> * **Syntax (negative)** — `rdflib` raises a `SparqlParseError` (the test deliberately ill-formed);"
    )
    lines.append(
        "> * **Query evaluation** — the visitor produces non-empty AQL "
        "without raising `UnsupportedSparqlError`."
    )
    if live_stats is not None:
        lines.append(
            "> * **Live execution** — the translated AQL was run against a "
            "real ArangoDB and the bindings matched the W3C-expected "
            "`.srx` results."
        )
    lines.append("")
    lines.append(
        "Low query-evaluation coverage is *expected* in v0 and tracks our "
        "progress as visitor methods are ported from "
        "`references/arango-sparql/src/lib/`."
    )
    lines.append("")

    lines.append("## Headline numbers")
    lines.append("")
    lines.append("| Category | Total | Pass | Fail | Xfail | Skip | Coverage |")
    lines.append("| -------- | -----:| ----:| ----:| -----:| ----:| --------:|")
    order = [
        ("Syntax (positive)", POS_SYNTAX_11),
        ("Syntax (negative)", NEG_SYNTAX_11),
        ("Query evaluation", QUERY_EVAL),
    ]
    for label, key in order:
        stats = by_category.get(key, CategoryStats())
        lines.append(
            f"| {label} | {stats.total} | {stats.passed} | {stats.failed} | "
            f"{stats.xfailed} | {stats.skipped} | {stats.coverage:.1f}% |"
        )
    if live_stats is not None:
        # Live execution row always reports against the *translatable*
        # subset (the only set of cases the live harness can attempt),
        # so its denominator is intentionally smaller than the
        # Query-evaluation row's. Read it as "of the cases translation
        # accepts today, how many AQL-execute to the spec-correct
        # bindings".
        lines.append(
            f"| Live execution | {live_stats.total} | {live_stats.passed} | "
            f"{live_stats.failed} | {live_stats.xfailed} | {live_stats.skipped} | "
            f"{live_stats.coverage:.1f}% |"
        )
    lines.append("")

    out_keys = sorted(k for k in by_category if k in OUT_OF_SCOPE_TYPES)
    if out_keys:
        lines.append("## Out-of-scope test types (counted, not run)")
        lines.append("")
        lines.append("| Test type | Total | Reason |")
        lines.append("| --------- | -----:| ------ |")
        oos_reason = (
            "SPARQL 1.1 Update / Protocol / Service-Description / CSV "
            "result-format are not v0 targets — the transpiler ports query "
            "semantics first."
        )
        for key in out_keys:
            stats = by_category[key]
            lines.append(f"| `mf:{key}` | {stats.total} | {oos_reason} |")
        lines.append("")

    aggregate: Counter = Counter()
    for stats in by_category.values():
        aggregate.update(stats.xfail_reasons)

    # Per-bucket roll-up tells the reader at a glance how many of the
    # XFAILs are actionable roadmap items vs. harness artefacts. This
    # is the table the PRD §13.5 tracker reads from when planning the
    # next slice.
    bucket_totals: Counter = Counter()
    for reason, count in aggregate.items():
        bucket_totals[_bucket(reason)] += count

    lines.append("## XFAIL implication summary")
    lines.append("")
    lines.append(
        "Each XFAIL is bucketed by what fixing it would require — "
        "this distinguishes real roadmap gaps (``algebra``) from "
        "out-of-our-hands rdflib disagreements (``rdflib``). The "
        "translation-only harness runs every query against a permissive "
        "empty resolver (`SchemaResolver.from_turtle('', "
        "default_collection='Document', permissive_class_resolution="
        "True)`), so unknown class IRIs degrade to the default collection "
        "rather than masking algebra gaps behind schema XFAILs."
    )
    lines.append("")
    lines.append("| Bucket | Count | Implication |")
    lines.append("| ------ | -----:| ----------- |")
    for bucket in (BUCKET_ALGEBRA, BUCKET_SCHEMA, BUCKET_RDFLIB, BUCKET_OTHER):
        count = bucket_totals.get(bucket, 0)
        if count == 0 and bucket == BUCKET_OTHER:
            # Don't print an empty ``other`` row — it adds noise. The
            # other three buckets are always shown even at zero so the
            # column structure is stable across runs.
            continue
        lines.append(
            f"| `{bucket}` | {count} | {_BUCKET_IMPLICATION[bucket]} |"
        )
    lines.append("")

    lines.append("## Top XFAIL reasons")
    lines.append("")
    lines.append("| Count | Bucket | Reason | Implication |")
    lines.append("| -----:| ------ | ------ | ----------- |")
    for reason, count in aggregate.most_common(15):
        bucket = _bucket(reason)
        implication = _BUCKET_IMPLICATION[bucket]
        lines.append(
            f"| {count} | `{bucket}` | `{reason}` | {implication} |"
        )
    if not aggregate:
        lines.append("| _(none)_ |  |  |  |")
    lines.append("")

    if live_stats is not None and live_stats.xfail_reasons:
        # Live-execution divergences have very different implications
        # from translation-time xfails — they're spec-vs-AQL gaps the
        # operator needs to hand-verify, not "port a visitor method".
        # Surface them in their own table so the two are not visually
        # conflated in PR review.
        lines.append("## Live-execution divergences")
        lines.append("")
        lines.append("| Count | Test ID | Divergence reason |")
        lines.append("| -----:| ------- | ----------------- |")
        for reason, count in live_stats.xfail_reasons.most_common(20):
            lines.append(f"| {count} | _(see test)_ | `{reason}` |")
        lines.append("")

    lines.append("## How to reproduce")
    lines.append("")
    lines.append("```bash")
    lines.append("python tests/w3c/analyze_coverage.py            # print")
    lines.append("python tests/w3c/analyze_coverage.py --write    # update this file")
    lines.append("pytest -q tests/w3c -m w3c                      # full pytest run")
    lines.append("RUN_INTEGRATION=1 python tests/w3c/analyze_coverage.py --live --write")
    lines.append("                                                # include live execution row")
    lines.append("```")
    lines.append("")
    if live_stats is None:
        lines.append(
            "End-to-end (live ArangoDB) coverage is computed by re-running "
            "with `--live` after `RUN_INTEGRATION=1` is set; without it "
            "the live row is omitted so the report stays reproducible "
            "without Docker."
        )
    else:
        lines.append(
            "Live-execution numbers are scoped to the translatable subset "
            "(cases that the visitor accepts today). They surface AQL ↔ "
            "SPARQL semantic divergences caught against a real ArangoDB."
        )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def analyze() -> dict[str, CategoryStats]:
    if w3c_corpus_root() is None:
        print(
            "W3C corpus not on disk; run scripts/fetch_w3c.sh first.",
            file=sys.stderr,
        )
        sys.exit(2)

    by_category: dict[str, CategoryStats] = {}
    for case in collect_cases():
        stats = by_category.setdefault(case.test_type, CategoryStats())
        stats.total += 1
        status, reason = _classify(case)
        if status == "passed":
            stats.passed += 1
        elif status == "xfailed":
            stats.xfailed += 1
            stats.xfail_reasons[reason] += 1
        elif status == "failed":
            stats.failed += 1
        else:
            stats.skipped += 1
    return by_category


def analyze_live() -> CategoryStats:
    """Run the live-execution suite via pytest and tally pass / xfail.

    Defers to :mod:`tests.w3c.test_w3c_live_execution` so the live
    row uses *exactly* the same gating, fixtures, and divergence
    registry as the live-execution test does — there is no drift
    between "what the harness reports" and "what the live test
    asserts".

    Requires ``RUN_INTEGRATION=1``; when unset the function returns
    a stats object with ``total=0`` so the caller knows to skip the
    row.
    """
    stats = CategoryStats()

    # Late import keeps the live-execution path off the default
    # analyze() codepath — translation-only mode never pulls in
    # python-arango, docker helpers, or the SRX comparator.
    from tests.w3c.test_w3c_live_execution import (
        _LIVE_CASES,
        SKIP_REASONS,
        is_live_mode_enabled,
    )

    if not is_live_mode_enabled():
        return stats  # caller treats total=0 as "live mode disabled"

    stats.total = len(_LIVE_CASES)

    # Re-running pytest from inside a script is awkward; instead we
    # spawn it as a subprocess and parse its summary line. This
    # mirrors the methodology in arango-cypher-py's live-coverage
    # tooling: one source of truth (pytest) drives both CI and the
    # coverage report.
    import subprocess

    repo_root = Path(__file__).resolve().parents[2]
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
        "tests/w3c/test_w3c_live_execution.py",
        "-m",
        "w3c and integration",
    ]
    proc = subprocess.run(
        cmd,
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=dict(os.environ),  # forward RUN_INTEGRATION + ARANGO_* env vars
    )
    summary = (proc.stdout or "") + (proc.stderr or "")
    # Pytest's compact summary line has the canonical counters: e.g.
    # ``2 passed, 36 xfailed in 4.21s``. The token immediately
    # before each counter name is its integer count.
    import re as _re

    def _scan(name: str) -> int:
        match = _re.search(rf"(\d+)\s+{name}\b", summary)
        return int(match.group(1)) if match else 0

    stats.passed = _scan("passed")
    stats.xfailed = _scan("xfailed")
    stats.failed = _scan("failed")
    stats.skipped = _scan("skipped")

    # Surface the divergence reasons we already know about so the
    # report's "Live-execution divergences" table has something to
    # render even when the operator can't run pytest in verbose
    # mode (CI shells frequently buffer test-by-test output).
    for reason in SKIP_REASONS.values():
        stats.xfail_reasons[reason] += 1

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__ or "")
    parser.add_argument(
        "--write",
        action="store_true",
        help="overwrite tests/w3c/COVERAGE_REPORT.md with the latest numbers",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "include a Live-execution row sourced from "
            "tests/w3c/test_w3c_live_execution.py. Requires "
            "RUN_INTEGRATION=1; when unset the live row is omitted."
        ),
    )
    args = parser.parse_args()

    by_category = analyze()
    live_stats: CategoryStats | None = None
    if args.live:
        live_stats = analyze_live()
        if live_stats.total == 0:
            # The flag was passed but the gate is closed — emit an
            # informational note rather than a silent omission so the
            # operator notices.
            print(
                "--live requested but RUN_INTEGRATION is unset; live row omitted.",
                file=sys.stderr,
            )
            live_stats = None

    report = _format_markdown(by_category, live_stats=live_stats)

    if args.write:
        out_path = Path(__file__).parent / "COVERAGE_REPORT.md"
        out_path.write_text(report, encoding="utf-8")
        print(f"wrote {out_path}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())

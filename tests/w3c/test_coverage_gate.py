"""SC4 non-regression gate: W3C DAWG QUERY_EVAL coverage must ASSERT >= 96.4%.

Phase 7 (this repo's NL->SPARQL dense few-shot retrieval work) touches no
transpiler code, so actual regression risk here is ~nil. But "risk is nil"
is not the same as "the gate asserts" — the existing `-m w3c` path is
xfail-tolerant (query-eval XFAILs at ANY coverage level, per the marker's own
design) and is EXCLUDED entirely from the per-PR `test` CI job. Neither of
those catches a real regression. This module is the COMMITTED, ASSERTING
gate (M4): it computes coverage via `tests.w3c.analyze_coverage.analyze()`
and FAILS the build if QUERY_EVAL coverage drops below the committed 96.4%
floor.

Deliberately carries NO `pytestmark` (no `w3c` marker) so it is never swept
into the xfail-tolerant `-m w3c` path or excluded by the `test` job's
`-m "not integration and not w3c and not eval"` filter — a dedicated CI job
selects this module by path instead (see `.github/workflows/ci.yml`,
job `w3c-coverage`).
"""

from __future__ import annotations

import pytest

from tests.w3c.analyze_coverage import analyze
from tests.w3c.runner import QUERY_EVAL, w3c_corpus_root

# The committed SC4 floor. Keep in sync with tests/w3c/COVERAGE_REPORT.md's
# headline Query-evaluation coverage number (currently 96.4%, 244/253).
_MIN_QUERY_EVAL_COVERAGE = 96.4


def test_query_eval_coverage_meets_sc4_floor() -> None:
    if w3c_corpus_root() is None:
        pytest.skip(
            "W3C corpus not on disk; run scripts/fetch_w3c.sh first "
            "(the w3c-coverage CI job does this before running this test)"
        )

    by_category = analyze()
    stats = by_category[QUERY_EVAL]
    assert stats.coverage >= _MIN_QUERY_EVAL_COVERAGE, (
        f"W3C DAWG QUERY_EVAL coverage regressed below the committed SC4 "
        f"floor: actual={stats.coverage:.2f}% "
        f"(passed={stats.passed}/{stats.total}), "
        f"required>={_MIN_QUERY_EVAL_COVERAGE}%"
    )

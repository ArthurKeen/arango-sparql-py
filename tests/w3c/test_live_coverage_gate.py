"""Non-regression gate for canonical document-edge W3C live coverage.

Unlike the translation-only coverage gate, this necessarily requires Docker
and an ArangoDB instance. It runs only in the integration CI lane, where it
asserts that the faithful document-edge profile never falls below the
committed round-trip baseline. The RPT profile is intentionally reported
separately during discovery; it does not share this denominator or gate.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import integration_enabled
from tests.w3c.analyze_coverage import analyze_live
from tests.w3c.runner import w3c_corpus_root

_DOCUMENT_EDGE_W3C_CASES = 191
_MIN_DOCUMENT_EDGE_LIVE_PASSES = 124

pytestmark = pytest.mark.integration


def test_document_edge_live_coverage_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    if not integration_enabled():
        pytest.skip("set RUN_INTEGRATION=1 to assert live W3C coverage")
    if w3c_corpus_root() is None:
        pytest.skip("W3C corpus not on disk; run scripts/fetch_w3c.sh first")

    monkeypatch.setenv("W3C_STORAGE_PROFILE", "document_edge")
    stats = analyze_live()
    assert stats.total == _DOCUMENT_EDGE_W3C_CASES, (
        "W3C live denominator changed; review corpus selection before "
        f"accepting the new metric: actual={stats.total}, "
        f"expected={_DOCUMENT_EDGE_W3C_CASES}"
    )
    assert stats.passed >= _MIN_DOCUMENT_EDGE_LIVE_PASSES, (
        "W3C document-edge live coverage regressed below its committed floor: "
        f"actual={stats.coverage:.2f}% "
        f"(passed={stats.passed}/{stats.total}), "
        f"required>={_MIN_DOCUMENT_EDGE_LIVE_PASSES}/{_DOCUMENT_EDGE_W3C_CASES}"
    )

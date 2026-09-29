"""The perf gate's own contract — no timing involved, so not marked ``perf``."""

from __future__ import annotations

import warnings

import pytest

from tests.perf import gate as gate_mod
from tests.perf.gate import Measurement, gate

BASELINE = {
    "slo_tolerance": 1.25,
    "ratio_warn_factor": 1.5,
    "slo_p95_ms": {"row": 12},
    "calibrated_median_ratio": {"row": 2.0},
}


@pytest.fixture(autouse=True)
def _baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate_mod, "load_baseline", lambda: BASELINE)


def _m(p95_ms: float, ratio: float = 2.0) -> Measurement:
    return Measurement(p95_ms=p95_ms, median_ratio=ratio, rounds_p95_ms=(p95_ms,))


def test_within_the_slo_passes_quietly() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        gate("row", _m(14.9))  # 12 * 1.25 = 15


def test_over_the_slo_fails_in_every_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The old gate was advisory whenever CI was set; this one is not."""
    monkeypatch.setenv("CI", "true")

    with pytest.raises(AssertionError, match="exceeds the PRD §9.4 SLO"):
        gate("row", _m(15.1))


def test_a_ratio_regression_warns_but_does_not_fail() -> None:
    with pytest.warns(UserWarning, match="likely a real regression"):
        gate("row", _m(5.0, ratio=3.1))  # > 2.0 * 1.5


def test_a_row_missing_its_slo_is_an_error_not_a_skip() -> None:
    with pytest.raises(AssertionError, match="no slo_p95_ms"):
        gate("unknown", _m(1.0))


def test_the_checked_in_baseline_carries_every_gated_row() -> None:
    from tests.perf.conftest import load_baseline

    baseline = load_baseline()
    rows = {"translate_cold_p95_ms", "translate_warm_p95_ms", "execute_overhead_p95_ms"}

    assert set(baseline["slo_p95_ms"]) == rows
    assert baseline["slo_p95_ms"] == {
        "translate_cold_p95_ms": 60,
        "translate_warm_p95_ms": 12,
        "execute_overhead_p95_ms": 80,
    }, "must mirror the PRD §9.4 table"
    assert set(baseline["calibrated_median_ratio"]) == rows

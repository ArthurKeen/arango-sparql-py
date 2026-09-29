"""Measurement and gating for the CI-blocking perf rows (PRD §3.12, §9.4).

Why this replaced the per-file ``_gate`` copies
-----------------------------------------------
The old gate compared a run's p95 with an absolute ``2.3 ms`` captured on one
laptop, ``* 1.25``, and enforced it only when the run's environment label
matched the baseline's. Every developer machine is ``"local"``, so the gate
bound on hardware it was never captured on and failed under ordinary load
(measured p95 3.4–6.0 ms with Docker running); CI is ``"ci"``, so the row
PRD §9.4 calls CI-blocking was only ever advisory there.

PRD §3.12 states the contract: each row passes "within ≤ 25 % of the
*stated* p95" — the SLO in the §9.4 table, not a snapshot. So:

* **Enforced, everywhere:** best-of-``ROUNDS`` p95 ``<= slo_p95_ms * 1.25``.
  Best-of-rounds because load only ever adds time — the fastest round is the
  closest estimate of the code's own cost.
* **Advisory tripwire:** each request is interleaved with a ``GET /health``
  through the same client and stack, and the *median* of their ratio is
  compared with a checked-in reference. Interleaving puts both under the same
  machine speed and load at the same moment, so the ratio is hardware-
  independent (±12 % across runs here, against ±60 % for absolute p95). A
  ratio over ``ratio_warn_factor`` × the reference warns long before the SLO
  would fail — early notice of a real regression without a flaky gate.
"""

from __future__ import annotations

import contextlib
import gc
import logging
import statistics
import time
import warnings
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from tests.perf.conftest import load_baseline, p95

ROUNDS = 3
N_ITER = 120
WARMUP = 20


@contextlib.contextmanager
def quiet_measurement() -> Iterator[None]:
    """Silence logging and hold off the cyclic GC for one measurement loop.

    Per-request ``logger.info`` under pytest's capture, and a GC pass landing
    mid-loop, are the two common single-iteration outliers unrelated to the
    measured work.
    """
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        gc.enable()
        logging.disable(previous)


@dataclass(frozen=True)
class Measurement:
    p95_ms: float
    median_ratio: float
    rounds_p95_ms: tuple[float, ...]


def measure(
    request: Callable[[int], None],
    calibrate: Callable[[], None],
    *,
    rounds: int = ROUNDS,
    n_iter: int = N_ITER,
    warmup: int = WARMUP,
) -> Measurement:
    """Time *request* (given a unique iteration index) interleaved with *calibrate*.

    *request* must assert its own response status: a fast failing request is
    not a fast request.
    """
    p95s: list[float] = []
    ratios: list[float] = []
    for r in range(rounds):
        timed: list[float] = []
        reference: list[float] = []
        with quiet_measurement():
            for i in range(n_iter):
                t0 = time.perf_counter()
                calibrate()
                reference.append(time.perf_counter() - t0)
                t0 = time.perf_counter()
                request(r * n_iter + i)
                timed.append(time.perf_counter() - t0)
        timed, reference = timed[warmup:], reference[warmup:]
        p95s.append(p95(timed) * 1000)
        ratios.append(statistics.median(timed) / statistics.median(reference))
    return Measurement(p95_ms=min(p95s), median_ratio=min(ratios), rounds_p95_ms=tuple(p95s))


def gate(row_key: str, measured: Measurement) -> None:
    """Enforce the SLO for *row_key*; warn when the calibrated ratio regresses."""
    baseline = load_baseline()
    slo = (baseline.get("slo_p95_ms") or {}).get(row_key)
    assert slo is not None, f"tests/perf/baseline.json has no slo_p95_ms[{row_key!r}] (PRD §9.4 table)"
    tolerance = float(baseline.get("slo_tolerance", 1.25))
    limit = slo * tolerance
    assert measured.p95_ms <= limit, (
        f"{row_key}: best-of-{len(measured.rounds_p95_ms)} p95={measured.p95_ms:.3f}ms exceeds the "
        f"PRD §9.4 SLO {slo}ms * {tolerance} = {limit:.3f}ms (rounds: "
        f"{', '.join(f'{v:.3f}' for v in measured.rounds_p95_ms)} ms)"
    )

    reference = (baseline.get("calibrated_median_ratio") or {}).get(row_key)
    if reference is None:
        warnings.warn(
            f"{row_key}: no calibrated_median_ratio reference yet; measured {measured.median_ratio:.2f}",
            stacklevel=2,
        )
        return
    warn_factor = float(baseline.get("ratio_warn_factor", 1.5))
    if measured.median_ratio > reference * warn_factor:
        warnings.warn(
            f"{row_key}: median cost is {measured.median_ratio:.2f}x a /health round-trip, against "
            f"a reference of {reference:.2f}x (warn above {reference * warn_factor:.2f}x) — "
            "likely a real regression, though still within the SLO",
            stacklevel=2,
        )

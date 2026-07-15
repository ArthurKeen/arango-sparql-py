"""The `@pytest.mark.eval` regression gate for the NL -> SPARQL harness.

Runs the no-network `scripted` config through the eval runner and asserts
its live pass-rate meets the checked-in `baseline.json` gate — both the
aggregate pass_rate and, per-case, that nothing which passed at baseline
time now regresses.

Gated behind `RUN_EVAL=1` (rule 200: "eval" is slow and never runs from a
plain `pytest` invocation) so the default local/CI fast path stays quick.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.nl2sparql.eval.runner import EVAL_DIR, run

pytestmark = pytest.mark.eval


@pytest.mark.skipif(not os.getenv("RUN_EVAL"), reason="set RUN_EVAL=1 to run the NL eval gate")
def test_scripted_pass_rate_meets_baseline() -> None:
    report = run("scripted")
    baseline = json.loads((EVAL_DIR / "baseline.json").read_text())["configs"]["scripted"]

    # Aggregate regression gate.
    assert report.pass_rate >= baseline["pass_rate"] - 1e-9, (
        f"scripted pass_rate regressed: live={report.pass_rate!r} "
        f"baseline={baseline['pass_rate']!r}"
    )

    # Per-case regression gate: any case that passed at baseline time must
    # still pass now, catching a swap that keeps the aggregate rate steady
    # while silently breaking a different case.
    live_by_name = {c.name: c.passed for c in report.cases}
    for name, was_passing in baseline["cases"].items():
        if was_passing:
            assert live_by_name.get(name) is True, (
                f"case {name!r} regressed: previously passing per baseline.json, "
                f"now {live_by_name.get(name)!r}"
            )

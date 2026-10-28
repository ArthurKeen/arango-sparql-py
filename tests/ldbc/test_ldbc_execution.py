"""Live tier of the LDBC ledger: execute on ArangoDB, compare with pyoxigraph.

Gated on ``RUN_INTEGRATION=1`` and ``RUN_LDBC=1``. For every cell the
committed ledger says translates, each binding is translated, executed on
that layout's database and compared with the oracle. The outcome must equal
the committed one: a ``translates_wrong`` cell can be recorded (so the
defect is visible) but never silently appear or disappear.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.ldbc import ledger, params

pytestmark = [pytest.mark.integration, pytest.mark.ldbc]

_TRANSLATING = [c for c in ledger.read_ledger().values() if c.translation == "translates"]


@pytest.mark.skipif(not _TRANSLATING, reason="no ledger cell translates yet")
@pytest.mark.parametrize("cell", _TRANSLATING, ids=lambda c: "/".join(c.key))
def test_live_outcome_matches_the_ledger(
    cell: ledger.Cell, ldbc_db: Callable[[str], Any], ldbc_data: tuple[Path, Any], ldbc_sf: str
) -> None:
    nt_path, summary = ldbc_data
    qtext = next(t for t in ledger.corpus() if (t.op, t.form) == (cell.op, cell.form))
    live = ledger.run_live(
        ldbc_db(cell.layout),
        qtext,
        cell.layout,
        params.load(ldbc_sf)[cell.op],
        nt_path,
        summary.sha256,  # type: ignore[arg-type]
    )
    assert (live.execution, live.oracle) == (cell.execution, cell.oracle), (
        f"live outcome changed: ledger has {cell.execution}/{cell.oracle}, got "
        f"{live.execution}/{live.oracle} ({live.detail}). Record it with "
        "`python -m tests.ldbc.ledger --live --write` once understood."
    )

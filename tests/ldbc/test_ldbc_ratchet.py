"""Per-PR ratchet over the LDBC ledger's translation tier (PRD §13.6 LDBC-3).

No data and no database: every query text is translated against the three
committed mapping fixtures and compared with ``conformance_ledger.tsv``.
Any change fails — a regression, or an improvement that was not recorded —
and the message says how to record it.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from tests.ldbc import ledger, params
from tests.ldbc.loader import LAYOUTS

pytestmark = pytest.mark.ldbc

UPSTREAM = Path(__file__).parent / "UPSTREAM.md"


def test_translation_tier_matches_the_committed_ledger() -> None:
    committed = ledger.read_ledger()
    current = ledger.translation_cells()
    assert {c.key for c in current} == set(committed), (
        "the corpus and the ledger disagree on which cells exist; run python -m tests.ldbc.ledger --write"
    )
    changed = []
    for cell in current:
        old = committed[cell.key]
        if (cell.translation, cell.translation_detail) != (old.translation, old.translation_detail):
            changed.append(f"{cell.key}: {old.translation} → {cell.translation} ({cell.translation_detail})")
    assert not changed, (
        "translation outcomes changed. If this is an improvement, record it with "
        "`python -m tests.ldbc.ledger --write` (and re-run --live); if it is a regression, fix it.\n"
        + "\n".join(changed)
    )


def test_no_cell_is_a_crash() -> None:
    crashes = [c.key for c in ledger.read_ledger().values() if c.translation == "crash"]
    assert not crashes, f"translator crashed (any non-SparqlError exception is a defect): {crashes}"


def test_every_text_has_a_row_per_layout() -> None:
    texts = ledger.corpus()
    assert len(texts) == 26  # 21 verbatim + 5 variants
    keys = set(ledger.read_ledger())
    for qtext in texts:
        for layout in LAYOUTS:
            assert (qtext.op, qtext.form, layout) in keys


def test_verbatim_files_are_byte_identical_to_upstream() -> None:
    """``UPSTREAM.md`` pins each verbatim file's SHA-256; they are never edited (plan D3)."""
    pinned = dict(
        re.findall(r"^\| `([\w-]+\.rq)` \| `([0-9a-f]{64})` \|$", UPSTREAM.read_text(), re.MULTILINE)
    )
    files = sorted((ledger.QUERIES / "interactive_v1").glob("*.rq"))
    assert len(files) == len(pinned) == 21
    for path in files:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == pinned[path.name], path.name


def test_every_variant_says_what_it_changed() -> None:
    for qtext in ledger.corpus():
        if qtext.form == ledger.VERBATIM:
            continue
        header = qtext.text.split("\nPREFIX", 1)[0]
        assert f"Variant of {qtext.op}.rq" in header and "Change:" in header, qtext.path.name
        assert all(not line.strip() or line.startswith("#") for line in header.splitlines()), qtext.path.name


def test_every_placeholder_is_bound_for_every_binding() -> None:
    bindings = params.load("0.1")
    for qtext in ledger.corpus():
        assert len(bindings[qtext.op]) == params.BINDINGS_PER_QUERY + 1, qtext.op
        for binding in bindings[qtext.op]:
            assert not params.placeholders(params.splice(qtext.text, binding)), qtext.op

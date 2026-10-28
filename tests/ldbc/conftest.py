"""Shared fixtures for the live LDBC tests.

Live tests need both ``RUN_INTEGRATION=1`` and ``RUN_LDBC=1``, and run
against the harness's own ArangoDB (``docker-compose.ldbc.yml``, booted on
demand; see ``databases.py``): the first run
downloads the data set and loads millions of documents, which an ordinary
integration run should not pay for. ``LDBC_SF`` picks the scale factor
(default ``0.1``, the smallest published Turtle).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.integration.conftest import integration_enabled


def ldbc_enabled() -> bool:
    return os.getenv("RUN_LDBC", "").lower() in ("1", "true", "yes")


@pytest.fixture(scope="session")
def ldbc_sf() -> str:
    return os.getenv("LDBC_SF", "0.1")


@pytest.fixture(scope="session")
def ldbc_data(ldbc_sf: str) -> tuple[Path, Any]:
    """``(normalized.nt, NormalizeSummary)`` for the scale factor, fetched on demand."""
    if not ldbc_enabled():
        pytest.skip("set RUN_LDBC=1 (and RUN_INTEGRATION=1) to run the live LDBC tests")
    from tests.ldbc import datasets, normalize

    files = datasets.turtle_files(ldbc_sf)
    return normalize.normalize(files, datasets.cache_root() / f"sf{ldbc_sf}-normalized")


@pytest.fixture(scope="session")
def ldbc_db(ldbc_sf: str, ldbc_data: tuple[Path, Any]) -> Callable[[str], Any]:
    """Return a function ``layout → database handle`` with that layout loaded."""
    if not integration_enabled():
        pytest.skip("set RUN_INTEGRATION=1 to enable integration tests")
    from tests.ldbc import databases, loader

    if not databases.reachable() and not databases.boot_via_compose():
        pytest.skip(f"the LDBC ArangoDB at {databases.ARANGO_URL} is unreachable (docker-compose.ldbc.yml)")

    nt_path, summary = ldbc_data
    handles: dict[str, Any] = {}

    def get(layout: str) -> Any:
        if layout not in handles:
            db = databases.open_database(ldbc_sf, layout)  # type: ignore[arg-type]
            loader.load_layout(
                db,
                layout,
                nt_path,
                data_sha256=summary.sha256,
                triple_count=summary.triples_out,  # type: ignore[arg-type]
            )
            handles[layout] = db
        return handles[layout]

    return get

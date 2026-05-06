"""Shared pytest fixtures for ``arango-sparql-py``.

Heavy fixtures (ArangoDB connections, pyoxigraph stores, W3C manifests)
should live next to the tests that use them; this module is reserved
for things every test in the repo needs.
"""

from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def _quiet_rdflib_warnings(caplog: pytest.LogCaptureFixture) -> None:
    """rdflib likes to ``logger.warning`` on benign parsing edge cases.
    Suppress the noise so test output stays readable; tests that care
    about the warnings can still assert on ``caplog``.
    """
    logging.getLogger("rdflib").setLevel(logging.ERROR)

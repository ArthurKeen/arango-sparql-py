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


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets() -> None:
    """Reset the process-wide rate-limit buckets before every test.

    The compute / NL token buckets are module-level singletons keyed by
    client identity. Under a full suite run every route test shares one
    client key, so without this reset the cumulative request count would
    eventually exhaust the bucket and 429 whichever tests happened to run
    last — a flaky, ordering-dependent failure unrelated to the code under
    test. Resetting per case keeps rate-limit *behaviour* assertions intact
    (a test that fires N+1 requests still trips its own limit) while
    isolating tests from each other.
    """
    from arango_sparql.service import security as _security

    _security._compute_bucket.reset()
    _security._nl_bucket.reset()

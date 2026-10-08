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


@pytest.fixture(autouse=True)
def _inline_schema_warm(monkeypatch: pytest.MonkeyPatch):
    """Run background schema warms inline, and reset warm state per test.

    In production a schema cache miss starts the analysis on a daemon thread
    and the endpoint answers ``pending`` (``service/schema_warm.py``). Under
    test that thread would race the assertions and could outlive the test,
    touching a fake database after it is torn down. Running the warm inline
    keeps every route test deterministic: a miss analyzes, fills the cache,
    and the same request answers ``ready`` — the content the tests assert.
    The asynchronous ``pending`` path itself is covered explicitly in
    ``tests/service/test_schema_warm.py``, which replaces this seam.
    """
    from arango_sparql.service import schema_warm

    schema_warm._reset_for_tests()
    monkeypatch.setattr(schema_warm, "_start_thread", lambda target, name: target())
    yield
    schema_warm._reset_for_tests()

"""Fixtures for the service tests in this directory.

The schema-warm tests (``test_schema_warm.py``) drive the real schema routes
with the same fakes as ``tests/test_service_schema_routes.py`` — a fake
python-arango stack, a controllable ``acquire_mapping_bundle`` stub, and the
session / cache isolation — so they share those fixtures rather than copying
them. Imported here (not in the test module) so the fixture names are not
redefinitions of imported symbols.
"""

from tests.test_service_schema_routes import (  # noqa: F401 — re-exported pytest fixtures
    _isolate_state,
    client,
    fake_arango,
    session_token,
    stub_acquire,
)

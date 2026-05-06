"""Integration tests for ``arango-sparql-py``.

Tests in this package are gated behind the ``integration`` pytest
marker (declared in ``pyproject.toml``) and require a running
ArangoDB instance — see ``docker-compose.yml`` at the repo root.

Run with::

    RUN_INTEGRATION=1 .venv/bin/pytest -q -m integration

These tests are excluded from the default ``pytest`` run so the
unit-test loop stays fast and deterministic.
"""

from __future__ import annotations

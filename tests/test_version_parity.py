"""``arango_sparql.__version__`` must match ``pyproject.toml [project].version``.

They drifted once (``__init__`` stuck at 0.1.0 while pyproject moved to 0.2.0),
which the BYOC deploy would have shipped as a wrong ``openapi.json`` version and
a failed post-deploy verify. This is the cheap guard that keeps the single
source of truth honest — the deploy script (`scripts/byoc_deploy.py`) and the
FastAPI app both read ``__version__``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import arango_sparql

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_package_version_matches_pyproject() -> None:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    assert arango_sparql.__version__ == data["project"]["version"], (
        f"arango_sparql.__version__={arango_sparql.__version__!r} but "
        f"pyproject [project].version={data['project']['version']!r} — keep them in lockstep"
    )

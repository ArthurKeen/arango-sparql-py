"""The arangodb-schema-analyzer band stays co-installable with arango-cypher-py's.

PRD §12.1: the two transpilers are installed side by side (sibling venvs, the
CDF, r2g), so their analyzer bands must (1) share a ceiling and (2) overlap —
mutually exclusive bands are unsatisfiable, and a lower ceiling in one silently
DOWNGRADES a co-installed analyzer the other depends on (observed: CDF
``make seed``, 2026-09-06). Floors may differ briefly while one repo adopts a
fix release first (arango-cypher-py raised to 0.14.1 on 2026-10-05). Issue #8
noted the invariant was documented but not enforced; this test enforces it.

The sister band is read from the local ``references/arango-cypher-py`` checkout
when present, else from GitHub (both repos are public). An unreachable GitHub
skips with a stated reason rather than failing CI on a network blip.
"""

from __future__ import annotations

import tomllib
import urllib.request
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

_REPO = Path(__file__).resolve().parent.parent
_SISTER_LOCAL = _REPO / "references" / "arango-cypher-py" / "pyproject.toml"
_SISTER_URL = "https://raw.githubusercontent.com/arango-solutions-org/arango-cypher/main/pyproject.toml"
_PKG = "arangodb-schema-analyzer"


def _analyzer_specifier(pyproject: dict) -> SpecifierSet:
    project = pyproject.get("project", {})
    requirements = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        requirements.extend(extra)
    found = [Requirement(r) for r in requirements if Requirement(r).name == _PKG]
    assert found, f"{_PKG} is not declared in this pyproject"
    specs = {str(r.specifier) for r in found}
    assert len(specs) == 1, f"{_PKG} is declared with different bands in one pyproject: {specs}"
    return found[0].specifier


def _bounds(spec: SpecifierSet) -> tuple[Version, Version]:
    floors = [Version(s.version) for s in spec if s.operator == ">="]
    ceilings = [Version(s.version) for s in spec if s.operator == "<"]
    assert len(floors) == 1 and len(ceilings) == 1, f"band must be '>=floor,<ceiling', got {spec}"
    return floors[0], ceilings[0]


def _sister_pyproject() -> dict:
    if _SISTER_LOCAL.is_file():
        return tomllib.loads(_SISTER_LOCAL.read_text(encoding="utf-8"))
    try:
        with urllib.request.urlopen(_SISTER_URL, timeout=10) as resp:  # noqa: S310 — fixed https URL
            return tomllib.loads(resp.read().decode("utf-8"))
    except OSError as exc:
        pytest.skip(f"arango-cypher-py pyproject unavailable locally and from GitHub ({exc})")


def test_band_is_well_formed() -> None:
    ours = _analyzer_specifier(tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8")))
    floor, ceiling = _bounds(ours)
    assert floor < ceiling


def test_band_shares_a_ceiling_and_overlaps_with_arango_cypher_py() -> None:
    ours = _analyzer_specifier(tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8")))
    theirs = _analyzer_specifier(_sister_pyproject())
    our_floor, our_ceiling = _bounds(ours)
    their_floor, their_ceiling = _bounds(theirs)
    assert our_ceiling == their_ceiling, (
        f"analyzer ceilings differ: arango-sparql-py {ours} vs arango-cypher-py {theirs} — "
        "a lower ceiling downgrades a co-installed analyzer (PRD §12.1); raise them together"
    )
    assert max(our_floor, their_floor) < our_ceiling, (
        f"analyzer bands do not overlap: arango-sparql-py {ours} vs arango-cypher-py {theirs}"
    )

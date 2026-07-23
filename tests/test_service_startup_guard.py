"""Tests for the PRD §6.3.4 startup guard.

The guard runs at app-import time. To exercise it under different
env-var combinations we cannot just rely on the already-loaded
``arango_sparql.service.app`` module — we have to call the guard
function directly with the env state we want to test.

Coverage matrix (all four reachable cells from PRD §6.3.4):

| ANALYZER_REQUIRED | analyzer importable | Outcome           |
| ----------------- | ------------------- | ----------------- |
| true (default)    | yes                 | boot OK (cell 1)  |
| true              | no                  | boot REFUSED (2)  |
| false             | (irrelevant)        | boot OK (cell 3)  |
| false + ALLOW_HEURISTIC=false | both ignored at startup; runtime cell 4 lives in the route tests |

Plus install-hint sentinel tests so the version range cannot drift
silently between ``arango_sparql.service.app`` and
``arango_sparql.schema.acquire``.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any

import pytest

import arango_sparql.schema.acquire as acquire_mod

# Importing the parent package runs the ``app`` submodule which
# populates ``sys.modules["arango_sparql.service.app"]`` with the
# real module. We then peel off that slot — bypassing the parent
# package's ``app`` attribute (which actually points at the
# FastAPI app instance, not the module).
import arango_sparql.service  # noqa: F401  (side-effect import)

app_mod = sys.modules["arango_sparql.service.app"]


# ---------------------------------------------------------------------------
# Cell 1 — analyzer required AND importable → boot OK
# ---------------------------------------------------------------------------


def test_cell_1_required_and_importable_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCHEMA_ANALYZER_REQUIRED", raising=False)
    # Default is "true" (required); the analyzer extra is installed
    # in the test venv so the guard should pass without raising.
    app_mod._require_analyzer_unless_opted_out()


@pytest.mark.parametrize("raw", ["true", "True", "1", "yes", "TRUE"])
def test_cell_1_explicit_true_values_succeed(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("SCHEMA_ANALYZER_REQUIRED", raw)
    app_mod._require_analyzer_unless_opted_out()


# ---------------------------------------------------------------------------
# Cell 2 — analyzer required AND missing → boot REFUSED
# ---------------------------------------------------------------------------


def _block_schema_analyzer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``import schema_analyzer`` raise ``ImportError`` for
    the duration of one test. Removes any cached module reference
    and inserts a finder at the head of ``sys.meta_path`` that
    refuses every ``schema_analyzer*`` resolve.
    """

    for mod_name in list(sys.modules):
        if mod_name == "schema_analyzer" or mod_name.startswith("schema_analyzer."):
            monkeypatch.delitem(sys.modules, mod_name, raising=False)

    class _Blocker:
        def find_module(self, fullname: str, path: Any = None) -> Any:
            if fullname == "schema_analyzer" or fullname.startswith("schema_analyzer."):
                return self
            return None

        def load_module(self, fullname: str) -> ModuleType:
            raise ImportError(f"blocked: {fullname}")

        def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
            if fullname == "schema_analyzer" or fullname.startswith("schema_analyzer."):
                from importlib.machinery import ModuleSpec

                return ModuleSpec(fullname, self)
            return None

        def create_module(self, spec: Any) -> ModuleType | None:
            raise ImportError(f"blocked: {spec.name}")

        def exec_module(self, module: ModuleType) -> None:
            raise ImportError("blocked")

    monkeypatch.setattr(sys, "meta_path", [_Blocker(), *sys.meta_path])


def test_cell_2_required_but_missing_refuses_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCHEMA_ANALYZER_REQUIRED", raising=False)
    _block_schema_analyzer(monkeypatch)
    with pytest.raises(app_mod.AnalyzerStartupGuardError) as exc_info:
        app_mod._require_analyzer_unless_opted_out()
    assert "arangodb-schema-analyzer" in str(exc_info.value)
    assert exc_info.value.install_hint == app_mod.ANALYZER_INSTALL_HINT


def test_cell_2_explicit_required_and_missing_refuses_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCHEMA_ANALYZER_REQUIRED", "true")
    _block_schema_analyzer(monkeypatch)
    with pytest.raises(app_mod.AnalyzerStartupGuardError):
        app_mod._require_analyzer_unless_opted_out()


def test_cell_2_install_hint_carries_version_range_and_opt_out_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The startup-failure message must surface both the install
    command (so an operator can fix it) and the opt-out env var
    (so they can keep the service running while they decide).
    """

    _block_schema_analyzer(monkeypatch)
    try:
        app_mod._require_analyzer_unless_opted_out()
    except app_mod.AnalyzerStartupGuardError as exc:
        msg = str(exc)
        assert "pip install" in msg
        assert "arangodb-schema-analyzer" in msg
        assert "SCHEMA_ANALYZER_REQUIRED=false" in msg
    else:
        pytest.fail("guard should have raised")


# ---------------------------------------------------------------------------
# Cell 3 — opt-out set; analyzer absence is irrelevant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["false", "False", "0", "no", "FALSE"])
def test_cell_3_opt_out_succeeds_when_analyzer_missing(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("SCHEMA_ANALYZER_REQUIRED", raw)
    _block_schema_analyzer(monkeypatch)
    # Should not raise — the opt-out is a conscious operator
    # decision, the missing analyzer is no longer a startup failure.
    app_mod._require_analyzer_unless_opted_out()


def test_cell_3_opt_out_succeeds_when_analyzer_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opt-out is honoured even when the analyzer happens to be
    importable — the operator's choice wins over the auto-detect.
    """

    monkeypatch.setenv("SCHEMA_ANALYZER_REQUIRED", "false")
    app_mod._require_analyzer_unless_opted_out()


# ---------------------------------------------------------------------------
# Garbage env values default to "required"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["", "   ", "maybe", "definitely-not", "1.5"])
def test_garbage_env_value_defaults_to_required(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """Unknown / malformed values fall back to the safe default
    (required=True). A typo in a deployment YAML must not silently
    disable the analyzer requirement.
    """

    monkeypatch.setenv("SCHEMA_ANALYZER_REQUIRED", raw)
    _block_schema_analyzer(monkeypatch)
    with pytest.raises(app_mod.AnalyzerStartupGuardError):
        app_mod._require_analyzer_unless_opted_out()


# ---------------------------------------------------------------------------
# Whitespace tolerance
# ---------------------------------------------------------------------------


def test_env_value_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHEMA_ANALYZER_REQUIRED", "   false   ")
    _block_schema_analyzer(monkeypatch)
    # Whitespace-padded "false" should still parse as opt-out.
    app_mod._require_analyzer_unless_opted_out()


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------


def test_guard_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Running the guard twice in a row should produce the same
    outcome — required for tests that need to re-run it under
    different env-var states without a process restart.
    """

    monkeypatch.setenv("SCHEMA_ANALYZER_REQUIRED", "true")
    app_mod._require_analyzer_unless_opted_out()
    app_mod._require_analyzer_unless_opted_out()


# ---------------------------------------------------------------------------
# Install-hint cross-module consistency
# ---------------------------------------------------------------------------


def test_install_hint_matches_acquire_module() -> None:
    """The version pin must be identical in
    :mod:`arango_sparql.service.app` and
    :mod:`arango_sparql.schema.acquire`. A drift here means an
    operator could see two different "install this" messages
    depending on which surface raised first — confusing and
    actionable only by reading the source.
    """

    assert app_mod.ANALYZER_VERSION_RANGE == acquire_mod.ANALYZER_VERSION_RANGE


def test_app_install_hint_contains_acquire_command_form() -> None:
    """Both install hints should embed the same ``pip install
    'arangodb-schema-analyzer<version>'`` command so a copy/paste
    from either error message gets the operator unstuck.
    """

    assert "arangodb-schema-analyzer" in app_mod.ANALYZER_INSTALL_HINT
    assert app_mod.ANALYZER_VERSION_RANGE in app_mod.ANALYZER_INSTALL_HINT
    assert "pip install" in app_mod.ANALYZER_INSTALL_HINT


# ---------------------------------------------------------------------------
# Skip-guard escape hatch is honoured
# ---------------------------------------------------------------------------


def test_skip_guard_env_var_constant_form() -> None:
    """The escape hatch the test infrastructure uses must keep its
    documented name. A rename here would silently re-enable the
    boot-time guard for the whole test suite — and the next test
    that imports the package on a CI worker without the analyzer
    extra would crash collection.
    """

    # The variable name itself is part of the contract; we assert
    # against the literal so a refactor that renames it without
    # updating callers fails loudly.
    assert (
        "ARANGO_SPARQL_SKIP_STARTUP_GUARD"
        in (
            app_mod._require_analyzer_unless_opted_out.__code__.co_consts
            + app_mod._require_analyzer_unless_opted_out.__code__.co_names
            + tuple(app_mod.__dict__.keys())
        )
        or "ARANGO_SPARQL_SKIP_STARTUP_GUARD" in open(app_mod.__file__ or "", encoding="utf-8").read()
    )

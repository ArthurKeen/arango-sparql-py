"""Unit tests for :mod:`arango_sparql._env`.

Covers the canonical/legacy ordering for ``ARANGO_PASSWORD`` (the only
helper with a deprecated alias) plus the simpler URL/username/database
helpers that read ``ARANGO_URL`` / ``ARANGO_USER`` / ``ARANGO_DB``
directly with caller-supplied fallbacks.

The deprecation warning is fired at most once per ``(caller, env-name)``
pair, so tests that exercise the fallback path more than once call
:func:`_reset_warning_state_for_tests` in setup to re-arm the warning.
"""

from __future__ import annotations

import logging

import pytest

from arango_sparql import _env


@pytest.fixture(autouse=True)
def _reset_env_warnings() -> None:
    """Clear the once-per-process warning set before every test."""
    _env._reset_warning_state_for_tests()
    yield
    _env._reset_warning_state_for_tests()


# ---------------------------------------------------------------------------
# read_arango_password — canonical/legacy ordering
# ---------------------------------------------------------------------------


def test_read_password_canonical_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARANGO_PASSWORD", "canonical-marker")
    monkeypatch.delenv("ARANGO_PASS", raising=False)
    assert _env.read_arango_password(caller="test") == "canonical-marker"


def test_read_password_legacy_only_logs_and_warns(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("ARANGO_PASSWORD", raising=False)
    monkeypatch.setenv("ARANGO_PASS", "legacy-marker")
    with caplog.at_level(logging.WARNING, logger="arango_sparql"):
        with pytest.warns(DeprecationWarning, match="ARANGO_PASS is deprecated"):
            value = _env.read_arango_password(caller="test_legacy_only")
    assert value == "legacy-marker"
    assert any("ARANGO_PASS is deprecated" in rec.message for rec in caplog.records)
    assert any("test_legacy_only" in rec.message for rec in caplog.records)


def test_read_password_canonical_wins_over_legacy(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("ARANGO_PASSWORD", "canonical-wins")
    monkeypatch.setenv("ARANGO_PASS", "should-not-be-read")
    with caplog.at_level(logging.WARNING, logger="arango_sparql"):
        # No warning expected — the canonical name short-circuits the fallback.
        # ``filterwarnings("error")`` makes any DeprecationWarning fatal so
        # the assertion is positive (we never reached the legacy branch),
        # not just absent.
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert _env.read_arango_password(caller="test_both") == "canonical-wins"
    assert not any("deprecated" in rec.message.lower() for rec in caplog.records)


def test_read_password_neither_set_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARANGO_PASSWORD", raising=False)
    monkeypatch.delenv("ARANGO_PASS", raising=False)
    assert _env.read_arango_password(caller="test_neither") is None


def test_read_password_dedupes_legacy_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Same caller hitting the legacy fallback twice must log once."""
    monkeypatch.delenv("ARANGO_PASSWORD", raising=False)
    monkeypatch.setenv("ARANGO_PASS", "p")
    with caplog.at_level(logging.WARNING, logger="arango_sparql"):
        _env.read_arango_password(caller="dedupe_caller")
        _env.read_arango_password(caller="dedupe_caller")
    deprecation_lines = [r for r in caplog.records if "ARANGO_PASS is deprecated" in r.message]
    assert len(deprecation_lines) == 1


# ---------------------------------------------------------------------------
# read_arango_url / username / database — direct lookups with default
# ---------------------------------------------------------------------------


def test_read_url_returns_env_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARANGO_URL", "http://db.example.invalid:8529")
    assert _env.read_arango_url(caller="t") == "http://db.example.invalid:8529"


def test_read_url_returns_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARANGO_URL", raising=False)
    assert _env.read_arango_url(default="http://localhost:8529", caller="t") == "http://localhost:8529"


def test_read_url_returns_none_when_no_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARANGO_URL", raising=False)
    assert _env.read_arango_url(caller="t") is None


def test_read_username_returns_env_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARANGO_USER", "ronnie")
    assert _env.read_arango_username(default="root", caller="t") == "ronnie"


def test_read_username_returns_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARANGO_USER", raising=False)
    assert _env.read_arango_username(default="root", caller="t") == "root"


def test_read_database_returns_env_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARANGO_DB", "fixtures")
    assert _env.read_arango_database(default="_system", caller="t") == "fixtures"


def test_read_database_returns_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARANGO_DB", raising=False)
    assert _env.read_arango_database(default="_system", caller="t") == "_system"

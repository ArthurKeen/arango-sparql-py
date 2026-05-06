"""Environment-variable helpers shared between the FastAPI service and the
``arango-sparql`` CLI.

Mirrors :mod:`arango_cypher._env` so an operator who has already deployed
the Cypher sister project can copy their ``.env`` over verbatim and have
both services agree on credential resolution.

The canonical contract:

* ``ARANGO_PASSWORD`` is the **canonical** password env-var (matches
  industry convention — Postgres, Redis, MongoDB, MySQL, Cassandra, etc.
  all use ``*_PASSWORD``).
* ``ARANGO_PASS`` is read as a deprecated fallback when only the legacy
  name is set; a one-time ``DeprecationWarning`` + ``logging.WARNING`` is
  emitted so operators get a clean upgrade signal.
* ``ARANGO_URL`` / ``ARANGO_USER`` / ``ARANGO_DB`` are read directly —
  the Cypher sibling exposes them under the same names today and adding
  legacy aliases here would invent semantics that don't exist on the
  sister project. The helpers still go through this module so the route
  layer reads from a single source of truth and so a future audit can
  add a fallback by editing one helper rather than every call site.
* Any unset variable falls back to the caller-supplied ``default``
  (or ``None`` when no default was provided, matching :func:`os.getenv`'s
  shape).

The deprecation warning fires at most once per ``(caller, env-name)``
pair so a long-running service does not spam its log every request. The
``caller`` keyword scopes the dedupe so the service handler and a CLI
command can each log their own upgrade reminder if both happen to hit
the legacy name in the same process. Tests can re-arm the warning via
:func:`_reset_warning_state_for_tests`.

Removal timeline: the legacy ``ARANGO_PASS`` name will be removed at the
next major (1.0). Until then this helper is the single read site for
either name; do not call ``os.getenv("ARANGO_PASS")`` directly anywhere
new — let the helper handle the fallback so the deprecation log fires
exactly where it should.
"""

from __future__ import annotations

import logging
import os
import warnings

_logger = logging.getLogger("arango_sparql")

# Suppress repeated warnings for the same (caller, fallback-name) pair so
# a long-running process logs the deprecation once and then stays quiet.
_warned: set[tuple[str, str]] = set()


def read_arango_password(*, caller: str = "arango_sparql") -> str | None:
    """Resolve the ArangoDB password from environment variables.

    Order: ``ARANGO_PASSWORD`` (canonical) → ``ARANGO_PASS`` (legacy).
    Logs and emits a one-time ``DeprecationWarning`` when only the legacy
    name is set. Returns ``None`` when neither variable is set so the
    caller can distinguish "operator did not configure a password" from
    "operator deliberately set an empty string".

    Parameters
    ----------
    caller:
        Short, human-readable identifier ("connect_defaults", "cli",
        "test_setup", …) included in the deprecation log line so
        operators can find the call site. Also scopes the once-per-
        process dedupe so two distinct call sites still get one
        warning each.
    """
    canonical = os.environ.get("ARANGO_PASSWORD")
    if canonical is not None:
        return canonical

    legacy = os.environ.get("ARANGO_PASS")
    if legacy is not None:
        _emit_deprecation_warning(caller=caller, legacy_name="ARANGO_PASS", canonical_name="ARANGO_PASSWORD")
        return legacy

    return None


def read_arango_url(default: str | None = None, *, caller: str = "arango_sparql") -> str | None:
    """Resolve the ArangoDB coordinator URL.

    Reads ``ARANGO_URL`` directly. The Cypher sister project does not
    define a legacy alias for the URL, so neither do we. Goes through
    this helper rather than ``os.getenv`` so a future audit that needs
    to add an alias (or to log every URL read for tracing) can do so by
    editing one site instead of every caller. *caller* is reserved for
    that future use; it is currently only included in the trace-level
    log line for parity with the password helper.
    """
    value = os.environ.get("ARANGO_URL")
    if value is not None:
        return value
    _logger.debug("read_arango_url(%s) -> default=%r", caller, default)
    return default


def read_arango_username(default: str | None = None, *, caller: str = "arango_sparql") -> str | None:
    """Resolve the ArangoDB username from ``ARANGO_USER``.

    See :func:`read_arango_url` for why no legacy alias is honoured.
    """
    value = os.environ.get("ARANGO_USER")
    if value is not None:
        return value
    _logger.debug("read_arango_username(%s) -> default=%r", caller, default)
    return default


def read_arango_database(default: str | None = None, *, caller: str = "arango_sparql") -> str | None:
    """Resolve the ArangoDB database name from ``ARANGO_DB``.

    See :func:`read_arango_url` for why no legacy alias is honoured.
    """
    value = os.environ.get("ARANGO_DB")
    if value is not None:
        return value
    _logger.debug("read_arango_database(%s) -> default=%r", caller, default)
    return default


def _emit_deprecation_warning(*, caller: str, legacy_name: str, canonical_name: str) -> None:
    """Fire the once-per-(caller, env-name) deprecation log + warning."""
    key = (caller, legacy_name)
    if key in _warned:
        return
    _warned.add(key)
    msg = (
        f"{legacy_name} is deprecated; use {canonical_name} instead. "
        f"{legacy_name} will be removed at the next major release. "
        f"(read by {caller})"
    )
    _logger.warning(msg)
    warnings.warn(msg, DeprecationWarning, stacklevel=3)


def _reset_warning_state_for_tests() -> None:
    """Test-only hook to clear the once-per-process warning set.

    The deprecation log fires at most once per (caller, env-name) pair to
    avoid spamming a long-running service. Tests that exercise the
    fallback path more than once need a way to re-arm the warning between
    cases without resorting to monkey-patching the module-level set
    directly.
    """
    _warned.clear()

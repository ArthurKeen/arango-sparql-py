"""Integration runs never write to a remote ArangoDB by accident.

``arango_sparql.service.app`` loads a developer's ``.env`` on import (its
``load_dotenv()`` searches upward), and a nested pytest run inherited its
``ARANGO_URL`` and loaded W3C collections into a shared cluster
(2026-10-28). These pin the guard and the nested run's environment.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from tests.integration import conftest as integration
from tests.w3c import analyze_coverage


@pytest.mark.parametrize("url", ["http://localhost:8532", "http://127.0.0.1:28529", "http://[::1]:8529"])
def test_a_loopback_target_is_allowed(url: str) -> None:
    integration.refuse_remote_target(url)


def test_a_remote_target_is_refused_with_how_to_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(integration.ALLOW_REMOTE_ENV, raising=False)
    with pytest.raises(integration.RemoteTestTargetError) as caught:
        integration.refuse_remote_target("https://prod.demo.pilot.arango.ai")
    message = str(caught.value)
    assert "'prod.demo.pilot.arango.ai'" in message
    assert integration.ALLOW_REMOTE_ENV in message


def test_a_remote_target_needs_an_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(integration.ALLOW_REMOTE_ENV, "1")
    integration.refuse_remote_target("https://cluster.example:8529")


def test_integration_mode_checks_the_resolved_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUN_INTEGRATION", "1")
    monkeypatch.delenv(integration.ALLOW_REMOTE_ENV, raising=False)
    monkeypatch.setattr(integration, "DEFAULT_ARANGO_URL", "https://cluster.example:8529")
    with pytest.raises(integration.RemoteTestTargetError):
        integration.integration_enabled()


def test_integration_mode_off_checks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUN_INTEGRATION", raising=False)
    monkeypatch.setattr(integration, "DEFAULT_ARANGO_URL", "https://cluster.example:8529")
    assert integration.integration_enabled() is False


def test_the_nested_live_run_gets_this_runs_target_not_a_loaded_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # The process environment says prod.demo (as after the app loaded a
    # .env); the nested run must still get the target this run resolved.
    monkeypatch.setenv("RUN_INTEGRATION", "1")
    monkeypatch.setenv("ARANGO_URL", "https://prod.demo.pilot.arango.ai")
    monkeypatch.setenv("ARANGO_DB", "IAM")
    seen: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs["env"])
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="stopped by the test")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="stopped by the test"):
        analyze_coverage.analyze_live()
    assert seen["ARANGO_URL"] == integration.DEFAULT_ARANGO_URL
    assert seen["ARANGO_TEST_DB"] == integration.DEFAULT_ARANGO_DB
    assert seen["RUN_INTEGRATION"] == "1"


def test_the_nested_run_env_pins_the_target(monkeypatch: pytest.MonkeyPatch) -> None:
    # Needs no W3C corpus, so it runs in every CI job (the end-to-end test
    # above skips where the corpus is absent).
    monkeypatch.setenv("ARANGO_URL", "https://prod.demo.pilot.arango.ai")
    monkeypatch.setenv("ARANGO_TEST_DB", "IAM")
    monkeypatch.setenv("ARANGO_PASSWORD", "from-a-dotenv")
    env = analyze_coverage._nested_run_env()
    assert env["ARANGO_URL"] == integration.DEFAULT_ARANGO_URL
    assert env["ARANGO_TEST_DB"] == integration.DEFAULT_ARANGO_DB
    assert env["ARANGO_PASSWORD"] == integration.DEFAULT_ARANGO_PASSWORD
    assert env["ARANGO_DB"] == integration.DEFAULT_ARANGO_DB

"""Unit tests for W3C coverage accounting."""

from __future__ import annotations

import subprocess

import pytest

from tests.w3c import analyze_coverage


def _completed(*, returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_analyze_live_counts_only_parameterized_w3c_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_command: list[str] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        seen_command.extend(command)
        return _completed(stdout="86 passed, 105 xfailed in 10.24s")

    monkeypatch.setenv("RUN_INTEGRATION", "1")
    monkeypatch.setattr(subprocess, "run", fake_run)

    stats = analyze_coverage.analyze_live()

    assert seen_command[-3] == ("tests/w3c/test_w3c_live_execution.py::test_live_execution")
    assert stats.total == 191
    assert stats.passed == 86
    assert stats.xfailed == 105
    assert stats.coverage == pytest.approx(86 / 191 * 100)


def test_analyze_live_rejects_counter_denominator_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUN_INTEGRATION", "1")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(stdout="90 passed, 105 xfailed in 10.24s"),
    )

    with pytest.raises(RuntimeError, match="195 outcomes for 191 W3C cases"):
        analyze_coverage.analyze_live()


def test_analyze_live_surfaces_hard_pytest_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUN_INTEGRATION", "1")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(returncode=1, stdout="1 failed, 190 passed"),
    )

    with pytest.raises(RuntimeError, match="live W3C pytest run failed"):
        analyze_coverage.analyze_live()

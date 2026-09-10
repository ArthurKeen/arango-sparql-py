"""Unit tests for W3C coverage accounting."""

from __future__ import annotations

import subprocess

import pytest

from tests.w3c import analyze_coverage


def _completed(*, returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


# --- Federation (SERVICE) reclassification ---------------------------------


def test_uses_service_detects_federation_keyword() -> None:
    assert analyze_coverage._uses_service("SELECT * { SERVICE <http://x/> { ?s ?p ?o } }")
    assert analyze_coverage._uses_service("SELECT * {\n  SERVICE SILENT <http://x/> { ?s ?p ?o }\n}")


def test_uses_service_ignores_the_keyword_in_comments() -> None:
    # A '# ... SERVICE ...' note must not trip detection — only real use counts.
    assert not analyze_coverage._uses_service("# this query has no SERVICE\nSELECT * { ?s ?p ?o }")
    assert not analyze_coverage._uses_service("SELECT * { ?s ?p ?o }")


def test_federation_cases_leave_the_query_eval_denominator() -> None:
    # Integration against the real corpus: every SERVICE query-eval case is
    # lifted into the out-of-scope FEDERATION category (not counted as an
    # algebra XFAIL, not in the QueryEvaluationTest denominator).
    from tests.w3c.runner import QUERY_EVAL, w3c_corpus_root

    if w3c_corpus_root() is None:
        pytest.skip("W3C corpus not on disk; run scripts/fetch_w3c.sh first")

    by_category = analyze_coverage.analyze()
    fed = by_category.get(analyze_coverage.FEDERATION)
    assert fed is not None and fed.total >= 1, "expected federation cases to be reclassified"
    # Federation cases are skipped (out of scope), never counted as passes/xfails.
    assert fed.skipped == fed.total and fed.passed == 0 and fed.xfailed == 0
    # None of the remaining query-eval XFAILs mention ServiceGraphPattern.
    qe = by_category[QUERY_EVAL]
    assert not any("ServiceGraphPattern" in reason for reason in qe.xfail_reasons)


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

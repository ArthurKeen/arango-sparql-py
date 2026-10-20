"""Every CI step that runs the W3C suite fetches the corpus first.

The DAWG corpus is not vendored (``scripts/fetch_w3c.sh`` downloads it), and
without it the W3C tests skip rather than fail. A workflow that forgets the
fetch therefore runs nothing: the nightly did exactly that, its live leg
exiting 5 ("no tests ran") every day from at least 2026-10-13. This holds
the rule in every workflow, so the next job that runs W3C tests cannot omit it.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
FETCH = "scripts/fetch_w3c.sh"
# ``-m w3c`` selects the W3C marker (``-m "not … w3c …"`` excludes it and
# needs no corpus); a ``tests/w3c/`` path selects W3C modules directly.
RUNS_W3C = re.compile(r"pytest\b[^\n]*(?:-m\s+w3c\b|tests/w3c/)")


def _w3c_steps_without_a_prior_fetch() -> list[str]:
    problems = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job_name, job in (yaml.safe_load(path.read_text(encoding="utf-8")).get("jobs") or {}).items():
            fetched = False
            for step in job.get("steps") or []:
                run = step.get("run") or ""
                if FETCH in run:
                    fetched = True
                elif RUNS_W3C.search(run) and not fetched:
                    problems.append(f"{path.name}:{job_name}: {run.strip().splitlines()[0]}")
    return problems


def test_every_w3c_step_fetches_the_corpus_first() -> None:
    problems = _w3c_steps_without_a_prior_fetch()
    assert not problems, f"W3C tests run before `bash {FETCH}`:\n  " + "\n  ".join(problems)


def test_the_check_recognises_w3c_steps() -> None:
    """The check above passes vacuously if its pattern stops matching."""
    assert RUNS_W3C.search("pytest -m w3c -q --tb=short")
    assert RUNS_W3C.search("pytest tests/w3c/test_w3c_live_execution.py -q")
    assert not RUNS_W3C.search('pytest -m "not integration and not w3c and not eval" --tb=short -q')

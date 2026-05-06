"""NL → SPARQL evaluation harness.

Mirrors ``tests/nl2cypher/eval/runner.py`` from ``arango-cypher-py``:
consumes ``corpus.yml`` + ``configs.yml``, executes each corpus entry
against each configured provider, and writes JSON + Markdown reports
under ``reports/`` (gitignored).

The runner accepts any LLM provider, so unit tests pass a scripted
mock and CI sweeps pass a real ``OpenAIProvider``. Only ``baseline.json``
is checked in — that is the regression gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).parent
CORPUS_PATH = EVAL_DIR / "corpus.yml"
CONFIGS_PATH = EVAL_DIR / "configs.yml"
REPORTS_DIR = EVAL_DIR / "reports"


@dataclass
class CaseResult:
    name: str
    expected: str
    actual: str
    passed: bool
    elapsed_ms: float = 0.0


@dataclass
class Report:
    config: str
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return sum(1 for c in self.cases if c.passed) / max(len(self.cases), 1)


def run(config_name: str) -> Report:
    """TODO: port the Cypher eval runner. Stubbed today so the import
    surface compiles and the eval marker can be wired into CI."""
    raise NotImplementedError("nl2sparql eval runner is not implemented yet")


def write_report(report: Report, *, out_dir: Path = REPORTS_DIR) -> tuple[Path, Path]:
    """Write JSON + Markdown report files. Stubbed for the same reason."""
    raise NotImplementedError("nl2sparql eval reporter is not implemented yet")

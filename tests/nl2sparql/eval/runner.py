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

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from arango_sparql.errors import SparqlParseError
from arango_sparql.nl2sparql import (
    AnthropicClient,
    LLMClient,
    LLMResponse,
    NlPipeline,
    OpenAICompatibleClient,
    ScriptedLLMClient,
)
from arango_sparql.translate.parser import parse_sparql
from arango_sparql.translate.resolver import SchemaResolver

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


# ---------------------------------------------------------------------------
# Loaders — trusted checked-in YAML, always via yaml's safe_load only.
# ---------------------------------------------------------------------------


def _load_corpus() -> dict[str, Any]:
    return yaml.safe_load(CORPUS_PATH.read_text())


def _load_configs() -> dict[str, Any]:
    return yaml.safe_load(CONFIGS_PATH.read_text())


# ---------------------------------------------------------------------------
# Scripted-response helper — mirrors `_wrap` in tests/nl2sparql/test_pipeline.py
# ---------------------------------------------------------------------------


def _wrap_sparql(sparql: str) -> LLMResponse:
    """Wrap a SPARQL string in a fenced ```sparql block, as a real model would.

    ``extract_sparql_from_response`` (arango_sparql/nl2sparql/prompt.py) looks
    for this fence first, so the scripted double must mimic it exactly.
    """
    return LLMResponse(
        content=f"```sparql\n{sparql.strip()}\n```",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
    )


# ---------------------------------------------------------------------------
# Provider factory — config["provider"]["type"] -> LLMClient
# ---------------------------------------------------------------------------


def _client_for(config: dict[str, Any], case: dict[str, Any]) -> LLMClient:
    provider = config["provider"]
    ptype = provider["type"]
    if ptype == "scripted":
        # Fresh client per case — ScriptedLLMClient replays its LAST queued
        # response forever once drained, so sharing one client across cases
        # would leak case N-1's SPARQL into case N.
        canned = case.get("scripted", case["expected"])
        return ScriptedLLMClient([_wrap_sparql(canned)], latency_ms=0)
    if ptype in ("openai", "openrouter"):
        return OpenAICompatibleClient(provider=ptype, model=provider.get("model"))
    if ptype == "anthropic":
        return AnthropicClient(model=provider.get("model"))
    raise ValueError(f"unknown provider type {ptype!r}")


# ---------------------------------------------------------------------------
# Judge — rdflib canonical algebra comparison (rule 200: no string matching)
# ---------------------------------------------------------------------------


def _canonical(sparql: str) -> str | None:
    try:
        return repr(parse_sparql(sparql).algebra)
    except SparqlParseError:
        return None


def _judge_canonical(expected: str, outcome: Any) -> bool:
    if not outcome.aql:
        # Transpiler rejected the generated SPARQL (or repair exhausted) —
        # outcome.aql == "" is the authoritative accept signal.
        return False
    canonical_expected = _canonical(expected)
    canonical_actual = _canonical(outcome.sparql)
    return canonical_expected is not None and canonical_expected == canonical_actual


def _canon_row(row: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """Canonicalize a single binding row so key-insertion order (and
    therefore SELECT-projection column order) doesn't affect equality —
    only the (variable, value) pairs it actually contains do."""
    return tuple(sorted(row.items()))


def _judge_execution(expected: str, outcome: Any, data_ttl: str) -> bool:
    """Optional execution-equivalence tier — only used when a case carries
    a `data:` Turtle fixture. Lazy-imports `tests.helpers.oxi` so pyoxigraph
    absence never breaks the default (canonical) judging path."""
    if not outcome.aql:
        return False
    from tests.helpers.oxi import load_store_from_string, oxi_bindings

    store = load_store_from_string(data_ttl)
    expected_bindings = oxi_bindings(store, expected)
    actual_bindings = oxi_bindings(store, outcome.sparql)
    return sorted(map(_canon_row, expected_bindings)) == sorted(
        map(_canon_row, actual_bindings)
    )


def _judge(judge_name: str, case: dict[str, Any], outcome: Any) -> bool:
    if judge_name == "execution" and case.get("data"):
        return _judge_execution(case["expected"], outcome, case["data"])
    return _judge_canonical(case["expected"], outcome)


# ---------------------------------------------------------------------------
# run() — drive every corpus case through NlPipeline for one config
# ---------------------------------------------------------------------------


def run(config_name: str) -> Report:
    configs = _load_configs()["configs"]
    config = configs[config_name]
    corpus = _load_corpus()
    shared_ontology = corpus.get("ontology", "")
    judge_name = config.get("judge", "canonical")
    max_repairs = config.get("max_repairs", 2)

    cases: list[CaseResult] = []
    for case in corpus["cases"]:
        ontology_ttl = case.get("ontology", shared_ontology)
        resolver = SchemaResolver.from_turtle(ontology_ttl)
        client = _client_for(config, case)
        pipeline = NlPipeline(
            client=client,
            resolver=resolver,
            ontology_ttl=ontology_ttl,
            max_repairs=max_repairs,
        )

        t0 = time.perf_counter()
        outcome = pipeline.run(case["nl"], params=case.get("params"))
        elapsed_ms = (time.perf_counter() - t0) * 1000

        passed = _judge(judge_name, case, outcome)
        cases.append(
            CaseResult(
                name=case["name"],
                expected=case["expected"],
                actual=outcome.sparql,
                passed=passed,
                elapsed_ms=elapsed_ms,
            )
        )

    return Report(config=config_name, cases=cases)


# ---------------------------------------------------------------------------
# write_report() — JSON + Markdown under REPORTS_DIR (gitignored)
# ---------------------------------------------------------------------------


def write_report(report: Report, *, out_dir: Path = REPORTS_DIR) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{report.config}.json"
    md_path = out_dir / f"{report.config}.md"

    payload = {
        "config": report.config,
        "pass_rate": report.pass_rate,
        "cases": [
            {"name": c.name, "passed": c.passed, "elapsed_ms": c.elapsed_ms}
            for c in report.cases
        ],
    }
    json_path.write_text(json.dumps(payload, indent=2) + "\n")

    lines = [
        f"# NL->SPARQL eval report: `{report.config}`",
        "",
        f"**Pass rate:** {report.pass_rate:.3f} "
        f"({sum(1 for c in report.cases if c.passed)}/{len(report.cases)})",
        "",
        "| Case | Passed | Elapsed (ms) |",
        "|------|--------|---------------|",
    ]
    for c in report.cases:
        lines.append(f"| {c.name} | {'✓' if c.passed else '✗'} | {c.elapsed_ms:.1f} |")
    md_path.write_text("\n".join(lines) + "\n")

    return json_path, md_path

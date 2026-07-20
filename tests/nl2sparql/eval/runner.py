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
from pydantic import BaseModel, Field, model_validator
from rdflib.plugins.sparql.parserutils import CompValue

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
# Load-time schema gate — CorpusCase / BaselineConfig (AI-SPEC §4b)
# ---------------------------------------------------------------------------
#
# A malformed/unparseable positive gold must FAIL the corpus load loudly
# rather than be silently dropped — a skipped case is a hidden coverage hole
# (AI-SPEC Critical Failure Mode 2). Pydantic gives us the load-time gate;
# the ``_gold_must_parse`` validator runs the deterministic SPARQL parser on
# every positive gold so a bad gold surfaces as a ``ValidationError`` the
# instant the corpus is read.


class CorpusCase(BaseModel):
    """One eval corpus entry (mirrors the ``corpus.yml`` case shape).

    Positive cases carry a gold ``expected`` SPARQL query the judge targets.
    Negative cases (``expect_refusal: true``) carry a human-readable rationale
    in ``expected`` instead — the honest-refusal convention scores them by the
    inverted signal (no transpilable AQL == PASS), so the gold-must-parse
    validator MUST skip them (AI-SPEC §5 "Scoring negatives").
    """

    name: str = Field(min_length=1)
    nl: str = Field(min_length=1)
    expected: str = Field(min_length=1)
    scripted: str | None = None
    ontology: str | None = None
    params: dict[str, object] | None = None
    data: str | None = None
    # The negatives marker. Pinned exact key — both the corpus and the
    # ``_judge`` inverted branch key on it.
    expect_refusal: bool = False

    @model_validator(mode="after")
    def _gold_must_parse(self) -> CorpusCase:
        # Only positive cases hold gold SPARQL. For refusal cases ``expected``
        # is a rationale string and must NOT be parsed as gold.
        if not self.expect_refusal:
            try:
                parse_sparql(self.expected)
            except SparqlParseError as exc:  # re-raise as pydantic ValueError
                raise ValueError(
                    f"gold `expected` SPARQL for case {self.name!r} does not parse: {exc}"
                ) from exc
        return self


class BaselineConfig(BaseModel):
    """One config's checked-in regression gate (a ``baseline.json`` entry).

    The scripted gate needs only ``pass_rate``/``passed``/``total``/``cases``.
    The optional live-reproducibility fields (``model``, ``temperature``,
    ``corpus_sha``) let Plan 04 fold a live-model run into ``baseline.json``
    without re-touching ``runner.py``.
    """

    pass_rate: float = Field(ge=0.0, le=1.0)
    passed: int = Field(ge=0)
    total: int = Field(ge=1)
    cases: dict[str, bool]
    model: str | None = None
    temperature: float | None = None
    corpus_sha: str | None = None


# ---------------------------------------------------------------------------
# Loaders — trusted checked-in YAML, always via yaml's safe_load only.
# ---------------------------------------------------------------------------


def _load_corpus() -> dict[str, Any]:
    corpus = yaml.safe_load(CORPUS_PATH.read_text())
    # Gate every case at load time — a malformed gold fails the load loudly
    # (raises ``ValidationError``) instead of being silently skipped. The
    # validated model is discarded; ``run()`` keeps its existing ``case[...]``
    # dict access unchanged (this is a gate, not a data-flow rewrite).
    for case in corpus.get("cases", []):
        CorpusCase(**case)
    return corpus


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


def _stable_repr(node: Any) -> str:
    """Return a repr of *node* that is stable across ``PYTHONHASHSEED`` runs.

    rdflib's translated algebra embeds raw Python ``set``/``frozenset``
    objects (e.g. every ``BGP``/``Project`` node's ``_vars``) and, for
    ``SELECT *`` queries specifically, a ``Project.PV`` field built via
    ``list(a_set)`` inside ``rdflib.plugins.sparql.algebra`` — see
    ``arango_sparql/translate/parser.py``'s docstring for the identical
    footgun the transpiler proper works around via ``explicit_projection``.
    Plain ``repr()`` of the algebra is therefore not safe to compare across
    interpreter processes (different ``PYTHONHASHSEED`` -> different set
    iteration order for the *same* logical set of variables).

    This walks the (``CompValue`` / ``dict`` / ``list`` / ``tuple`` /
    ``set``) tree and canonicalizes any set-derived structure (raw sets,
    and the specific ``PV`` key which is list-shaped but set-derived) to a
    sorted tuple before falling back to the builtin ``repr()`` for leaves.
    Explicitly-ordered structures (e.g. ``BGP.triples``) are left alone —
    only unordered/set-derived data is canonicalized.
    """
    if isinstance(node, CompValue):
        inner = ", ".join(
            f"{key!r}: {_stable_repr(sorted(value, key=str) if key == 'PV' and isinstance(value, list) else value)}"
            for key, value in node.items()
        )
        return f"{node.name}_{{{inner}}}"
    if isinstance(node, (set, frozenset)):
        return "{" + ", ".join(_stable_repr(v) for v in sorted(node, key=str)) + "}"
    if isinstance(node, dict):
        inner = ", ".join(f"{k!r}: {_stable_repr(v)}" for k, v in node.items())
        return f"{{{inner}}}"
    if isinstance(node, list):
        return "[" + ", ".join(_stable_repr(v) for v in node) + "]"
    if isinstance(node, tuple):
        return "(" + ", ".join(_stable_repr(v) for v in node) + ")"
    return repr(node)


def _canonical(sparql: str) -> str | None:
    try:
        return _stable_repr(parse_sparql(sparql).algebra)
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
    if case.get("expect_refusal"):
        # Inverted refusal signal (AI-SPEC §5 "Scoring negatives"): a negative
        # case PASSES iff the pipeline produced NO transpilable AQL. The
        # pipeline surfaces refusal as ``outcome.aql == ""`` + a
        # ``W_NL_TRANSLATION_FAILED`` warning (it never raises), so ``aql`` is
        # the authoritative signal — mirroring ``_judge_canonical``'s empty-AQL
        # check, but inverted. A non-empty AQL over invented terms FAILS.
        return not outcome.aql
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

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
import math
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from arango_query_core.nl import DenseRetriever, FewShotIndex, cached_few_shot_index
from pydantic import BaseModel, Field, model_validator
from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.term import Variable

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
# Curated few-shot bank (07-02) — same path SparqlAdapter's production default
# resolves to (engine_adapter.py::_FEWSHOT_BANK_PATH), shared here so the
# dense/bm25 sweep arms build against the identical bank file.
BANK_PATH = EVAL_DIR / "fewshot_bank.yml"


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
    without re-touching ``runner.py``. The three ``embedding_*`` fields
    (D-04, Phase 7 07-04) extend that same provenance convention for the
    dense-mode arms — captured at RUN TIME (never hardcoded) so a re-run
    reproduces the same retrieval order.
    """

    pass_rate: float = Field(ge=0.0, le=1.0)
    passed: int = Field(ge=0)
    total: int = Field(ge=1)
    cases: dict[str, bool]
    model: str | None = None
    temperature: float | None = None
    corpus_sha: str | None = None
    # Phase 7 07-04 additions — dense-run provenance (D-04).
    embedding_model: str | None = None
    embedding_revision: str | None = None
    sentence_transformers_version: str | None = None


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


def _skeleton(node: Any) -> str:
    """A ``repr`` of *node* with every ``Variable`` erased to ``"?"``.

    Used only to order the elements of a set/frozenset in a way that does
    NOT depend on the original variable names, so that alpha-renaming
    numbering (``_alpha_normalize``) is driven by structure rather than by
    whatever names the model happened to pick.
    """
    if isinstance(node, Variable):
        return "?"
    if isinstance(node, CompValue):
        return f"{node.name}{{" + ",".join(f"{k}:{_skeleton(v)}" for k, v in node.items()) + "}"
    if isinstance(node, (set, frozenset)):
        return "{" + ",".join(sorted(_skeleton(v) for v in node)) + "}"
    if isinstance(node, (list, tuple)):
        return "[" + ",".join(_skeleton(v) for v in node) + "]"
    if isinstance(node, dict):
        return "{" + ",".join(f"{k}:{_skeleton(v)}" for k, v in node.items()) + "}"
    return repr(node)


def _alpha_normalize(node: Any, mapping: dict[Variable, Variable]) -> Any:
    """Rebuild *node*, replacing each ``Variable`` with a canonical
    ``?v0``/``?v1``/... assigned on first occurrence in a deterministic,
    variable-name-independent walk.

    This makes the canonical judge *alpha-equivalent*: two queries that are
    identical up to a consistent bijective variable renaming (e.g. the gold's
    ``?s ?n`` vs a model's ``?person ?name``) collapse to one canonical form.
    It is SOUND — only consistent renamings unify, because a single bijection
    (``mapping``) is applied across the whole tree before comparison; a
    genuinely different query (extra triple, different projection, swapped
    predicate) cannot collide. Ordered structures (``BGP.triples``, ``PV``)
    seed the numbering; set-derived structures are ordered by ``_skeleton``
    so numbering never depends on the original names.
    """
    if isinstance(node, Variable):
        if node not in mapping:
            mapping[node] = Variable(f"v{len(mapping)}")
        return mapping[node]
    if isinstance(node, CompValue):
        return CompValue(
            node.name,
            **{key: _alpha_normalize(value, mapping) for key, value in node.items()},
        )
    if isinstance(node, (set, frozenset)):
        ordered = sorted(node, key=_skeleton)
        return type(node)(_alpha_normalize(v, mapping) for v in ordered)
    if isinstance(node, list):
        return [_alpha_normalize(v, mapping) for v in node]
    if isinstance(node, tuple):
        return tuple(_alpha_normalize(v, mapping) for v in node)
    if isinstance(node, dict):
        return {key: _alpha_normalize(value, mapping) for key, value in node.items()}
    return node


def _canonical(sparql: str) -> str | None:
    try:
        algebra = parse_sparql(sparql).algebra
    except SparqlParseError:
        return None
    return _stable_repr(_alpha_normalize(algebra, {}))


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
    return sorted(map(_canon_row, expected_bindings)) == sorted(map(_canon_row, actual_bindings))


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

    # Additive few_shot config read (Phase 7 07-04 / RESEARCH Pitfall 5):
    # `run(config_name) -> Report`'s signature and Report's shape stay
    # byte-identical; absent `few_shot:` == today's zero-shot behavior.
    few_shot_cfg = config.get("few_shot", {})
    few_shot_mode = few_shot_cfg.get("mode", "zero")
    few_shot_k = few_shot_cfg.get("k", 0)

    # Build the index ONCE per arm, outside the per-case loop (Pitfall 1 —
    # never per-case; a fresh FewShotIndex would reload the SentenceTransformer
    # model + re-embed the whole bank on every one of the 25 corpus cases).
    few_shot_index: FewShotIndex | None = None
    if few_shot_mode in ("dense", "bm25"):
        few_shot_index = cached_few_shot_index(str(BANK_PATH), few_shot_mode)
        if few_shot_mode == "dense":
            # D-06 belt-and-suspenders: a wrong-mode/degraded retriever must
            # never be silently filed as a dense number.
            assert isinstance(few_shot_index.retriever, DenseRetriever), (
                f"D-06 guard failed: config {config_name!r} requested mode='dense' "
                f"but the built index's retriever is {type(few_shot_index.retriever).__name__!r}, "
                "not DenseRetriever. This means sentence-transformers is not "
                "installed/importable (install `.[dense]` before running this arm) — "
                "never record this as a dense-mode measurement."
            )

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
            few_shot_k=few_shot_k,
            few_shot_index=few_shot_index,
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
# Paired-analysis helpers (B1) — the primary confirmatory signal.
#
# Pure-Python, no scipy: `paired_mcnemar` computes the EXACT two-sided
# McNemar test over the (b, c) discordant-pair counts between two Reports'
# per-case verdicts (aligned by case name — both arms MUST run the same 25
# cases); `bootstrap_paired_delta` resamples the shared case keys with
# replacement to report a 95% CI on the paired pass-rate delta. Neither
# function makes a network call or constructs a Report itself — they operate
# purely on the `{case_name: bool}` dicts a caller extracts from two Reports.
# ---------------------------------------------------------------------------


def _cases_as_dict(report_cases: dict[str, bool] | list[CaseResult]) -> dict[str, bool]:
    """Normalize either a raw ``{name: passed}`` dict or a ``Report.cases``
    list of :class:`CaseResult` into a ``{name: passed}`` dict."""
    if isinstance(report_cases, dict):
        return report_cases
    return {c.name: c.passed for c in report_cases}


def paired_mcnemar(
    zero_cases: dict[str, bool] | list[CaseResult],
    dense_cases: dict[str, bool] | list[CaseResult],
) -> tuple[int, int, float]:
    """Exact two-sided McNemar test over paired zero-shot vs dense verdicts.

    Returns ``(b, c, p_value)`` where ``b`` = count(zero False & dense True)
    (the "lift" flips) and ``c`` = count(zero True & dense False) (the
    "regression" flips). ``p_value`` is the exact binomial two-sided McNemar
    p-value: ``min(1.0, 2 * sum(C(n, i) for i in 0..min(b, c)) / 2**n)`` with
    ``n = b + c`` (returns ``1.0`` when ``n == 0`` — no discordant pairs means
    no evidence of a difference either way).

    Raises ``ValueError`` if the two case-verdict sets don't share identical
    keys — this guards against comparing misaligned arms (e.g. a dense run
    against a stale/partial zero-shot run over a different case subset).
    """
    zero = _cases_as_dict(zero_cases)
    dense = _cases_as_dict(dense_cases)
    if zero.keys() != dense.keys():
        raise ValueError(
            "paired_mcnemar requires zero_cases and dense_cases to share "
            f"identical keys; zero-only={set(zero) - set(dense)!r} "
            f"dense-only={set(dense) - set(zero)!r}"
        )
    b = sum(1 for name in zero if zero[name] is False and dense[name] is True)
    c = sum(1 for name in zero if zero[name] is True and dense[name] is False)
    n = b + c
    if n == 0:
        return b, c, 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1))
    p_value = min(1.0, 2 * tail / (2**n))
    return b, c, p_value


def bootstrap_paired_delta(
    zero_cases: dict[str, bool] | list[CaseResult],
    dense_cases: dict[str, bool] | list[CaseResult],
    iters: int = 10000,
    seed: int = 1234,
) -> tuple[float, float, float]:
    """Bootstrap CI on the paired pass-rate delta (dense - zero).

    Returns ``(delta, lo, hi)``: ``delta`` is the observed
    ``dense_pass_rate - zero_pass_rate`` over the shared case keys; ``lo``/
    ``hi`` are the 2.5th/97.5th percentile of the delta resampled (with
    replacement) ``iters`` times over the shared case keys, using a seeded
    ``random.Random`` for reproducibility.

    Raises ``ValueError`` if the two case-verdict sets don't share identical
    keys (same guard as :func:`paired_mcnemar`).
    """
    zero = _cases_as_dict(zero_cases)
    dense = _cases_as_dict(dense_cases)
    if zero.keys() != dense.keys():
        raise ValueError(
            "bootstrap_paired_delta requires zero_cases and dense_cases to "
            f"share identical keys; zero-only={set(zero) - set(dense)!r} "
            f"dense-only={set(dense) - set(zero)!r}"
        )
    names = sorted(zero.keys())
    n = len(names)
    if n == 0:
        return 0.0, 0.0, 0.0

    def _pass_rate(sample_names: list[str], verdicts: dict[str, bool]) -> float:
        return sum(1 for name in sample_names if verdicts[name]) / len(sample_names)

    observed_delta = _pass_rate(names, dense) - _pass_rate(names, zero)

    rng = random.Random(seed)
    deltas: list[float] = []
    for _ in range(iters):
        sample = [names[rng.randrange(n)] for _ in range(n)]
        deltas.append(_pass_rate(sample, dense) - _pass_rate(sample, zero))
    deltas.sort()
    lo_idx = max(0, int(0.025 * len(deltas)))
    hi_idx = min(len(deltas) - 1, int(0.975 * len(deltas)))
    return observed_delta, deltas[lo_idx], deltas[hi_idx]


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
        "cases": [{"name": c.name, "passed": c.passed, "elapsed_ms": c.elapsed_ms} for c in report.cases],
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

"""Re-point spike: can NLQueryEngine + a SparqlAdapter reproduce the current
scripted eval verdicts (baseline.json), proving the shared engine is a faithful
drop-in for nl2sparql's private loop?

Behavior-preserving check ONLY — no repo files changed. Run from repo root.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml
from arango_query_core.nl.engine import NLQueryEngine
from arango_query_core.nl.seams import GuardrailVerdict, ValidationResult

from arango_sparql.api import translate as api_translate
from arango_sparql.errors import SparqlError
from arango_sparql.nl2sparql.client import ScriptedLLMClient
from arango_sparql.nl2sparql.models import LLMResponse
from arango_sparql.translate.parser import parse_sparql
from arango_sparql.translate.resolver import SchemaResolver

EVAL = Path("tests/nl2sparql/eval")
corpus = yaml.safe_load((EVAL / "corpus.yml").read_text())
configs = yaml.safe_load((EVAL / "configs.yml").read_text())["configs"]
baseline = json.loads((EVAL / "baseline.json").read_text())["configs"]["scripted"]["cases"]
default_ontology = corpus.get("ontology", "")
max_repairs = int(configs["scripted"].get("max_repairs", 2))


def _wrap(sparql: str) -> str:
    return f"```sparql\n{sparql.strip()}\n```"


def _canonical(sparql: str) -> str | None:
    try:
        return repr(parse_sparql(sparql).algebra)
    except SparqlError:
        return None


class ScriptedProviderBridge:
    """Bridge our ScriptedLLMClient -> engine's LLMProvider(generate(system,user))."""

    def __init__(self, sparql: str) -> None:
        self._client = ScriptedLLMClient(
            [LLMResponse(content=_wrap(sparql), prompt_tokens=0, completion_tokens=0, total_tokens=0, cached_tokens=0)],
            latency_ms=0,
        )

    def generate(self, system: str, user: str) -> tuple[str, dict[str, int]]:
        resp = self._client.generate(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )
        return resp.content, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cached_tokens": 0}


class SparqlAdapter:
    """The 5 seams, mapped onto shipped nl2sparql/transpiler pieces (spike)."""

    language = "sparql"

    def __init__(self, ontology_ttl: str) -> None:
        self.ontology_ttl = ontology_ttl
        self.resolver = SchemaResolver.from_turtle(ontology_ttl)

    def grammar_prompt_section(self, schema_context: str) -> str:  # seam 1
        return (
            "You are a SPARQL 1.1 expert. Generate a single valid SPARQL 1.1 query.\n"
            "Ontology (Turtle):\n" + self.ontology_ttl
        )

    def few_shot_index(self):  # seam 2 (zero-shot for behavior-preservation)
        return None

    def validate(self, query: str) -> ValidationResult:  # seam 3
        try:
            api_translate(query, resolver=self.resolver)
            return ValidationResult(ok=True)
        except SparqlError as exc:
            return ValidationResult(ok=False, error=str(exc), code=getattr(exc, "code", ""))

    def repair_hint(self, query: str, failure: ValidationResult) -> str:  # seam 4
        return failure.error

    def guardrails(self, query: str, context: dict) -> GuardrailVerdict:  # seam 5
        return GuardrailVerdict(allowed=True)


print(f"{'case':<24} {'engine_ok':<10} {'engine_pass':<12} {'baseline':<9} verdict")
print("-" * 70)
all_agree = True
for case in corpus["cases"]:
    name = case["name"]
    scripted = case.get("scripted", case["expected"])
    ontology = case.get("ontology", default_ontology)
    adapter = SparqlAdapter(ontology)
    engine = NLQueryEngine(
        provider=ScriptedProviderBridge(scripted),
        adapter=adapter,
        few_shot_k=0,
        max_retries=max_repairs,
    )
    res = engine.generate(case["nl"], schema_context="")
    ce = _canonical(case["expected"])
    cq = _canonical(res.query) if res.query else None
    engine_pass = bool(res.ok and ce is not None and cq is not None and ce == cq)
    baseline_pass = bool(baseline[name])
    agree = engine_pass == baseline_pass
    all_agree = all_agree and agree
    print(f"{name:<24} {str(res.ok):<10} {str(engine_pass):<12} {str(baseline_pass):<9} {'AGREE' if agree else '*** MISMATCH ***'}")

engine_rate = sum(
    1
    for case in corpus["cases"]
    if (
        lambda r, ce: bool(r.ok and ce is not None and _canonical(r.query) is not None and ce == _canonical(r.query))
    )(
        NLQueryEngine(
            provider=ScriptedProviderBridge(case.get("scripted", case["expected"])),
            adapter=SparqlAdapter(case.get("ontology", default_ontology)),
            few_shot_k=0,
            max_retries=max_repairs,
        ).generate(case["nl"], schema_context=""),
        _canonical(case["expected"]),
    )
) / len(corpus["cases"])
print("-" * 70)
print(f"engine scripted pass_rate = {engine_rate:.4f}  (baseline = 0.8333)")
print("SPIKE RESULT:", "PASS — engine reproduces all verdicts" if all_agree else "FAIL — verdict mismatch")

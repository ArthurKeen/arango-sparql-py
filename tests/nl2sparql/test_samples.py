"""Unit tests for :mod:`arango_sparql.nl2sparql.samples`.

Covers both generation paths (rule-based default + optional LLM) plus the
degradation contract: empty / malformed ontologies and LLM failures must
never raise, they return ``[]`` or fall back to the deterministic
rule-based generator. Mirrors the sister project's
``suggest_nl_queries`` test posture.
"""

from __future__ import annotations

import pytest

from arango_sparql.nl2sparql import LLMResponse, ScriptedLLMClient, suggest_nl_queries
from arango_sparql.nl2sparql.samples import _parse_llm_lines

ONTOLOGY_TTL = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

:Person a owl:Class .
:Organization a owl:Class .
:worksFor a owl:ObjectProperty ;
    rdfs:domain :Person ;
    rdfs:range :Organization .
""".strip()


def _resp(content: str) -> LLMResponse:
    return LLMResponse(content=content, prompt_tokens=10, completion_tokens=10, total_tokens=20)


# ---------------------------------------------------------------------------
# Rule-based path (the default and the fallback)
# ---------------------------------------------------------------------------


def test_rule_based_generates_questions_from_classes_and_properties() -> None:
    out = suggest_nl_queries(ONTOLOGY_TTL, use_llm=False)
    assert out, "a non-empty ontology must yield suggestions"
    blob = "\n".join(out).lower()
    # Class templates.
    assert "show 10 organizations" in blob
    assert "how many persons are there?" in blob
    # Object-property templates (domain -> range traversal).
    assert any("for each person" in q.lower() for q in out)
    assert any("count organizations per person" in q.lower() for q in out)


def test_rule_based_respects_count_cap() -> None:
    out = suggest_nl_queries(ONTOLOGY_TTL, count=3, use_llm=False)
    assert len(out) == 3


def test_rule_based_dedupes_case_insensitively() -> None:
    out = suggest_nl_queries(ONTOLOGY_TTL, use_llm=False)
    lowered = [q.lower() for q in out]
    assert len(lowered) == len(set(lowered))


def test_empty_ontology_returns_empty() -> None:
    assert suggest_nl_queries("", use_llm=False) == []
    assert suggest_nl_queries("   \n  ", use_llm=False) == []
    assert suggest_nl_queries(None, use_llm=False) == []


def test_malformed_ontology_returns_empty_not_raises() -> None:
    # owl_graph_view raises OwlParseError internally; the suggestor must
    # swallow it and degrade to an empty list.
    assert suggest_nl_queries("this is not turtle :{(", use_llm=False) == []


def test_classes_without_object_properties_still_yield_questions() -> None:
    ttl = """
    @prefix : <http://ex.org/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    :Widget a owl:Class .
    """.strip()
    out = suggest_nl_queries(ttl, use_llm=False)
    assert any("widget" in q.lower() for q in out)


# ---------------------------------------------------------------------------
# Optional LLM path
# ---------------------------------------------------------------------------


def test_llm_path_used_when_client_and_use_llm_true() -> None:
    client = ScriptedLLMClient([_resp("Who works where?\nList all organizations")], latency_ms=0)
    out = suggest_nl_queries(ONTOLOGY_TTL, use_llm=True, client=client)
    assert out == ["Who works where?", "List all organizations"]
    # The model was actually consulted (one generate call).
    assert len(client.calls) == 1


def test_use_llm_false_never_calls_client() -> None:
    client = ScriptedLLMClient([_resp("ignored")], latency_ms=0)
    out = suggest_nl_queries(ONTOLOGY_TTL, use_llm=False, client=client)
    assert client.calls == []
    # Falls through to rule-based output.
    assert any("show 10" in q.lower() for q in out)


def test_llm_failure_falls_back_to_rule_based() -> None:
    client = ScriptedLLMClient([RuntimeError("transport boom")], latency_ms=0)
    out = suggest_nl_queries(ONTOLOGY_TTL, use_llm=True, client=client)
    # Did not raise, and produced the deterministic rule-based set.
    assert any("show 10" in q.lower() for q in out)


def test_llm_empty_output_falls_back_to_rule_based() -> None:
    client = ScriptedLLMClient([_resp("   \n\n")], latency_ms=0)
    out = suggest_nl_queries(ONTOLOGY_TTL, use_llm=True, client=client)
    assert any("show 10" in q.lower() for q in out)


# ---------------------------------------------------------------------------
# LLM line parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1. First question\n2) Second question", ["First question", "Second question"]),
        ("- Bullet one\n* Bullet two", ["Bullet one", "Bullet two"]),
        ('"Quoted question"\n\n', ["Quoted question"]),
        ("Good question\nSELECT * WHERE { ?s ?p ?o }", ["Good question"]),
        ("PREFIX ex: <x>\nReal question", ["Real question"]),
        ("okay\nokay\nOKAY", ["okay"]),  # case-insensitive dedupe
        ("a\nbb", []),  # both shorter than the 4-char floor
    ],
)
def test_parse_llm_lines(raw: str, expected: list[str]) -> None:
    assert _parse_llm_lines(raw, count=8) == expected


def test_parse_llm_lines_respects_count() -> None:
    raw = "\n".join(f"Question number {i}" for i in range(20))
    assert len(_parse_llm_lines(raw, count=5)) == 5

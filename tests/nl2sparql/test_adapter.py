"""SparqlLanguageAdapter + shared-engine integration (no network).

Covers the five seams and the end-to-end ``nl_to_sparql`` path with a
scripted provider: transpiler-backed validation (parse AND translate),
repair-hint propagation of stable error codes, corpus loading through
the shared few-shot format, and the no-provider fallback contract.
"""

from __future__ import annotations

import pytest

pytest.importorskip("arango_query_core", reason="nl extra (arango-query-core) required")

from arango_query_core.nl import NLQueryEngine  # noqa: E402

from arango_sparql.nl2sparql import nl_to_sparql  # noqa: E402
from arango_sparql.nl2sparql.adapter import SparqlLanguageAdapter  # noqa: E402
from arango_sparql.translate.resolver import SchemaResolver  # noqa: E402

ONTOLOGY_TTL = """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
<http://example.org/Person> a owl:Class ; phys:collectionName "Person" .
<http://example.org/name> a owl:DatatypeProperty .
"""

GOOD_SPARQL = "PREFIX ex: <http://example.org/> SELECT ?name WHERE { ?p a ex:Person ; ex:name ?name }"


def _adapter() -> SparqlLanguageAdapter:
    resolver = SchemaResolver.from_turtle(ONTOLOGY_TTL)
    return SparqlLanguageAdapter(resolver=resolver, ontology_ttl=ONTOLOGY_TTL)


class ScriptedProvider:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def generate(self, system: str, user: str) -> tuple[str, dict[str, int]]:
        self.calls.append((system, user))
        return self._responses.pop(0), {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cached_tokens": 0,
        }


# ---------------------------------------------------------------------------
# Seam 3 — transpiler-backed validation
# ---------------------------------------------------------------------------


def test_validate_accepts_translatable_query() -> None:
    assert _adapter().validate(GOOD_SPARQL).ok


def test_validate_rejects_parse_error_with_stable_code() -> None:
    result = _adapter().validate("SELECT WHERE this is not sparql")
    assert not result.ok
    assert result.code == "E_SPARQL_PARSE"
    assert "E_SPARQL_PARSE" in result.error


def test_validate_rejects_untranslatable_query_not_just_unparseable() -> None:
    # Parses fine (legal SPARQL 1.1) but the deterministic translator
    # refuses SERVICE — validation must fail at the TRANSLATE stage.
    query = (
        "PREFIX ex: <http://example.org/> SELECT ?s WHERE { "
        "SERVICE <http://remote.example/sparql> { ?s ?p ?o } }"
    )
    result = _adapter().validate(query)
    assert not result.ok
    assert result.code == "E_SPARQL_UNSUPPORTED"
    # The repair message nudges toward supported SPARQL 1.1 shapes.
    assert "SPARQL 1.1 alternative" in result.error


def test_validate_rejects_empty() -> None:
    result = _adapter().validate("   ")
    assert not result.ok and result.code == "E_EMPTY"


# ---------------------------------------------------------------------------
# Seams 1 + 2 — grammar prompt and corpus
# ---------------------------------------------------------------------------


def test_grammar_prompt_embeds_ontology() -> None:
    section = _adapter().grammar_prompt_section("")
    assert "SPARQL 1.1 expert" in section
    assert 'phys:collectionName "Person"' in section


def test_corpus_loads_and_retrieves_sparql_examples() -> None:
    # Without rank_bm25 the index silently degrades to a no-op retriever
    # (by design); retrieval assertions only make sense with it present.
    pytest.importorskip("rank_bm25", reason="nl extra (rank_bm25) required for retrieval")
    index = _adapter().few_shot_index()
    assert index is not None
    assert len(index.examples) >= 20
    top = index.retrieve("how many people are in each department?", k=2)
    assert top and any("GROUP BY" in query for _, query in top)


# ---------------------------------------------------------------------------
# End-to-end through the shared engine (scripted provider)
# ---------------------------------------------------------------------------


def test_engine_end_to_end_repairs_untranslatable_first_attempt() -> None:
    provider = ScriptedProvider(
        [
            # First attempt: parseable but untranslatable (SERVICE).
            "```sparql\nSELECT ?s WHERE { SERVICE <http://r.example/> { ?s ?p ?o } }\n```",
            # Repaired attempt: translatable.
            f"```sparql\n{GOOD_SPARQL}\n```",
        ]
    )
    engine = NLQueryEngine(provider=provider, adapter=_adapter(), max_retries=2)
    outcome = engine.generate("names of all people")
    assert outcome.ok and outcome.retries == 1
    assert "ex:Person" in outcome.query
    # The retry prompt carried the stable error code to the LLM.
    assert "E_SPARQL_UNSUPPORTED" in provider.calls[1][1]


def test_nl_to_sparql_happy_path_with_injected_provider() -> None:
    result = nl_to_sparql(
        "names of all people",
        ontology_ttl=ONTOLOGY_TTL,
        provider=ScriptedProvider([f"```sparql\n{GOOD_SPARQL}\n```"]),
    )
    assert result.method == "llm"
    assert result.sparql == GOOD_SPARQL
    assert result.total_tokens == 120
    assert result.confidence > 0


def test_nl_to_sparql_without_provider_is_explicitly_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("LLM_PROVIDER", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    result = nl_to_sparql("anything", ontology_ttl=ONTOLOGY_TTL)
    assert result.method == "unavailable"
    assert result.sparql == ""
    assert "provider" in result.explanation.lower()


def test_nl_to_sparql_failure_reports_last_error() -> None:
    result = nl_to_sparql(
        "names of all people",
        ontology_ttl=ONTOLOGY_TTL,
        provider=ScriptedProvider(["not sparql at all"] * 3),
        max_retries=2,
    )
    assert result.method == "llm_failed"
    assert result.sparql == "" and result.retries == 2
    assert "E_SPARQL_PARSE" in result.explanation

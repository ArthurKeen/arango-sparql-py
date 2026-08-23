"""SPARQL example postconditions — unit checks + end-to-end through the engine.

Two layers:

* Unit — call each shipped postcondition's ``check`` directly on hand-written
  SPARQL, asserting it flags the semantically-wrong shape and (critically) does
  NOT false-positive on valid BIND/aggregate bindings or non-SELECT queries.
* Integration — drive :func:`arango_sparql.nl2sparql.nl_to_sparql` with a
  scripted provider so a violation rides the real retry budget: it retries with
  the correction, succeeds when the next candidate satisfies the invariant, and
  fails closed (``method="llm_failed"``, reason+code surfaced) when it never does.
"""

from __future__ import annotations

import pytest

pytest.importorskip("arango_query_core", reason="nl extra (arango-query-core) required")

from arango_query_core.nl import PostconditionContext  # noqa: E402

from arango_sparql.nl2sparql import nl_to_sparql  # noqa: E402
from arango_sparql.nl2sparql.postconditions import (  # noqa: E402
    ForbidUnboundProjection,
    RequireResultLimit,
)

ONTOLOGY_TTL = """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
<http://example.org/Person> a owl:Class ; phys:collectionName "Person" .
<http://example.org/name> a owl:DatatypeProperty .
"""

_PREFIX = "PREFIX ex: <http://example.org/> "
_NO_LIMIT = _PREFIX + "SELECT ?name WHERE { ?p a ex:Person ; ex:name ?name }"
_WITH_LIMIT = _NO_LIMIT + " LIMIT 100"


def _ctx() -> PostconditionContext:
    return PostconditionContext(schema_summary="", question="q", attempt=0)


class ScriptedProvider:
    """LLMProvider double: returns queued responses, records calls."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def generate(self, system: str, user: str) -> tuple[str, dict[str, int]]:
        self.calls.append((system, user))
        content = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        return content, {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cached_tokens": 0,
        }


# ---------------------------------------------------------------------------
# RequireResultLimit — unit
# ---------------------------------------------------------------------------


def test_require_limit_flags_unbounded_select() -> None:
    v = RequireResultLimit().check(_NO_LIMIT, context=_ctx())
    assert v is not None
    assert v.code == "require_result_limit"
    assert "unbounded" in v.reason.lower() and "LIMIT" in v.suggested_hint


def test_require_limit_accepts_bounded_select() -> None:
    assert RequireResultLimit().check(_WITH_LIMIT, context=_ctx()) is None


def test_require_limit_rejects_over_ceiling() -> None:
    v = RequireResultLimit(max_rows=10).check(_NO_LIMIT + " LIMIT 500", context=_ctx())
    assert v is not None and "exceeds" in v.reason and "10" in v.suggested_hint


def test_require_limit_accepts_within_ceiling() -> None:
    assert RequireResultLimit(max_rows=10).check(_NO_LIMIT + " LIMIT 5", context=_ctx()) is None


def test_require_limit_ignores_non_select() -> None:
    # ASK has no row projection to bound — must be accepted untouched.
    assert RequireResultLimit().check("ASK { ?s ?p ?o }", context=_ctx()) is None


def test_require_limit_prompt_section_states_the_rule() -> None:
    assert "LIMIT" in RequireResultLimit().prompt_section()
    assert "at most 25" in RequireResultLimit(max_rows=25).prompt_section()


# ---------------------------------------------------------------------------
# ForbidUnboundProjection — unit
# ---------------------------------------------------------------------------


def test_unbound_projection_flags_variable_never_bound() -> None:
    q = _PREFIX + "SELECT ?name ?bogus WHERE { ?p a ex:Person ; ex:name ?name }"
    v = ForbidUnboundProjection().check(q, context=_ctx())
    assert v is not None
    assert v.code == "forbid_unbound_projection"
    assert "?bogus" in v.reason and "?bogus" in v.suggested_hint


def test_unbound_projection_accepts_fully_bound() -> None:
    assert ForbidUnboundProjection().check(_NO_LIMIT, context=_ctx()) is None


def test_unbound_projection_accepts_bind_target() -> None:
    # ?label is bound by BIND, not a triple — must NOT be a false positive.
    q = _PREFIX + "SELECT ?name ?label WHERE { ?p ex:name ?name BIND(?name AS ?label) }"
    assert ForbidUnboundProjection().check(q, context=_ctx()) is None


def test_unbound_projection_accepts_aggregate_result() -> None:
    q = _PREFIX + "SELECT (COUNT(?p) AS ?n) WHERE { ?p a ex:Person }"
    assert ForbidUnboundProjection().check(q, context=_ctx()) is None


def test_unbound_projection_accepts_select_star() -> None:
    # SELECT * declares no projection list — nothing to verify.
    assert ForbidUnboundProjection().check(_PREFIX + "SELECT * WHERE { ?s ?p ?o }", context=_ctx()) is None


def test_unbound_projection_ignores_non_select() -> None:
    assert ForbidUnboundProjection().check("ASK { ?s ?p ?o }", context=_ctx()) is None


def test_unbound_projection_prompt_section_states_the_rule() -> None:
    section = ForbidUnboundProjection().prompt_section()
    assert "bound" in section and "SELECT" in section


# ---------------------------------------------------------------------------
# End-to-end through nl_to_sparql — the retry budget
# ---------------------------------------------------------------------------


def test_postcondition_retries_then_succeeds() -> None:
    provider = ScriptedProvider([f"```sparql\n{_NO_LIMIT}\n```", f"```sparql\n{_WITH_LIMIT}\n```"])
    result = nl_to_sparql(
        "people",
        ontology_ttl=ONTOLOGY_TTL,
        provider=provider,
        postconditions=[RequireResultLimit()],
    )
    assert result.method == "llm"
    assert result.sparql == _WITH_LIMIT
    assert result.retries == 1
    # The correction reached the model on the retry.
    assert "LIMIT" in provider.calls[1][1]


def test_postcondition_exhaustion_fails_closed() -> None:
    provider = ScriptedProvider([f"```sparql\n{_NO_LIMIT}\n```"])
    result = nl_to_sparql(
        "people",
        ontology_ttl=ONTOLOGY_TTL,
        provider=provider,
        postconditions=[RequireResultLimit()],
        max_retries=2,
    )
    assert result.method == "llm_failed"
    assert result.sparql == ""  # a valid-but-rejected query is never returned
    assert result.retries == 2
    assert "require_result_limit" in result.explanation
    assert "unbounded" in result.explanation.lower()


def test_postcondition_announced_in_system_prompt() -> None:
    provider = ScriptedProvider([f"```sparql\n{_WITH_LIMIT}\n```"])
    nl_to_sparql(
        "people",
        ontology_ttl=ONTOLOGY_TTL,
        provider=provider,
        postconditions=[RequireResultLimit()],
    )
    system = provider.calls[0][0]
    assert "INVARIANT" in system and "LIMIT" in system


def test_no_postconditions_leaves_behaviour_unchanged() -> None:
    # The same unbounded query is accepted when no postcondition is supplied.
    provider = ScriptedProvider([f"```sparql\n{_NO_LIMIT}\n```"])
    result = nl_to_sparql("people", ontology_ttl=ONTOLOGY_TTL, provider=provider)
    assert result.method == "llm"
    assert result.sparql == _NO_LIMIT
    assert result.retries == 0

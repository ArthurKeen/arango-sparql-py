"""Query mining, slice 2: drafting question + SPARQL and the verify/retry loop.

Ported from arango-cypher-py's ``tests/test_query_mining_generate.py``. The
client is :class:`~arango_sparql.nl2sparql.client.ScriptedLLMClient`, which
honours the real ``LLMClient.generate(messages) -> LLMResponse`` contract;
translation goes through the real translator with a real ontology, so a draft
the translator refuses is refused here too.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from arango_sparql.nl2sparql.client import ScriptedLLMClient
from arango_sparql.nl2sparql.models import LLMResponse
from arango_sparql.query_mining.generate import (
    Draft,
    GenerationError,
    build_system_prompt,
    build_user_prompt,
    parse_draft,
    physical_name_table,
)
from arango_sparql.query_mining.harvest import CANVAS_ACTIONS, SAVED_QUERIES, SavedQuery
from arango_sparql.query_mining.miner import (
    MAX_CONSECUTIVE_PROVIDER_FAILURES,
    mine_database,
    mine_query,
)
from arango_sparql.translate.mapping import MappingBundle
from arango_sparql.translate.resolver import SchemaResolver
from tests.helpers.fake_arango import FakeDb

NS = "http://ex.org/movies#"
TTL = f"""
@prefix :     <{NS}> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix phys: <https://arango.solutions/phys#> .
:Person a owl:Class ; phys:collectionName "persons" ; phys:mappingStyle "COLLECTION" .
:Movie a owl:Class ; phys:collectionName "movies" ; phys:mappingStyle "COLLECTION" .
:ACTED_IN a owl:ObjectProperty ; rdfs:domain :Person ; rdfs:range :Movie ;
  phys:edgeCollectionName "acted_in" ; phys:mappingStyle "DEDICATED_COLLECTION" .
"""

BUNDLE = MappingBundle(
    conceptual_schema={"entities": [{"name": "Person"}, {"name": "Movie"}]},
    physical_mapping={
        "entities": {
            "Person": {"style": "COLLECTION", "collectionName": "persons"},
            "Movie": {"style": "COLLECTION", "collectionName": "movies"},
            "Doc": {"style": "LABEL", "collectionName": "vertices", "typeField": "type", "typeValue": "Doc"},
        },
        "relationships": {"ACTED_IN": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "acted_in"}},
    },
    metadata={},
)

KEANU = {"_id": "persons/1", "_key": "1", "name": "Keanu Reeves"}
EDGE = {"_id": "acted_in/5", "_from": "persons/1", "_to": "movies/9"}

SOURCE_AQL = "FOR m IN movies FILTER m.title == @t FOR p, e IN 1..1 INBOUND m acted_in RETURN e"
GOOD = f'PREFIX : <{NS}>\nSELECT ?p ?m WHERE {{ ?p :ACTED_IN ?m . ?m :title "The Matrix" }}'


def _client(replies: list[str | BaseException]) -> ScriptedLLMClient:
    return ScriptedLLMClient(
        [r if isinstance(r, BaseException) else LLMResponse(content=r) for r in replies], latency_ms=0
    )


def _reply(question: str, sparql: str) -> str:
    return "```json\n" + json.dumps({"question": question, "sparql": sparql}) + "\n```"


def _user_messages(client: ScriptedLLMClient) -> list[str]:
    return [next(m["content"] for m in call if m["role"] == "user") for call in client.calls]


def _db(*, candidate_rows: list[Any] | None = None) -> FakeDb:
    def handler(q: str, b: dict[str, Any]) -> list[Any]:
        if q == SOURCE_AQL:
            return [EDGE]
        if "DOCUMENT(id)" in q:  # the identity lookup: these documents have no _uri
            return [[i, i] for i in b["ids"]]
        return candidate_rows if candidate_rows is not None else [{"p": "persons/1", "m": "movies/9"}]

    # The collections exist, so an _id from them in a binding reads as a resource.
    return FakeDb({"persons": [], "movies": [], "acted_in": []}, handler=handler)


def _saved(**kw: Any) -> SavedQuery:
    base: dict[str, Any] = {
        "source": SAVED_QUERIES,
        "key": "q1",
        "name": "Cast of a movie",
        "description": "Everyone who acted in a given movie",
        "aql": SOURCE_AQL,
        "bind_vars": {"t": "The Matrix"},
        "graph": "movies",
    }
    base.update(kw)
    return SavedQuery(**base)


def _mine(db: FakeDb, client: ScriptedLLMClient, **kw: Any) -> Any:
    return mine_query(
        db,
        _saved(),
        client,
        resolver=SchemaResolver.from_turtle(TTL),
        system_prompt=build_system_prompt(BUNDLE, schema_summary="(schema)", namespace=NS),
        **kw,
    )


# ---------------------------------------------------------------------------
# Drafting
# ---------------------------------------------------------------------------


class TestDraft:
    def test_parses_json_inside_fences_and_prose(self) -> None:
        d = parse_draft('Sure!\n```json\n{"question": "Who?", "sparql": "SELECT * { ?s ?p ?o }"}\n```')
        assert d == Draft("Who?", "SELECT * { ?s ?p ?o }")

    @pytest.mark.parametrize(
        ("reply", "message"),
        [
            ("no json here", "no JSON object"),
            ('{"question": "q", "sparql": "SELECT", ', "no JSON object"),
            ('{"question": "", "sparql": "SELECT * { ?s ?p ?o }"}', '"question"'),
            ('{"question": "q"}', '"sparql"'),
            ('{"question": "q", "cypher": "MATCH (n) RETURN n"}', '"sparql"'),
        ],
    )
    def test_rejects_unusable_replies_with_feedback(self, reply: str, message: str) -> None:
        with pytest.raises(GenerationError, match=message):
            parse_draft(reply)

    def test_the_name_table_maps_physical_to_ontology_terms(self) -> None:
        table = physical_name_table(BUNDLE)
        assert "- collection persons -> :Person" in table
        assert "- documents in vertices with type = 'Doc' -> :Doc" in table
        assert "- edge collection acted_in -> property :ACTED_IN" in table

    def test_the_system_prompt_names_the_namespace_and_the_schema(self) -> None:
        system = build_system_prompt(BUNDLE, schema_summary="Classes: :Person, :Movie", namespace=NS)
        assert f"PREFIX : <{NS}>" in system
        assert "Classes: :Person, :Movie" in system
        assert '{"question": "...", "sparql": "..."}' in system

    def test_the_prompt_carries_the_source_and_the_selected_node(self) -> None:
        q = _saved(source=CANVAS_ACTIONS, bind_vars={"nodes": ["persons/1"]})
        user = build_user_prompt(q, q.bind_vars, start_properties={"name": "Keanu Reeves"})
        assert SOURCE_AQL in user
        assert "Everyone who acted in a given movie" in user
        assert 'match it by: name = "Keanu Reeves"' in user


# ---------------------------------------------------------------------------
# The verify / retry loop
# ---------------------------------------------------------------------------


class TestMineQuery:
    def test_retries_with_feedback_until_a_draft_verifies(self) -> None:
        client = _client(
            [
                "I think the answer is SELECT ...",  # no JSON
                _reply("Did anyone act in it?", f"PREFIX : <{NS}>\nASK {{ ?p :ACTED_IN ?m }}"),
                _reply("Who acted in The Matrix?", GOOD),
            ]
        )
        outcome = _mine(_db(), client)
        assert outcome.verified, outcome.reason
        ex = outcome.example
        assert ex.attempts == 3
        assert ex.question == "Who acted in The Matrix?"
        assert ex.sparql == GOOD
        assert ex.verdict.kind == "same_vertices"  # the saved query returned the edge
        assert "acted_in" in ex.aql or "acted_in" in ex.bind_vars.values()
        users = _user_messages(client)
        assert "no JSON object" in users[1]
        assert "is a ASK query; write a SELECT" in users[2]
        assert [a.failure[:30] for a in outcome.failed_attempts] == [
            "the reply contained no JSON ob",
            "the SPARQL is a ASK query; wri",
        ]

    def test_an_unknown_class_is_fed_back_with_a_hint(self) -> None:
        bad = f"PREFIX : <{NS}>\nSELECT ?p WHERE {{ ?p a :Actor }}"
        client = _client([_reply("q", bad), _reply("Who acted in The Matrix?", GOOD)])
        outcome = _mine(_db(), client)
        assert outcome.verified
        assert "Use only the classes and properties the schema lists" in _user_messages(client)[1]

    def test_different_results_are_rejected_and_explained(self) -> None:
        client = _client([_reply("q", GOOD)])
        outcome = _mine(_db(candidate_rows=[{"p": "persons/2", "m": "movies/9"}]), client)
        assert not outcome.verified
        assert (
            "its results differ from the saved query's (resources differ: 1 missing, 1 extra"
            in outcome.reason
        )
        assert outcome.failed_attempts[0].sparql == GOOD

    def test_a_source_that_cannot_run_is_never_sent_to_the_model(self) -> None:
        client = _client([_reply("q", GOOD)])
        outcome = _mine(FakeDb(handler=lambda q, b: []), client)
        assert outcome.reason == "source: returns no rows with its saved bind values"
        assert client.calls == []

    def test_a_provider_failure_ends_the_query_without_retries(self) -> None:
        client = _client([RuntimeError("401 Unauthorized"), _reply("q", GOOD)])
        outcome = _mine(_db(), client)
        assert outcome.provider_failed
        assert outcome.reason == "LLM provider failed: RuntimeError: 401 Unauthorized"
        assert len(client.calls) == 1


class TestMineDatabase:
    def _db(self, n: int) -> FakeDb:
        docs = [
            {
                "_key": f"q{i}",
                "name": f"Cast {i}",
                "description": "Everyone who acted in a given movie",
                "queryText": SOURCE_AQL,
                "bindVariables": {"t": "The Matrix", "n": i},
                "graphId": "movies",
            }
            for i in range(n)
        ]
        docs.append(
            {
                "_key": "builtin",
                "name": "Fetch edges of type @@edgeType (default)",
                "queryText": "FOR d IN @@edgeType LIMIT 100 RETURN d",
                "bindVariables": {"@edgeType": "acted_in"},
                "graphId": "movies",
            }
        )
        inner = _db()
        return FakeDb(
            {SAVED_QUERIES: docs, "persons": [], "movies": [], "acted_in": []},
            handler=inner.aql._handler,
            graphs={"movies": []},
        )

    def _run(self, db: FakeDb, client: ScriptedLLMClient) -> Any:
        return mine_database(
            db,
            BUNDLE,
            client,
            resolver=SchemaResolver.from_turtle(TTL),
            schema_summary="",
            namespace=NS,
            graph="movies",
        )

    def test_builtins_are_skipped_unless_asked(self) -> None:
        outcomes, skipped = self._run(self._db(1), _client([_reply("q", GOOD)]))
        assert [o.query.key for o in outcomes] == ["q0"]
        assert outcomes[0].verified, outcomes[0].reason
        assert ("_queries/builtin", "visualizer built-in") in skipped

    def test_stops_calling_a_provider_that_keeps_failing(self) -> None:
        n = MAX_CONSECUTIVE_PROVIDER_FAILURES + 2
        client = _client([RuntimeError("401 Unauthorized")])
        outcomes, skipped = self._run(self._db(n), client)
        assert len(client.calls) == MAX_CONSECUTIVE_PROVIDER_FAILURES
        assert sum(1 for _, why in skipped if why == "not attempted: the LLM provider kept failing") == 2

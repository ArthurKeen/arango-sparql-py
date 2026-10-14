"""Query mining end to end on a real ArangoDB (PRD §7.7).

A database seeded like one a team has used: people who know each other, a
named graph over them, and the saved queries ArangoDB's own tools write — a
visualizer query (``_queries``), a canvas action (``_canvasActions``) that
runs on a selected node, a query-editor save (``_editor_saved_queries``),
and one saved query that writes. The real analyzer reads the schema, and the
real ``mine-examples`` command runs; only the LLM is replaced, by a client
that honours the real ``LLMClient`` contract and answers from what its
prompt says — including one wrong first draft, so the feedback loop runs
against real results.

Checks: each read query is verified by the result rule (the edge query by
its endpoints, the value query by its values, the canvas action from a
sampled start) — with half the people carrying a ``_uri``, so SPARQL binds
them by IRI while the saved AQL returns their ``_id``; the writing query is never executed; the examples land in
``arango_sparql_examples`` and ``list_examples`` serves them by graph; and
the examples collection stays out of the next schema analysis.

Run with::

    RUN_INTEGRATION=1 .venv/bin/pytest tests/integration/test_query_mining_live.py
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

import pytest
from typer.testing import CliRunner

from arango_sparql import cli
from arango_sparql.nl2sparql.models import LLMResponse
from arango_sparql.query_mining.store import DEFAULT_COLLECTION, list_examples
from arango_sparql.schema.acquire import acquire_mapping_bundle, analyzer_available
from tests.integration.conftest import (
    DEFAULT_ARANGO_PASSWORD,
    DEFAULT_ARANGO_URL,
    DEFAULT_ARANGO_USER,
    arangodb_reachable,
    integration_enabled,
    try_boot_arangodb_via_compose,
)

pytestmark = pytest.mark.integration

_DB = "sparql_mining_live"
_NAMES = ["Ada", "Bo", "Cy", "Di"]
_LLM_ENV = ("SCHEMA_ANALYZER_PROVIDER", "LLM_PROVIDER")  # keep the analyzer on its deterministic baseline


@pytest.fixture(scope="module")
def db() -> Iterator[Any]:
    if not integration_enabled():
        pytest.skip("set RUN_INTEGRATION=1 to enable integration tests")
    if not arangodb_reachable() and not try_boot_arangodb_via_compose():
        pytest.skip(f"ArangoDB at {DEFAULT_ARANGO_URL} is unreachable and could not be booted")
    if not analyzer_available():
        pytest.skip("mining reads the schema with arangodb-schema-analyzer")
    from arango import ArangoClient

    client = ArangoClient(hosts=DEFAULT_ARANGO_URL.rstrip("/"))
    sys_db = client.db("_system", username=DEFAULT_ARANGO_USER, password=DEFAULT_ARANGO_PASSWORD)
    if sys_db.has_database(_DB):
        sys_db.delete_database(_DB)
    sys_db.create_database(_DB)
    handle = client.db(_DB, username=DEFAULT_ARANGO_USER, password=DEFAULT_ARANGO_PASSWORD)
    people = handle.create_collection("people")
    # Two of the four carry a _uri, so SPARQL binds those two by IRI and the
    # other two by _id — the identity mapping must get both right.
    ids = [
        people.insert(
            {"name": n, **({"_uri": f"http://acme.example/people/{n}"} if n in ("Ada", "Cy") else {})}
        )["_id"]
        for n in _NAMES
    ]
    knows = handle.create_collection("knows", edge=True)
    for i in range(4):
        knows.insert({"_from": ids[i], "_to": ids[(i + 1) % 4]})
    handle.create_graph(
        "social",
        edge_definitions=[
            {
                "edge_collection": "knows",
                "from_vertex_collections": ["people"],
                "to_vertex_collections": ["people"],
            }
        ],
    )
    handle.create_collection("_queries", system=True).insert_many(
        [
            {
                "name": "Who knows whom",
                "description": "Every acquaintance in the network",
                "queryText": "FOR e IN knows RETURN e",
                "bindVariables": {},
                "graphId": "social",
            },
            {
                "name": "Mark everyone seen",
                "description": "Housekeeping",
                "queryText": "FOR p IN people UPDATE p WITH {seen: true} IN people",
                "bindVariables": {},
                "graphId": "social",
            },
        ]
    )
    handle.create_collection("_canvasActions", system=True).insert(
        {
            "name": "From a selected person: who they know",
            "description": "People the selected person knows",
            "queryText": "FOR n IN @nodes FOR v IN 1..1 OUTBOUND n knows RETURN v",
            "bindVariables": {"nodes": []},
            "graphId": "social",
        }
    )
    handle.create_collection("_editor_saved_queries", system=True).insert(
        {
            "title": "Everyone's name",
            "content": "FOR p IN people SORT p.name RETURN p.name",
            "bindVariables": "{}",
            "databaseName": _DB,
        }
    )
    try:
        yield handle
    finally:
        sys_db.delete_database(_DB, ignore_missing=True)
        client.close()


class _ReadingLLM:
    """An ``LLMClient`` (``provider``, ``model``, ``generate(messages) -> LLMResponse``)
    that drafts from what the prompt says, as a model would."""

    provider = "openai"
    model = "scripted-reader"

    def __init__(self, person: str, knows: str) -> None:
        self._person, self._knows = person, knows
        self.prompts: list[str] = []

    def generate(self, messages: list[dict[str, str]]) -> LLMResponse:
        system = next(m["content"] for m in messages if m["role"] == "system")
        user = next(m["content"] for m in messages if m["role"] == "user")
        self.prompts.append(user)
        ns = re.search(r"PREFIX : <([^>]+)>", system).group(1)  # type: ignore[union-attr]
        p, k = self._person, self._knows
        head = f"PREFIX : <{ns}>\n"
        if "Who knows whom" in user:
            if "rdf:type" not in user:  # first draft: untyped, refused by strict subject routing
                sparql = head + f"SELECT ?a ?b WHERE {{ ?a :{k} ?b }}"
            else:
                sparql = head + f"SELECT ?a ?b WHERE {{ ?a a :{p} ; :{k} ?b }}"
        elif "Everyone's name" in user:
            if "was rejected" not in user:  # first draft: one name short
                sparql = head + f"SELECT ?n WHERE {{ ?p a :{p} ; :name ?n }} ORDER BY ?n LIMIT 3"
            else:
                sparql = head + f"SELECT ?n WHERE {{ ?p a :{p} ; :name ?n }} ORDER BY ?n"
        elif "selected in the graph" in user:
            name = re.search(r'match it by: name = "([^"]+)"', user).group(1)  # type: ignore[union-attr]
            sparql = head + f'SELECT ?v WHERE {{ ?n a :{p} ; :name "{name}" ; :{k} ?v }}'
        else:
            sparql = head + "SELECT ?x WHERE { ?x ?y ?z } LIMIT 1"
        return LLMResponse(content=json.dumps({"question": "q: " + user.splitlines()[0], "sparql": sparql}))


def _terms(db: Any) -> tuple[str, str]:
    """The ontology names the analyzer gave ``people`` and ``knows``."""
    bundle = acquire_mapping_bundle(db, include_owl=True)
    person = next(
        n for n, s in bundle.physical_mapping["entities"].items() if s.get("collectionName") == "people"
    )
    knows = next(
        n
        for n, s in bundle.physical_mapping["relationships"].items()
        if s.get("edgeCollectionName") == "knows"
    )
    return person, knows


@pytest.fixture(scope="module")
def mined(db: Any) -> dict[str, Any]:
    """Run the real ``mine-examples`` once for the module."""
    llm = _ReadingLLM(*_terms(db))
    with pytest.MonkeyPatch.context() as mp:
        for name in _LLM_ENV:
            mp.delenv(name, raising=False)
        mp.setattr(cli, "_mining_client", lambda name, model: llm)
        result = CliRunner().invoke(
            cli.app,
            ["mine-examples", "--provider", "openai", "--db", _DB, "--graph", "social"],
            env={
                "ARANGO_URL": DEFAULT_ARANGO_URL,
                "ARANGO_USERNAME": DEFAULT_ARANGO_USER,
                "ARANGO_USER": DEFAULT_ARANGO_USER,
                "ARANGO_PASSWORD": DEFAULT_ARANGO_PASSWORD,
                "COLUMNS": "200",
            },
        )
    return {"result": result, "llm": llm}


def _stored(db: Any) -> dict[str, dict[str, Any]]:
    return {d["source"]["name"]: d for d in db.collection(DEFAULT_COLLECTION).all()}


def test_the_command_verifies_every_read_query(db: Any, mined: dict[str, Any]) -> None:
    result = mined["result"]
    assert result.exit_code == 0, result.output
    assert "3 of 4 verified" in result.output
    assert "3 saved" in result.output


def test_each_example_passed_by_the_right_rule(db: Any, mined: dict[str, Any]) -> None:
    stored = _stored(db)
    assert stored["Who knows whom"]["verification"]["verdict"] == "same_vertices"  # edges, by their endpoints
    assert stored["Who knows whom"]["verification"]["resources"] == 4
    assert stored["Who knows whom"]["verification"]["attempts"] == 2  # typed after the routing feedback
    assert stored["Everyone's name"]["verification"]["verdict"] == "same_values"
    assert stored["Everyone's name"]["verification"]["attempts"] == 2  # the short first draft was fed back
    canvas = stored["From a selected person: who they know"]
    assert canvas["verification"]["verdict"] == "identical"
    assert canvas["sparql"].count(":name") == 1  # matched the sampled start by its name


def test_the_rejected_drafts_were_told_why(mined: dict[str, Any]) -> None:
    retry = next(p for p in mined["llm"].prompts if "Everyone's name" in p and "was rejected" in p)
    assert "its results differ from the saved query's (values differ" in retry
    retry = next(p for p in mined["llm"].prompts if "Who knows whom" in p and "was rejected" in p)
    assert "the SPARQL does not translate: cannot route subject variable 'a'" in retry
    assert "Use only the classes" not in retry  # that hint is for undeclared names only


def test_a_writing_query_is_never_run(db: Any, mined: dict[str, Any]) -> None:
    assert "Mark everyone seen" not in _stored(db)
    assert all("seen" not in doc for doc in db.collection("people").all())
    assert "the query writes (UpdateNode)" in mined["result"].output


def test_the_service_lists_them_by_graph(db: Any, mined: dict[str, Any]) -> None:
    names = {e["source"]["name"] for e in list_examples(db, graph="social")}
    assert names == {"Who knows whom", "From a selected person: who they know", "Everyone's name"}
    # Editor saves carry no graph, so they show under any graph.
    assert {e["source"]["name"] for e in list_examples(db, graph="other")} == {"Everyone's name"}


def test_the_examples_collection_stays_out_of_the_schema(db: Any, mined: dict[str, Any]) -> None:
    assert db.has_collection(DEFAULT_COLLECTION)
    bundle = acquire_mapping_bundle(db, include_owl=True)
    collections = {s.get("collectionName") for s in bundle.physical_mapping["entities"].values()}
    assert DEFAULT_COLLECTION not in collections
    assert "Examples" not in json.dumps(bundle.conceptual_schema) + (bundle.owl_turtle or "")

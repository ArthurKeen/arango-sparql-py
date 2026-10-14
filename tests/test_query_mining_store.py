"""Query mining, slice 3: storing verified examples in the database.

Ported from arango-cypher-py's ``tests/test_query_mining_store.py`` (the CLI
cases live in ``tests/test_query_mining_cli.py``).
"""

from __future__ import annotations

from typing import Any

import pytest

from arango_sparql.query_mining.harvest import SAVED_QUERIES, SavedQuery
from arango_sparql.query_mining.miner import MinedExample, Outcome
from arango_sparql.query_mining.signature import Verdict, reference_of, signature_of
from arango_sparql.query_mining.store import (
    DEFAULT_COLLECTION,
    SISTER_COLLECTION,
    example_collections,
    example_key,
    list_examples,
    save_outcomes,
    to_document,
)
from tests.helpers.fake_arango import FakeDb


def _query(key: str, name: str = "Q13 Circular trust") -> SavedQuery:
    return SavedQuery(
        source=SAVED_QUERIES,
        key=key,
        name=name,
        description="3-cycles of CAN_ASSUME",
        aql="FOR e IN CAN_ASSUME RETURN e",
        bind_vars={},
        graph="IAM_DEMO",
    )


def _verified(key: str, question: str = "Which IAM roles trust each other in a cycle?") -> Outcome:
    q = _query(key)
    sig = signature_of([{"_id": "CAN_ASSUME/1", "_from": "aws_iam_role/1", "_to": "aws_iam_role/2"}])
    example = MinedExample(
        question=question,
        sparql="PREFIX : <http://ex.org/iam#>\nSELECT ?a ?b WHERE { ?a :CAN_ASSUME ?b }",
        aql="FOR doc1 IN @@_c1 ...",
        bind_vars={},
        verdict=Verdict("same_vertices"),
        source=q,
        source_signature=sig,
        reference=reference_of(sig, {}),
        attempts=1,
    )
    return Outcome(q, example)


class TestStore:
    def test_the_document_records_provenance_and_what_was_verified(self) -> None:
        outcome = _verified("q13")
        assert outcome.example is not None
        doc = to_document(outcome.example, mapping_hash="abc123", model="openai:gpt-4.1")
        assert doc["_key"] == example_key(SAVED_QUERIES, "q13")
        assert doc["kind"] == "mined"
        assert doc["sparql"].startswith("PREFIX : <http://ex.org/iam#>")
        assert "cypher" not in doc and "params" not in doc
        assert doc["graph"] == "IAM_DEMO"
        assert doc["source"]["name"] == "Q13 Circular trust"
        assert doc["source"]["fingerprint"] == outcome.query.fingerprint
        assert doc["verification"]["verdict"] == "same_vertices"
        assert doc["verification"]["mapping_hash"] == "abc123"
        assert doc["verification"]["model"] == "openai:gpt-4.1"
        assert doc["verification"]["vertices"] == 2 and doc["verification"]["edges"] == 1
        assert doc["verification"]["resources"] == 2

    def test_creates_the_collection_and_saves_verified_examples(self) -> None:
        db = FakeDb({})
        counts = save_outcomes(db, [_verified("q1"), _verified("q2")], mapping_hash="h", model="m")
        assert counts == {"saved": 2, "removed": 0, "kept": 0}
        assert db.created == [DEFAULT_COLLECTION]
        assert len(db.collection(DEFAULT_COLLECTION).all()) == 2

    def test_re_mining_replaces_in_place(self) -> None:
        db = FakeDb({})
        save_outcomes(db, [_verified("q1", "old wording")], mapping_hash="h", model="m")
        save_outcomes(db, [_verified("q1", "new wording")], mapping_hash="h2", model="m")
        docs = db.collection(DEFAULT_COLLECTION).all()
        assert [d["question"] for d in docs] == ["new wording"]
        assert docs[0]["verification"]["mapping_hash"] == "h2"

    def test_a_query_that_no_longer_verifies_loses_its_example(self) -> None:
        db = FakeDb({})
        save_outcomes(db, [_verified("q1")], mapping_hash="h", model="m")
        rejected = Outcome(_query("q1"), reason="its results differ")
        counts = save_outcomes(db, [rejected], mapping_hash="h", model="m")
        assert counts == {"saved": 0, "removed": 1, "kept": 0}
        assert db.collection(DEFAULT_COLLECTION).all() == []

    def test_a_provider_failure_keeps_the_existing_example(self) -> None:
        db = FakeDb({})
        save_outcomes(db, [_verified("q1")], mapping_hash="h", model="m")
        unjudged = Outcome(_query("q1"), reason="LLM provider failed", provider_failed=True)
        counts = save_outcomes(db, [unjudged], mapping_hash="h", model="m")
        assert counts == {"saved": 0, "removed": 0, "kept": 1}
        assert len(db.collection(DEFAULT_COLLECTION).all()) == 1

    def test_the_collection_name_is_configurable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARANGO_SPARQL_EXAMPLES_COLLECTION", "my_examples")
        db = FakeDb({})
        save_outcomes(db, [_verified("q1")], mapping_hash="h", model="m")
        assert db.created == ["my_examples"]
        # ...and the configured name is the one schema analysis leaves out.
        assert example_collections() == {"my_examples", SISTER_COLLECTION}

    def test_listing_is_empty_without_the_collection(self) -> None:
        assert list_examples(FakeDb({})) == []

    def test_listing_scopes_by_graph_and_caps_the_limit(self) -> None:
        seen: list[tuple[str, dict[str, Any]]] = []

        def handler(q: str, b: dict[str, Any]) -> list[Any]:
            seen.append((q, b))
            return [{"question": "q"}]

        db = FakeDb({DEFAULT_COLLECTION: []}, handler=handler)
        assert list_examples(db, graph="IAM_DEMO", limit=10_000) == [{"question": "q"}]
        ((query, bind),) = seen
        assert bind == {"@c": DEFAULT_COLLECTION, "scope": "IAM_DEMO", "limit": 200}
        assert 'e["graph"]' in query and "e.graph" not in query  # GRAPH is an AQL keyword

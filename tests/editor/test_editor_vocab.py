"""Editor vocabulary v1 and this library's producer (issue #68).

The editor's completions and the NL→SPARQL prompt's grounding come from one
per-class derivation (``arango_sparql.schema.vocabulary``), so the human and
the model never disagree about which properties a class owns.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from arango_sparql.editor import EditorVocabV1, editor_vocab
from arango_sparql.nl2sparql.schema_summary import build_schema_summary

from .core_runner import call

NS = "http://arangodb.com/schema/hybrid#"


def _bundle():
    """Shaped like the analyzer's export (conceptualSchema / physicalMapping)."""
    cs = {
        "entities": [
            {
                "name": "Document",
                "properties": [
                    {"name": "file_name", "type": "string"},
                    {"name": "content", "type": "string"},
                    {"name": "content", "type": "string"},  # listed twice — once in the vocabulary
                    {"name": "first name", "type": "string"},  # not a PN_LOCAL — omitted
                ],
            },
            {"name": "Chunk", "properties": [{"name": f"p{i}", "type": "string"} for i in range(12)]},
            {"name": "Empty", "properties": []},
            {"name": "bad name", "properties": [{"name": "x"}]},  # not a PN_LOCAL — omitted
        ],
        "relationships": [
            {"type": "PART_OF", "fromEntity": "Chunk", "toEntity": "Document"},
            {"type": "LINKS", "fromEntity": "Any", "toEntity": "Any"},
            {"type": "bad rel", "fromEntity": "Chunk", "toEntity": "Document"},
        ],
    }
    pm = {
        "entities": {
            "Document": {"style": "COLLECTION", "collectionName": "AWS_Docs", "properties": {}},
            "Empty": {
                "style": "COLLECTION",
                "collectionName": "Empties",
                "properties": {"x": {"field": "x"}},
            },
        }
    }
    return SimpleNamespace(conceptual_schema=cs, physical_mapping=pm, owl_turtle=None)


def test_every_property_is_listed_uncapped_with_object_properties_on_their_domain() -> None:
    vocab = editor_vocab(_bundle(), namespace=NS, source="arangodb:IAM")
    assert vocab.base == NS
    assert list(vocab.classes) == ["Chunk", "Document", "Empty"]
    assert vocab.classes["Chunk"].props == [f"p{i}" for i in range(12)] + ["LINKS", "PART_OF"]
    assert vocab.classes["Document"].props == ["file_name", "content", "LINKS"]
    assert vocab.classes["Empty"].props == ["x", "LINKS"]  # falls back to the physical keys
    assert {(c.source, c.kind) for c in vocab.classes.values()} == {("arangodb:IAM", "arangodb")}


def test_no_physical_names_leak() -> None:
    dumped = editor_vocab(_bundle(), namespace=NS, source="arangodb:IAM").model_dump_json()
    assert "AWS_Docs" not in dumped and "COLLECTION" not in dumped and "phys:" not in dumped


def test_everything_the_prompt_lists_the_editor_offers_on_the_same_class() -> None:
    """The point of one derivation: a property the model is told belongs to a
    class is a property the editor offers on that class."""
    bundle = _bundle()
    vocab = editor_vocab(bundle, namespace=NS, source="arangodb:IAM")
    prompt_classes = {}
    for line in build_schema_summary(bundle, namespace=NS).splitlines():
        m = re.match(r"^  :(\w+) — (.*)$", line)
        if m:
            listed = [] if m.group(2) == "no properties" else [t.lstrip(":") for t in m.group(2).split(", ")]
            prompt_classes[m.group(1)] = listed
    assert set(prompt_classes) == set(vocab.classes)
    for cls, props in prompt_classes.items():
        assert set(props) <= set(vocab.classes[cls].props), cls


def test_every_relationship_the_prompt_lists_the_editor_offers_on_its_domain() -> None:
    bundle = _bundle()
    bundle.conceptual_schema["relationships"].append(
        {"type": "CITES", "fromEntity": "Ghost", "toEntity": "Document"}
    )
    vocab = editor_vocab(bundle, namespace=NS, source="arangodb:IAM")
    rels = [
        (m.group(1), m.group(2))
        for line in build_schema_summary(bundle, namespace=NS).splitlines()
        if (m := re.match(r"^  \?a :(\S+) \?b(?:   \(\?a a (:\w+|any class))?", line))
    ]
    assert ("CITES", ":Ghost") in rels and ("LINKS", None) in rels
    for name, domain in rels:
        owners = [c for c, info in vocab.classes.items() if name in info.props]
        if domain and domain != "any class":
            assert domain.lstrip(":") in owners, name  # a domain with no entity still gets it
        else:
            assert set(owners) == set(vocab.classes), name


def test_classes_stay_sorted_when_a_domain_has_no_entity() -> None:
    bundle = _bundle()
    bundle.conceptual_schema["relationships"].append(
        {"type": "CITES", "fromEntity": "Alpha", "toEntity": "Document"}
    )
    vocab = editor_vocab(bundle, namespace=NS, source="arangodb:IAM")
    assert list(vocab.classes) == ["Alpha", "Chunk", "Document", "Empty"]
    assert vocab.classes["Alpha"].props == ["CITES", "LINKS"]  # LINKS has an Any domain


def test_an_empty_namespace_is_refused() -> None:
    with pytest.raises(ValueError, match="default namespace"):
        editor_vocab(_bundle(), namespace="", source="arangodb:IAM")


def test_the_model_accepts_the_fabrics_payload() -> None:
    """The example in CDF's M9 spec §3.1, which CDF's own producer emits."""
    payload = {
        "base": "urn:arango-sparql:concept#",
        "classes": {
            "Account": {
                "props": ["accountId", "accountName"],
                "source": "postgresql:crm",
                "kind": "postgresql",
            }
        },
    }
    assert EditorVocabV1.model_validate(payload).model_dump() == payload


@pytest.mark.parametrize(
    "payload",
    [
        {"base": "", "classes": {}},
        {"base": NS, "classes": {"A": {"props": [], "source": "s"}}},
        {"base": NS, "classes": {"A": {"props": [], "source": "s", "kind": "k", "extra": 1}}},
        {"base": NS, "classes": {}, "version": 2},
        {"base": "http://x/> ;", "classes": {}},
        {"base": NS, "classes": {"two words": {"props": [], "source": "s", "kind": "k"}}},
        {"base": NS, "classes": {"A": {"props": ["dotted.name"], "source": "s", "kind": "k"}}},
    ],
    ids=[
        "empty-base",
        "missing-kind",
        "extra-class-field",
        "extra-top-field",
        "base-breaks-out-of-angle-brackets",
        "class-name-not-writable",
        "property-name-not-writable",
    ],
)
def test_the_model_rejects_other_shapes(payload: dict) -> None:
    with pytest.raises(ValidationError):
        EditorVocabV1.model_validate(payload)


def test_the_core_offers_what_the_producer_emits() -> None:
    """End to end: the producer's payload drives the core's completion."""
    vocab = editor_vocab(_bundle(), namespace=NS, source="arangodb:IAM").model_dump()
    doc = "SELECT * WHERE { ?c a :Chunk ; :PA"
    result = call("suggest", doc, len(doc), vocab, {"conceptPrefixes": [""]})
    assert [i["insert"] for i in result["items"]] == [":PART_OF"]


def test_any_domain_relationships_reach_an_orphan_domain_whatever_their_names() -> None:
    for any_name in ("AAA_any", "ZZZ_any"):
        bundle = _bundle()
        bundle.conceptual_schema["relationships"] += [
            {"type": "M_orphan", "fromEntity": "Ghost", "toEntity": "Document"},
            {"type": any_name, "fromEntity": "Any", "toEntity": "Any"},
        ]
        vocab = editor_vocab(bundle, namespace=NS, source="arangodb:IAM")
        assert any_name in vocab.classes["Ghost"].props, any_name


def test_a_name_ending_in_a_newline_is_left_out_not_fatal() -> None:
    bundle = _bundle()
    bundle.conceptual_schema["entities"][0]["properties"].append({"name": "q\n", "type": "string"})
    vocab = editor_vocab(bundle, namespace=NS, source="arangodb:IAM")
    assert "q\n" not in vocab.classes["Document"].props and "q" not in vocab.classes["Document"].props

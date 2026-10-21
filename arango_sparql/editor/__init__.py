"""Shared pieces of the syntax-directed SPARQL editors (issue #68).

Two editors run the same suggestion, tokenizing and formatting rules: this
repository's CodeMirror workbench and the contextual-data-fabric demo's
textarea editor. The rules live in one plain-JavaScript file shipped in this
package, ``sparql_editor_core.js``; each editor keeps only its own shell
(painting, menu, bracket pairing, keys).

* :func:`editor_core_js` — that file's source, for a page that inlines it as a
  classic ``<script>`` (it defines ``globalThis.SparqlEditorCore``).
* :class:`EditorVocabV1` — **editor vocabulary v1**, the one JSON payload the
  core is generic over. A producer outside this library (the fabric builds one
  from every source it federates) validates against this model.
* :func:`editor_vocab` — this library's producer, from a mapping bundle, built
  on the same per-class derivation as the NL→SPARQL prompt's schema summary
  (:mod:`arango_sparql.schema.vocabulary`), so the human's completions and the
  model's grounding never disagree about which properties a class owns.

Changing the payload's shape means a new contract name (``…/v2``), here, in
the core's ``VOCAB_CONTRACT``, and in the fabric's M9 spec §3.1.
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..schema.vocabulary import concept_classes, concept_relationships

__all__ = [
    "ARANGODB_KIND",
    "CORE_JS_RESOURCE",
    "EDITOR_VOCAB_CONTRACT",
    "EditorClassV1",
    "EditorVocabV1",
    "editor_core_js",
    "editor_vocab",
]

#: The contract name; the core exposes the same string as ``VOCAB_CONTRACT``.
EDITOR_VOCAB_CONTRACT = "editor-vocabulary/v1"

#: The source kind this library's producer stamps on every class.
ARANGODB_KIND = "arangodb"

#: The core's file name inside this package.
CORE_JS_RESOURCE = "sparql_editor_core.js"


class EditorClassV1(BaseModel):
    """One class: its properties and the source that owns it."""

    model_config = ConfigDict(extra="forbid")

    props: list[str] = Field(description="Property local names, data and object properties alike.")
    source: str = Field(description="The owning source, e.g. 'postgresql:crm' or 'arangodb:IAM'.")
    kind: str = Field(description="The owning source's kind, e.g. 'postgresql' or 'arangodb'.")


class EditorVocabV1(BaseModel):
    """Editor vocabulary v1: ``{"base": …, "classes": {Name: {props, source, kind}}}``."""

    model_config = ConfigDict(extra="forbid")

    base: str = Field(min_length=1, description="The concept namespace IRI that local names extend.")
    classes: dict[str, EditorClassV1]


def editor_vocab(bundle: Any, *, namespace: str, source: str) -> EditorVocabV1:
    """The editor vocabulary for *bundle*, in the ontology's *namespace*.

    Each class lists every data property (uncapped, unlike the prompt), then
    every object property whose domain is that class or any class. *source*
    names the database the bundle describes, e.g. ``"arangodb:IAM"``.
    """
    if not namespace:
        raise ValueError("editor_vocab needs the ontology's default namespace; got an empty one")
    classes = concept_classes(bundle)
    for rel in concept_relationships(bundle):
        for cls in [rel.domain] if rel.domain else list(classes):
            props = classes.get(cls)
            if props is not None and rel.name not in props:
                props.append(rel.name)
    return EditorVocabV1(
        base=namespace,
        classes={
            name: EditorClassV1(props=props, source=source, kind=ARANGODB_KIND)
            for name, props in classes.items()
        },
    )


@cache
def editor_core_js() -> str:
    """The shared editor core's JavaScript source (read once per process)."""
    return files(__package__).joinpath(CORE_JS_RESOURCE).read_text(encoding="utf-8")

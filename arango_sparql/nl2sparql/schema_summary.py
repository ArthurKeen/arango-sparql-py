"""Schema summary for the NL→SPARQL prompt, built from the analyzer's mapping.

The prompt used to carry only the OWL ontology, and until
arangodb-schema-analyzer 0.15.1 its OWL export declared classes and object
properties but **no datatype properties** — so the model never saw a single
field name. On prod.demo ``IAM`` the analyzer found 1,576 fields across 113
types, yet the model had to guess, and reached for the ``phys:typeValue``
storage annotation (2026-10-08). From 0.15.1 the OWL declares every field
(``owl:DatatypeProperty``); this summary stays the compact per-class index the
prompt leads with (PRD §7.2).

Ported from arango-cypher-py's ``nl2cypher._core._build_schema_summary``,
which never relies on OWL for its NL context: every type with up to
``MAX_PROPS`` of its properties, then every relationship with its domain and
range. Same rule as cypher: only *conceptual* names — no collection names,
mapping styles or ``phys:`` annotations; the translator owns the physical
mapping. Terms are written as SPARQL prefixed names in the ontology's default
(``:``) namespace — the form the model must emit. An undeclared property such
as ``:file_name`` resolves through the translator's local-name fallback to the
document attribute ``file_name`` (PRD §6.7).
"""

from __future__ import annotations

from typing import Any

# The per-class derivation is shared with the editors' vocabulary
# (arango_sparql.editor.editor_vocab), so the prompt and the editor's
# completion read one list. ``default_namespace`` is re-exported here, where
# callers have always imported it from.
from ..schema.vocabulary import concept_classes, concept_relationships, default_namespace

__all__ = ["MAX_PROPS", "build_schema_summary", "default_namespace"]

#: Properties listed per class, as in the sister project.
MAX_PROPS = 8

_HEADER = (
    "Classes and their data properties. Write every term with the ontology's "
    "default prefix `{prefix}` (declare `PREFIX : <{namespace}>`), e.g. "
    "`?x a :SomeClass ; :some_property ?value`. A property listed here can be "
    "used even if the Turtle above does not declare it."
)


def build_schema_summary(bundle: Any, *, namespace: str) -> str:
    """Render the analyzer bundle as a prompt section for SPARQL generation.

    *bundle* is a :class:`~arango_sparql.translate.mapping.MappingBundle` (only
    its ``conceptual_schema`` / ``physical_mapping`` dicts are read). Returns an
    empty string when the bundle carries no entities, so callers can pass the
    result straight to the prompt builder. Deterministic for a given bundle —
    classes and relationships are sorted — so the system prompt stays
    cache-stable across requests.
    """
    class_lines: list[str] = []
    for cls, props in concept_classes(bundle).items():
        terms = [f":{p}" for p in props][:MAX_PROPS]
        listed = ", ".join(terms) if terms else "no properties"
        class_lines.append(f"  :{cls} — {listed}")
    if not class_lines:
        return ""

    rel_lines: list[str] = []
    for rel in concept_relationships(bundle):
        line = f"  ?a :{rel.name} ?b"
        if rel.domain or rel.range:
            domain = f":{rel.domain}" if rel.domain else "any class"
            range_ = f":{rel.range}" if rel.range else "any class"
            line += f"   (?a a {domain} ; ?b a {range_})"
        rel_lines.append(line)

    lines = [_HEADER.format(prefix=":", namespace=namespace), *class_lines]
    if rel_lines:
        lines += ["Relationships (object properties between instances):", *rel_lines]
    return "\n".join(lines)

"""The conceptual vocabulary of a mapping bundle: classes, their properties,
and the relationships between them.

One derivation, two readers, so they cannot disagree about which properties a
class owns (a property attached to the wrong class silently returns an empty
answer):

* the NL→SPARQL prompt's schema summary
  (:func:`arango_sparql.nl2sparql.schema_summary.build_schema_summary`), which
  caps the properties it lists per class;
* the syntax-directed editors' vocabulary
  (:func:`arango_sparql.editor.editor_vocab`), which lists them all.

Only *conceptual* names leave this module: no collection names, mapping styles
or ``phys:`` annotations (PRD §17.4). A name that cannot be written as a plain
SPARQL prefixed name is left out, rather than shown in a form a reader would
copy into an invalid query.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: A safe subset of SPARQL's PN_LOCAL: the names this module lets out. The
#: editor core (``LOCAL`` in ``arango_sparql/editor/sparql_editor_core.js``)
#: and ``EditorVocabV1`` use the same character set.
PREFIXED_NAME_LOCAL = r"^[A-Za-z_][A-Za-z0-9_\-]*$"
_PN_LOCAL_SAFE = re.compile(PREFIXED_NAME_LOCAL)

_DEFAULT_PREFIX = re.compile(r"@prefix\s+:\s*<([^>]+)>", re.IGNORECASE)
_SPARQL_DEFAULT_PREFIX = re.compile(r"PREFIX\s+:\s*<([^>]+)>", re.IGNORECASE)

#: The analyzer's spelling of "no domain / range constraint".
ANY_CLASS = "Any"


@dataclass(frozen=True)
class ConceptRelationship:
    """An object property; ``domain`` / ``range`` are ``None`` for any class."""

    name: str
    domain: str | None
    range: str | None


def is_prefixed_name_local(name: str) -> bool:
    """True when *name* can be written as ``:name``."""
    return bool(name) and bool(_PN_LOCAL_SAFE.match(name))


def default_namespace(*turtles: str | None) -> str | None:
    """The ontology's default (``:``) namespace, from the first Turtle that
    declares one; ``None`` when none does."""
    for ttl in turtles:
        if not ttl:
            continue
        match = _DEFAULT_PREFIX.search(ttl) or _SPARQL_DEFAULT_PREFIX.search(ttl)
        if match:
            return match.group(1)
    return None


def _entity_props(entity: dict[str, Any], physical: dict[str, Any]) -> list[str]:
    props = [p.get("name", "") for p in entity.get("properties") or [] if isinstance(p, dict)]
    if not props:
        pme = (physical.get("entities") or {}).get(entity.get("name", ""))
        if isinstance(pme, dict) and isinstance(pme.get("properties"), dict):
            props = list(pme["properties"].keys())
    return props


def concept_classes(bundle: Any) -> dict[str, list[str]]:
    """Each class (sorted by name) with its data properties, uncapped, in the
    analyzer's order.

    *bundle* is a :class:`~arango_sparql.translate.mapping.MappingBundle` (only
    its ``conceptual_schema`` / ``physical_mapping`` dicts are read). A class
    the conceptual schema lists without properties falls back to its physical
    mapping's property keys.
    """
    cs = getattr(bundle, "conceptual_schema", None) or {}
    pm = getattr(bundle, "physical_mapping", None) or {}
    out: dict[str, list[str]] = {}
    for entity in sorted(
        (e for e in cs.get("entities") or [] if isinstance(e, dict)),
        key=lambda e: str(e.get("name", "")),
    ):
        name = str(entity.get("name", ""))
        if not is_prefixed_name_local(name):
            continue
        props = out.setdefault(name, [])
        for prop in _entity_props(entity, pm):
            if is_prefixed_name_local(prop) and prop not in props:
                props.append(prop)
    return out


def concept_relationships(bundle: Any) -> list[ConceptRelationship]:
    """The bundle's object properties, sorted by (name, domain, range)."""
    cs = getattr(bundle, "conceptual_schema", None) or {}
    out: list[ConceptRelationship] = []
    for rel in sorted(
        (r for r in cs.get("relationships") or [] if isinstance(r, dict)),
        key=lambda r: (str(r.get("type", "")), str(r.get("fromEntity", "")), str(r.get("toEntity", ""))),
    ):
        name = str(rel.get("type", ""))
        if not is_prefixed_name_local(name):
            continue
        out.append(
            ConceptRelationship(name, _endpoint(rel.get("fromEntity")), _endpoint(rel.get("toEntity")))
        )
    return out


def _endpoint(value: Any) -> str | None:
    name = str(value or "")
    return name if name != ANY_CLASS and is_prefixed_name_local(name) else None

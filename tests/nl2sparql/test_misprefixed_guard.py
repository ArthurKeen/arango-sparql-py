"""NL validation rejects a mis-prefixed name so the repair loop can fix it.

Regression (prod.demo IAM, 2026-10-08): for "What are the different types of
AWS security documents available?" the LLM wrote ``?d :phys:typeValue ?t`` —
the ontology's ``phys:typeValue`` *mapping annotation* pasted after the ``:``
prefix. It translated (local-name fallback), so validation accepted it and the
user got a broken query instead of a repair.

Undeclared plain names must keep working: analyzer ontologies declare no
datatype properties at all, so NL relies on the local-name fallback for every
field (``:file_name``).
"""

from __future__ import annotations

from arango_sparql.nl2sparql.engine_adapter import E_NL_MISPREFIXED_TERM, SparqlAdapter
from arango_sparql.nl2sparql.prompt import PromptBuilder
from arango_sparql.translate.resolver import SchemaResolver

_TTL = """
@prefix :     <http://example.org/> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .

:Doc a owl:Class ;
  phys:mappingStyle "COLLECTION" ;
  phys:collectionName "Docs" .
"""


def _adapter() -> SparqlAdapter:
    return SparqlAdapter(resolver=SchemaResolver.from_turtle(_TTL), ontology_ttl=_TTL)


def test_a_mis_prefixed_annotation_is_rejected_with_a_repairable_message() -> None:
    adapter = _adapter()
    q = "PREFIX : <http://example.org/>\nSELECT DISTINCT ?t WHERE { ?d a :Doc . ?d :phys:typeValue ?t . }"
    result = adapter.validate(q)
    assert result.ok is False
    assert result.code == E_NL_MISPREFIXED_TERM
    assert "phys:typeValue" in result.error
    assert "storage annotations" in result.error
    # The repair loop feeds this to the model, tagged with the code.
    hint = adapter.repair_hint(q, result)
    assert hint.startswith(f"[{E_NL_MISPREFIXED_TERM}]")


def test_an_undeclared_plain_attribute_still_validates() -> None:
    q = "PREFIX : <http://example.org/>\nSELECT ?n WHERE { ?d a :Doc . ?d :file_name ?n . } LIMIT 5"
    assert _adapter().validate(q).ok is True


def test_the_prompt_says_phys_terms_are_not_data() -> None:
    system = PromptBuilder(ontology_ttl=_TTL).render_system()
    assert "`phys:` namespace" in system
    assert "never use them as predicates" in system

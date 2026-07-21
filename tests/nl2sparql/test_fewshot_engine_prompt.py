"""SC2 gate: retrieved few-shot examples land in the ENGINE-built prompt.

Phase 7 Plan 03 flips ``SparqlAdapter.few_shot_index()`` from ``None`` to a
populated :class:`~arango_query_core.nl.fewshot.FewShotIndex`. Success
Criterion 2 requires that retrieved examples appear in
:meth:`~arango_query_core.nl.engine.NLQueryEngine._system_prompt`'s
``## Examples`` section — the engine-built prompt — and NOT in the standalone
:class:`~arango_sparql.nl2sparql.prompt.PromptBuilder` path
(``SparqlAdapter.grammar_prompt_section`` -> ``PromptBuilder.render_system``),
which stays zero-shot / example-free forever (that path is dead code since
Phase 06.1 re-pointed the pipeline onto the shared engine).

Key-free / no-network / no-torch: builds the index with ``mode="bm25"``
(``rank_bm25`` is a pure-Python, already-pinned dependency) — the dense path
itself is exercised by ``arango-query-core``'s own unit tests (07-01), not
here. ``_system_prompt`` never fires an LLM completion, so a bare
:class:`EngineProviderBridge` around a :class:`ScriptedLLMClient` is enough to
satisfy the ``LLMProvider`` protocol without ever calling it.
"""

from __future__ import annotations

from arango_query_core.nl import FewShotIndex
from arango_query_core.nl.engine import NLQueryEngine

from arango_sparql.nl2sparql.client import ScriptedLLMClient
from arango_sparql.nl2sparql.engine_adapter import EngineProviderBridge, SparqlAdapter
from arango_sparql.nl2sparql.models import LLMResponse
from arango_sparql.translate.resolver import SchemaResolver

ONTOLOGY = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

:Person a owl:Class ; phys:collectionName "Person" .
:name a owl:DatatypeProperty ; rdfs:domain :Person ;
    rdfs:range <http://www.w3.org/2001/XMLSchema#string> .
""".strip()

_SENTINEL = "SENTINEL_FEWSHOT_Q_a1b2c3"

_CORPUS_BODY = f"""
version: 1
examples:
  - question: "{_SENTINEL} list every person and their name"
    query: 'PREFIX : <http://ex.org/> SELECT ?s ?n WHERE {{ ?s a :Person ; :name ?n . }}'
  - question: "count all widgets shipped last quarter"
    query: 'PREFIX : <http://ex.org/> SELECT (COUNT(?w) AS ?c) WHERE {{ ?w a :Widget . }}'
  - question: "total revenue per customer segment"
    query: 'PREFIX : <http://ex.org/> SELECT ?seg (SUM(?amt) AS ?total) WHERE {{ ?o :segment ?seg ; :amount ?amt . }}'
"""


def _build_index(tmp_path) -> FewShotIndex:
    corpus_path = tmp_path / "fewshot_sentinel_bank.yml"
    corpus_path.write_text(_CORPUS_BODY, encoding="utf-8")
    return FewShotIndex.from_corpus_files([corpus_path], mode="bm25")


def test_examples_render_in_engine_built_prompt_not_standalone_builder(tmp_path) -> None:
    index = _build_index(tmp_path)
    adapter = SparqlAdapter(
        resolver=SchemaResolver.from_turtle(ONTOLOGY),
        ontology_ttl=ONTOLOGY,
        few_shot_index=index,
    )
    bridge = EngineProviderBridge(ScriptedLLMClient([LLMResponse(content="unused")], latency_ms=0))
    engine = NLQueryEngine(provider=bridge, adapter=adapter, few_shot_k=3, max_retries=0)

    # The engine's own render path — no LLM completion is fired by this call.
    system_prompt = engine._system_prompt("List all people along with their names.", "")

    assert "## Examples" in system_prompt
    assert _SENTINEL in system_prompt

    # SC2: the standalone PromptBuilder path (grammar_prompt_section) must
    # stay example-free — examples never route through it.
    standalone_prompt = adapter.grammar_prompt_section("")
    assert _SENTINEL not in standalone_prompt
    assert "## Examples" not in standalone_prompt

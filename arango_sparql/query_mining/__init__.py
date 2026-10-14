"""Mine a database's saved AQL queries for verified NL → SPARQL examples.

Ported from arango-cypher-py's ``arango_cypher/query_mining`` (PRD §7.7).

ArangoDB databases often carry hand-written AQL with descriptive text: the
graph visualizer's saved queries (``_queries``) and canvas actions
(``_canvasActions``), and the query editor's saved queries
(``_editor_saved_queries``). Those are the analyses people actually run on the
data, so they make far better examples than schema-template questions.

The pipeline, one saved query at a time:

1. :mod:`.harvest` — read the saved queries; refuse anything whose plan writes.
2. :mod:`.binding` — complete the bind variables (canvas actions run on
   selected nodes, so start vertices are sampled from the graph) and run the
   source AQL read-only under time and row caps.
3. :mod:`.generate` — an LLM writes an NL question and SPARQL over the
   ontology, with the source AQL as the reference meaning.
4. :mod:`.miner` — the SPARQL is transpiled and executed, and its results must
   match the source's (:mod:`.signature`: the same resources, by the identity
   a SPARQL result binds). Failures retry with the error fed back.
5. :mod:`.store` — verified examples are written to a collection in the same
   database, where the service reads them without needing an LLM key.
"""

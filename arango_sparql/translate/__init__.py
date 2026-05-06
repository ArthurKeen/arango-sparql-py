"""SPARQL → AQL translation pipeline.

Submodules:

- :mod:`.parser`   — wraps ``rdflib.plugins.sparql`` parser + algebra.
- :mod:`.visitor`  — Algebra visitor; one ``visit_<NodeType>`` per node.
- :mod:`.builder`  — parameterized AQL query builder (bind-vars only).
- :mod:`.resolver` — OWL/Turtle URI → ArangoDB collection resolver.
"""

from __future__ import annotations

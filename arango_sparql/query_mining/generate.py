"""Ask an LLM for an NL question and SPARQL equivalent to a saved AQL query.

Ported from arango-cypher-py's ``arango_cypher/query_mining/generate.py``.

The NL → SPARQL prompt shows the model only the conceptual schema (PRD §7.2).
Mining cannot: its input *is* physical AQL. So this prompt — offline,
build-time, never on the user path — also carries the physical → conceptual
name table (collection ``users`` is class ``:User``; edge collection
``follows`` is property ``:FOLLOWS``), and the model must answer in ontology
terms only; the translator, not the model, maps them back.

Differences from cypher's prompt, because SPARQL is not Cypher: terms are
prefixed names in the ontology's default ``:`` namespace (declared with
``PREFIX``, as in the NL prompt's schema summary — ``nl2sparql/schema_summary.py``);
values the AQL binds are written inline as SPARQL literals (the translator
turns every literal into a bind variable), so a draft carries no separate
parameters; and only SELECT queries are asked for, since only bindings can be
compared with the saved query's results.

The reply is one JSON object::

    {"question": "...", "sparql": "..."}
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..translate.mapping import MappingBundle
from .harvest import SavedQuery

if TYPE_CHECKING:
    # Type-only: nl2sparql's package import loads the service app (models.py).
    from ..nl2sparql.client import LLMClient

#: Bind values longer than this are elided from the prompt.
_MAX_PROMPT_VALUE = 300

SYSTEM_PROMPT = """You translate an ArangoDB AQL query into an equivalent SPARQL 1.1 SELECT query over an
ontology, and write the plain-English question an analyst would ask to get its answer.

{schema}

Physical names in the AQL and what they are in the ontology:
{names}

Rules:
- The SPARQL must return the same resources as the AQL: the same start resources, the same
  filters, the same traversal direction and depth, the same LIMIT. Bind every resource the AQL
  returns (a path's vertices, an edge's endpoints, a document) to a variable in the SELECT.
- Begin with `PREFIX : <{namespace}>` and write every class and property as `:Name` exactly as
  listed; never a collection name.
- Write the AQL's bind values inline as SPARQL literals. A start node given as a document id is
  matched by its properties shown in the request, not by its id.
- SPARQL subset this engine translates: SELECT only (no CONSTRUCT, ASK or DESCRIBE); no SERVICE;
  no FROM or GRAPH clauses. A relationship followed 1..n times is a property path (`:P+`, `:P/:P`).
- The question is how a domain analyst would ask for this answer: lead with the intent (the saved
  query's description says what it is for), in one plain sentence. Name concrete values a user would
  type (a role or bucket name, a tag) when the query filters on them. Leave housekeeping filters
  implicit — tenant or org ids, soft-delete flags, status codes — although the SPARQL keeps them.
  Never mention AQL, SPARQL, collections, bind parameters, or how to return the result.
- Reply with one JSON object and nothing else:
  {{"question": "...", "sparql": "..."}}"""


class GenerationError(Exception):
    """The model's reply was not a usable draft; the message is retry feedback."""


class ProviderError(Exception):
    """The LLM call itself failed (auth, quota, network) — not something a retry
    with feedback can fix. Never carries the request or any credential."""


@dataclass(frozen=True)
class Draft:
    question: str
    sparql: str


def physical_name_table(bundle: MappingBundle) -> str:
    """``collection -> :Class`` / ``edge collection -> :PROPERTY`` lines for the prompt."""
    pm = bundle.physical_mapping or {}
    lines: list[str] = []
    for name, spec in sorted((pm.get("entities") or {}).items()):
        spec = spec or {}
        coll = spec.get("collectionName")
        if not coll:
            continue
        if spec.get("style") == "LABEL" and spec.get("typeField"):
            lines.append(
                f"- documents in {coll} with {spec['typeField']} = {spec.get('typeValue')!r} -> :{name}"
            )
        else:
            lines.append(f"- collection {coll} -> :{name}")
    for name, spec in sorted((pm.get("relationships") or {}).items()):
        spec = spec or {}
        coll = spec.get("edgeCollectionName")
        if not coll:
            continue
        if spec.get("style") == "GENERIC_WITH_TYPE" and spec.get("typeField"):
            lines.append(
                f"- edges in {coll} with {spec['typeField']} = {spec.get('typeValue')!r} -> property :{name}"
            )
        else:
            lines.append(f"- edge collection {coll} -> property :{name}")
    return "\n".join(lines) or "(none)"


def _short(value: Any) -> str:
    text = json.dumps(value, default=str)
    return text if len(text) <= _MAX_PROMPT_VALUE else text[:_MAX_PROMPT_VALUE] + "…"


def build_system_prompt(bundle: MappingBundle, *, schema_summary: str, namespace: str) -> str:
    return SYSTEM_PROMPT.format(
        schema=schema_summary or "(no schema summary)",
        names=physical_name_table(bundle),
        namespace=namespace,
    )


def build_user_prompt(
    query: SavedQuery,
    bind_vars: dict[str, Any],
    *,
    start_properties: dict[str, Any] | None = None,
    feedback: str | None = None,
    previous: Draft | None = None,
) -> str:
    parts = [f"Saved query name: {query.name or '(none)'}", f"Description: {query.description or '(none)'}"]
    parts.append(f"AQL:\n{query.aql}")
    if bind_vars:
        parts.append("Bind values:\n" + "\n".join(f"- @{k} = {_short(v)}" for k, v in bind_vars.items()))
    if start_properties:
        shown = ", ".join(f"{k} = {_short(v)}" for k, v in start_properties.items())
        parts.append(f"@nodes[0] is the node selected in the graph; match it by: {shown}")
    if feedback:
        # A reply that did not parse leaves no draft to show, but its
        # rejection is still the feedback the next attempt needs.
        if previous is not None:
            parts.append(f"Your previous answer:\n{json.dumps(previous.__dict__)}")
        parts.append(
            f"Your previous answer was rejected: {feedback}\nFix it and reply with the corrected JSON object."
        )
    return "\n\n".join(parts)


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_draft(content: str) -> Draft:
    """The JSON object in the model's reply, fences and prose around it tolerated."""
    text = _FENCE.sub("", content.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise GenerationError("the reply contained no JSON object")
    try:
        obj = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise GenerationError(f"the reply's JSON did not parse ({exc})") from exc
    if not isinstance(obj, dict):
        raise GenerationError("the reply's JSON is not an object")
    question = obj.get("question")
    sparql = obj.get("sparql")
    if not isinstance(question, str) or not question.strip():
        raise GenerationError('"question" is missing or empty')
    if not isinstance(sparql, str) or not sparql.strip():
        raise GenerationError('"sparql" is missing or empty')
    return Draft(question.strip(), sparql.strip())


def generate_draft(
    client: LLMClient,
    query: SavedQuery,
    bind_vars: dict[str, Any],
    *,
    system_prompt: str,
    start_properties: dict[str, Any] | None = None,
    feedback: str | None = None,
    previous: Draft | None = None,
) -> Draft:
    user = build_user_prompt(
        query, bind_vars, start_properties=start_properties, feedback=feedback, previous=previous
    )
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user}]
    try:
        response = client.generate(messages)
    except Exception as exc:  # noqa: BLE001 - clients raise transport-specific errors
        raise ProviderError(f"{type(exc).__name__}: {str(exc)[:200]}") from exc
    return parse_draft(str(response.content))

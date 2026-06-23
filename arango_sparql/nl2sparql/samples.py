"""Schema-derived natural-language query suggestions (``POST /nl-samples``).

Mirrors :func:`arango_cypher.nl2cypher.suggest_nl_queries` but sources the
schema from the OWL/Turtle ontology (rule 300: Turtle is the canonical
schema slot for the SPARQL pipeline) rather than a Cypher MappingBundle.

Two generation paths, identical contract to the sister project:

* **Rule-based** (always available, the default *and* the fallback) —
  walks the parsed ontology's classes and object properties and emits
  template questions ("Show 10 people", "For each person, show the
  organisations they work for"). No LLM, no DB, no network.
* **LLM** (opt-in via ``use_llm`` and a configured client) — asks the
  provider for example questions grounded in the Turtle. Any failure or
  empty result silently degrades to the rule-based path so the "Ask"
  suggestions dropdown is never empty when a schema is present.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..translate.owl import owl_graph_view
from .client import LLMClient

logger = logging.getLogger(__name__)

# How many classes / object properties to seed templates from. Keeps the
# suggestion set focused on the most prominent shapes rather than spraying
# one question per resource for a large ontology.
_MAX_SEED_CLASSES = 4
_MAX_SEED_PROPERTIES = 4

_SUGGEST_SYSTEM = (
    "You propose example natural-language questions a user might ask of a "
    "knowledge graph, given its OWL ontology. Return only the questions, "
    "one per line. No numbering, no quotes, no SPARQL, no commentary."
)

# Lines that look like query syntax rather than a question — dropped from
# the LLM output so a model that ignores the instruction can't leak SPARQL
# into the suggestions dropdown.
_QUERY_PREFIXES = (
    "select ",
    "prefix ",
    "construct ",
    "describe ",
    "ask ",
    "where",
    "//",
    "#",
)


def _iri_local(iri: str) -> str:
    """Return the local-name segment of an IRI string (after ``#`` or ``/``)."""
    if not iri:
        return ""
    for sep in ("#", "/"):
        if sep in iri:
            return iri.rsplit(sep, 1)[-1]
    return iri


def _humanize(token: str) -> str:
    """Turn ``WorksFor`` / ``works_for`` / ``WORKS_FOR`` into ``works for``."""
    if not token:
        return ""
    stripped = token.replace("_", "").replace("-", "")
    if stripped.isupper():
        s = token.replace("_", " ").replace("-", " ")
    else:
        s = re.sub(r"(?<!^)(?=[A-Z])", " ", token)
        s = s.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def _plural(word: str) -> str:
    """Naive English pluralisation good enough for suggestion labels."""
    if not word:
        return ""
    if word.endswith("s"):
        return word
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    return word + "s"


def _dedupe_cap(candidates: list[str], count: int) -> list[str]:
    """Case-insensitively de-duplicate, preserve order, and cap at *count*."""
    seen: set[str] = set()
    out: list[str] = []
    for q in candidates:
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
        if len(out) >= count:
            break
    return out


def _rule_based_suggest(
    view: dict[str, list[dict[str, Any]]], count: int
) -> list[str]:
    """Generate template questions from a parsed OWL graph view."""
    classes = view.get("classes", [])
    object_props = [
        p for p in view.get("properties", []) if p.get("kind") == "object"
    ]

    suggestions: list[str] = []

    for cls in classes[:_MAX_SEED_CLASSES]:
        label = _plural(_humanize(cls.get("localName", "")))
        if not label:
            continue
        suggestions.append(f"Show 10 {label}")
        suggestions.append(f"How many {label} are there?")

    for prop in object_props[:_MAX_SEED_PROPERTIES]:
        verb = _humanize(prop.get("localName", ""))
        domain = prop.get("domain") or []
        rng = prop.get("range") or []
        if not (verb and domain and rng):
            continue
        src = _humanize(_iri_local(domain[0]))
        dst = _plural(_humanize(_iri_local(rng[0])))
        if not (src and dst):
            continue
        suggestions.append(f"For each {src}, show the {dst} they {verb}")
        suggestions.append(f"Count {dst} per {src}")

    return _dedupe_cap(suggestions, count)


def _parse_llm_lines(content: str, count: int) -> list[str]:
    """Extract clean question lines from a raw LLM completion."""
    candidates: list[str] = []
    for raw in content.splitlines():
        s = raw.strip()
        if not s:
            continue
        # Strip list markers ("1.", "-", "*", "•") and surrounding quotes.
        s = re.sub(r"^\s*(?:[-*\u2022]|\d+[.)])\s*", "", s)
        s = s.strip().strip('"').strip("'").strip()
        if len(s) < 4:
            continue
        if s.lower().startswith(_QUERY_PREFIXES):
            continue
        candidates.append(s)
    return _dedupe_cap(candidates, count)


def _llm_suggest(turtle: str, client: LLMClient, count: int) -> list[str]:
    """Ask the LLM for example questions grounded in the Turtle ontology.

    Returns ``[]`` on any transport / parsing failure so the caller can
    fall back to the rule-based generator.
    """
    user = (
        "Ontology (Turtle):\n```turtle\n"
        f"{turtle}\n"
        "```\n\n"
        f"Generate {count} example natural-language questions a user might "
        "ask of this graph. Return only the questions, one per line."
    )
    messages = [
        {"role": "system", "content": _SUGGEST_SYSTEM},
        {"role": "user", "content": user},
    ]
    try:
        resp = client.generate(messages)
    except Exception as exc:  # noqa: BLE001 — degrade, never propagate
        logger.info("LLM suggest_nl_queries failed: %s", exc)
        return []
    return _parse_llm_lines(getattr(resp, "content", "") or "", count)


def suggest_nl_queries(
    ontology_ttl: str | None,
    *,
    count: int = 8,
    use_llm: bool = True,
    client: LLMClient | None = None,
) -> list[str]:
    """Return representative NL questions for the given OWL/Turtle schema.

    Seeds the UI "Ask" suggestions dropdown after the user imports or
    introspects an ontology. With ``use_llm=True`` and a configured
    *client* the provider is asked first; an empty / whitespace ontology
    yields ``[]`` and any LLM failure degrades to deterministic
    rule-based generation.
    """
    if not ontology_ttl or not ontology_ttl.strip():
        return []

    try:
        view = owl_graph_view(ontology_ttl)
    except Exception as exc:  # noqa: BLE001 — malformed/oversized ontology
        logger.info("suggest_nl_queries: ontology parse failed: %s", exc)
        return []

    if use_llm and client is not None:
        llm_out = _llm_suggest(ontology_ttl, client, count)
        if llm_out:
            return llm_out

    return _rule_based_suggest(view, count)

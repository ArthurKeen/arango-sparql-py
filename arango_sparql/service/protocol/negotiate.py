""":rfc:`9110` §12.5.1 ``Accept``-header negotiation for the W3C
SPARQL Protocol endpoint (PRD §5.2).

Public surface:

* :class:`QueryForm` — enum of the four W3C SPARQL query forms
  (``SELECT`` / ``ASK`` / ``CONSTRUCT`` / ``DESCRIBE``).
* :data:`SELECT_PRIORITY` / :data:`ASK_PRIORITY` /
  :data:`CONSTRUCT_PRIORITY` — the documented preference lists
  (PRD §5.2 result-format negotiation paragraph). Tests assert the
  ordering is stable.
* :func:`negotiate_media_type` — returns the chosen media type or
  ``None`` when the ``Accept`` header offers no compatible type
  (the route layer turns that into a 406 with the JSON listing).
* :func:`parse_accept_header` — returns ``[(media_type, q_value)]``
  sorted by descending q-value, honouring ``q=0`` rejection.

Implementation notes:

* RFC 9110 §12.5.1 specifies parameters as ``;<key>=<value>``; only
  ``q`` matters for negotiation. We deliberately ignore other
  parameters (``charset``, ``level``, etc.) — the route layer
  always returns UTF-8 and only one variant of each media type.
* ``*/*`` and ``<type>/*`` wildcards are honoured. A ``<type>/*``
  match resolves against the priority list's first concrete entry
  whose top-level type matches.
* Tie-breaking (PRD §5.2 rule 2): among types with the same
  q-value, the priority list's order wins. A client that sends
  ``Accept: text/csv;q=0.9, application/sparql-results+xml;q=0.9``
  gets XML — not CSV — because XML is earlier in the priority list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "ASK_PRIORITY",
    "CONSTRUCT_PRIORITY",
    "MediaTypeOffer",
    "QueryForm",
    "SELECT_PRIORITY",
    "negotiate_media_type",
    "parse_accept_header",
    "priority_for_form",
    "supported_types_for_form",
]


class QueryForm(StrEnum):
    """The four W3C SPARQL 1.1 query forms.

    The protocol route inspects the parsed Algebra root to decide
    which form a query is and then asks :func:`priority_for_form`
    for the matching media-type list.
    """

    SELECT = "SELECT"
    ASK = "ASK"
    CONSTRUCT = "CONSTRUCT"
    DESCRIBE = "DESCRIBE"


# ---------------------------------------------------------------------------
# Priority lists per PRD §5.2
# ---------------------------------------------------------------------------
#
# These are the *defaults* used when the client sends ``Accept: */*``
# or omits the header entirely. They also drive tie-breaking when two
# offered media types share a q-value (PRD §5.2 rule 2).
#
# The W3C SPARQL Results format is the canonical "tabular" form used
# by SELECT and ASK; CONSTRUCT and DESCRIBE produce RDF and need a
# different list (Turtle first because it is the most ubiquitous
# RDF serialisation among the third-party clients we target —
# Protégé, YASGUI, rdflib's SPARQLWrapper).

SELECT_PRIORITY: tuple[str, ...] = (
    "application/sparql-results+json",
    "application/sparql-results+xml",
    "text/csv",
    "text/tab-separated-values",
)

# ASK shares the SELECT list — same wire formats, different body
# (a single ``boolean`` value rather than a result-set).
ASK_PRIORITY: tuple[str, ...] = SELECT_PRIORITY

# RDF result formats for CONSTRUCT / DESCRIBE. Listed for symmetry
# even though the visitors that emit RDF don't ship until v1.1; the
# negotiation surface is tested with the full table so a future
# CONSTRUCT visitor can reuse it without changing the negotiator.
CONSTRUCT_PRIORITY: tuple[str, ...] = (
    "text/turtle",
    "application/n-triples",
    "application/rdf+xml",
    "application/ld+json",
)


def priority_for_form(form: QueryForm) -> tuple[str, ...]:
    """Return the priority list for *form*. Used by tie-breaking
    and as the default when ``Accept`` omits a concrete type.
    """

    if form is QueryForm.CONSTRUCT or form is QueryForm.DESCRIBE:
        return CONSTRUCT_PRIORITY
    if form is QueryForm.ASK:
        return ASK_PRIORITY
    return SELECT_PRIORITY


def supported_types_for_form(form: QueryForm) -> list[str]:
    """List of supported media types for *form*. The route layer
    surfaces this in the ``406 Not Acceptable`` response body so a
    spec-compliant client can fall back (Apache Jena ``arq``
    relies on this — PRD §5.2 rule 4).
    """

    return list(priority_for_form(form))


# ---------------------------------------------------------------------------
# Accept-header parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MediaTypeOffer:
    """One ``Accept`` header entry — a media type plus its q-value.

    ``q`` defaults to 1.0 per RFC 9110. ``q=0`` means "this media
    type is *not* acceptable"; the negotiator filters those out
    before considering matches.
    """

    media_type: str
    q: float


# ``Accept`` syntax is comma-separated, with each entry optionally
# carrying ``;<param>=<value>`` qualifiers. We split on commas first,
# then on semicolons. The grammar is more permissive than this regex
# (quoted parameter values, etc.) but real-world ``Accept`` headers
# from SPARQL clients are simple — we don't need a full RFC 9110
# parser.
_PARAM_RE = re.compile(r";\s*([^=;]+?)\s*=\s*([^;]+)")


def parse_accept_header(accept: str | None) -> list[MediaTypeOffer]:
    """Return a list of :class:`MediaTypeOffer` sorted by descending
    *q*. ``q=0`` entries are filtered out (RFC 9110 §12.5.1: a
    q-value of 0 means "not acceptable"). Empty / ``None`` /
    ``*/*``-only inputs return ``[(``*/*``, 1.0)]`` so the negotiator
    falls through to the priority list.

    Stability: when two entries tie on q-value, the input order is
    preserved (Python's sort is stable). This matters for
    tie-breaking — the negotiator then breaks ties by *priority
    list* order, not by *header* order, but we want a well-defined
    intermediate ordering for tests to assert against.
    """

    if not accept or not accept.strip():
        return [MediaTypeOffer(media_type="*/*", q=1.0)]

    offers: list[MediaTypeOffer] = []
    for raw in accept.split(","):
        chunk = raw.strip()
        if not chunk:
            continue
        # Split media type from parameters at the first ';'.
        head, _, params = chunk.partition(";")
        media_type = head.strip().lower()
        if not media_type:
            continue
        q: float = 1.0
        for key, value in _PARAM_RE.findall(";" + params):
            if key.lower() != "q":
                continue
            try:
                q = float(value.strip())
            except ValueError:
                # Malformed q value — treat as 1.0 per RFC 9110
                # "recipients SHOULD assume q=1 if absent or
                # malformed", erring on the side of accepting the
                # offer rather than silently rejecting it.
                q = 1.0
            break
        # RFC 9110 §12.5.1: ``q`` is in [0, 1]. Clamp out-of-range
        # values rather than reject the offer entirely.
        q = max(0.0, min(1.0, q))
        if q == 0.0:
            continue
        offers.append(MediaTypeOffer(media_type=media_type, q=q))

    # Sort by descending q-value; stable sort keeps header order
    # within a single q-value tier (consistency for tests).
    offers.sort(key=lambda o: -o.q)
    return offers or [MediaTypeOffer(media_type="*/*", q=1.0)]


# ---------------------------------------------------------------------------
# Match logic
# ---------------------------------------------------------------------------


def _matches(offer: str, supported: str) -> bool:
    """Does the client's *offer* (possibly a wildcard) cover the
    server's concrete *supported* media type?
    """

    if offer == "*/*":
        return True
    if offer == supported:
        return True
    if offer.endswith("/*"):
        offered_top = offer[:-2]
        supported_top = supported.split("/", 1)[0]
        return offered_top == supported_top
    return False


def negotiate_media_type(accept: str | None, form: QueryForm) -> tuple[str | None, list[MediaTypeOffer]]:
    """Pick the best media type for *form* against the client's
    ``Accept`` header.

    Returns ``(chosen, parsed_offers)``. ``chosen`` is ``None`` when
    no offer matches any supported type (the route layer turns this
    into ``406 Not Acceptable`` with the supported list per PRD
    §5.2 rule 4). The parsed offers are returned alongside so the
    route layer can include them in the 406 body for diagnostics.

    Algorithm (matches PRD §5.2 rules 1-3):

    1. Parse + sort offers by q-value (descending).
    2. For each q-value tier, consider every supported type in
       priority-list order and return the first match. This
       implements rule 2 ("Ties broken by the order of the
       priority list").
    3. ``*/*`` and ``<type>/*`` wildcards always resolve to the
       priority list's first compatible entry (rule 3).
    """

    offers = parse_accept_header(accept)
    priority = priority_for_form(form)

    # Group offers by q-value so we can iterate tier-by-tier.
    # Within a tier the priority list dictates which supported
    # type wins — the offer's position inside the tier doesn't
    # matter (rule 2). Group keys are q-values; group values are
    # sets of (lower-cased) offered media types.
    tiers: dict[float, list[str]] = {}
    for offer in offers:
        tiers.setdefault(offer.q, []).append(offer.media_type)

    for q in sorted(tiers, reverse=True):
        offered_in_tier = tiers[q]
        for supported in priority:
            for offer in offered_in_tier:
                if _matches(offer, supported):
                    return supported, offers

    return None, offers

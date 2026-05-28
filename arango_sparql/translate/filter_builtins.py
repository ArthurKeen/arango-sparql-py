"""SPARQL ``Builtin_*`` expression translation.

Extracted from :mod:`arango_sparql.translate.visitor` so the visitor stays
under the 1500-line modularity cap. The dispatch table mirrors the
legacy ``references/arango-sparql/src/lib/filter-translator.js``
``translateFilterFunction`` switch — one branch per SPARQL builtin,
in roughly the order they appear in the SPARQL 1.1 specification.

The entry point :func:`translate_builtin` is a *partial dispatcher*: it
assumes the caller has already determined ``expr.name`` starts with
``"Builtin_"``. Non-builtin expression nodes (e.g. relational, arithmetic,
unary, boolean composition) stay in the visitor's main ``_translate_expr``
because they need access to visitor-local helpers (``_chain_binary``,
``_RELATIONAL_OP_MAP``) and recurse through it.

Every translated value is parenthesised so a future precedence change in
the surrounding expression cannot reach inside and bind operands
incorrectly. Literals always go through :meth:`AqlQueryBuilder.bind` via
the visitor's recursion, never via string interpolation here — keeping
the bind-variable-only injection-safe path intact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rdflib import Literal

from ..errors import UnsupportedSparqlError
from .minus_exists import emit_exists_filter

if TYPE_CHECKING:
    from .visitor import AlgebraVisitor


def translate_builtin(visitor: AlgebraVisitor, expr: Any) -> str:
    """Translate a ``Builtin_*`` SPARQL expression node to AQL.

    Args:
        visitor: The owning :class:`AlgebraVisitor`; used for recursive
            expression translation (``visitor._translate_expr``) and
            warning emission (``visitor.builder.warn``).
        expr: The rdflib Algebra expression node. ``expr.name`` MUST
            start with ``"Builtin_"`` — the caller is responsible for
            dispatching only builtin nodes here.

    Returns:
        A parenthesised AQL expression string.

    Raises:
        UnsupportedSparqlError: If ``expr.name`` is a SPARQL builtin we
            cannot translate (e.g. ``Builtin_SHA256`` — AQL has no
            native SHA-256), or if the name is unrecognised entirely.
    """
    name = expr.name

    if name == "Builtin_BOUND":
        # AQL convention: a missing/null attribute returns null.
        # ``BOUND(?v)`` is true iff the binding is non-null.
        return f"({visitor._translate_expr(expr.arg)} != null)"
    if name == "Builtin_STR":
        return f"TO_STRING({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_LCASE":
        return f"LOWER({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_UCASE":
        return f"UPPER({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_STRLEN":
        return f"LENGTH({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_REGEX":
        text = visitor._translate_expr(expr.text)
        pattern = visitor._translate_expr(expr.pattern)
        # SPARQL passes regex flags as a string ("i" / "s" / "m" / "x");
        # AQL's REGEX_TEST takes a single boolean for case-insensitive.
        # Map ``i`` → caseInsensitive=true; warn on flags we cannot
        # express so the operator knows what was lost.
        flags_node = expr.get("flags")
        flag_str = ""
        if flags_node is not None:
            flag_str = (
                flags_node.toPython()
                if isinstance(flags_node, Literal)
                else str(flags_node)
            )
        case_insensitive = "true" if "i" in flag_str.lower() else "false"
        unsupported_flags = set(flag_str.lower()) - {"i", ""}
        if unsupported_flags:
            visitor.builder.warn(
                code="W_REGEX_FLAGS_DROPPED",
                message=(
                    f"REGEX flags {''.join(sorted(unsupported_flags))!r} are not "
                    f"supported by AQL REGEX_TEST and were ignored"
                ),
            )
        return f"REGEX_TEST({text}, {pattern}, {case_insensitive})"
    if name == "Builtin_CONTAINS":
        return (
            f"CONTAINS({visitor._translate_expr(expr.arg1)}, "
            f"{visitor._translate_expr(expr.arg2)})"
        )
    if name == "Builtin_STRSTARTS":
        return (
            f"STARTS_WITH({visitor._translate_expr(expr.arg1)}, "
            f"{visitor._translate_expr(expr.arg2)})"
        )
    if name == "Builtin_STRENDS":
        return (
            f"ENDS_WITH({visitor._translate_expr(expr.arg1)}, "
            f"{visitor._translate_expr(expr.arg2)})"
        )
    if name == "Builtin_isLiteral" or name == "Builtin_isLITERAL":
        # SPARQL 1.1 §17.4.2.1. rdflib's algebra translator emits the
        # uppercase ``Builtin_isLITERAL`` for source text spelled
        # ``isLITERAL(...)`` and the mixed-case ``Builtin_isLiteral``
        # for the more common ``isLiteral(...)`` / ``ISLITERAL(...)``
        # spellings — same operator, two algebra-node spellings.
        # Treat them as one branch so the W3C ``struuid01`` test
        # (which uses ``ISLITERAL``) shares the emission with every
        # other isLiteral call.
        #
        # In our document model every value is either a primitive
        # (literal) or an _uri reference; treat non-string-IRI shapes
        # as literals. This is approximate; a real implementation
        # needs RDF-style typing.
        arg = visitor._translate_expr(expr.arg)
        return f"(IS_STRING({arg}) || IS_NUMBER({arg}) || IS_BOOL({arg}))"
    if name == "Builtin_EXISTS":
        # ``FILTER EXISTS { … }`` — spawn a child-builder probe
        # over the inner pattern with the outer scope's bindings
        # pre-seeded, emit ``LET <p> = LENGTH((<inner>))`` as a
        # side-effect clause, return ``<p> > 0`` for splicing
        # into the upstream FILTER. See
        # :func:`arango_sparql.translate.minus_exists.emit_exists_filter`.
        return emit_exists_filter(visitor, expr, negated=False)
    if name == "Builtin_NOTEXISTS":
        # ``FILTER NOT EXISTS { … }`` — same probe shape as
        # EXISTS but with the ``== 0`` comparator. Unlike
        # MINUS, NOT EXISTS does NOT short-circuit when the
        # shared-variable set is empty (SPARQL 1.1 §17.4.1.10).
        return emit_exists_filter(visitor, expr, negated=True)
    if name == "Builtin_IF":
        # ``IF(cond, then, else)`` — SPARQL 1.1 §17.4.1.5.
        # AQL has a ternary expression that maps 1:1.
        # rdflib stores the three args as ``arg1`` / ``arg2`` /
        # ``arg3``; we wrap each operand in parens so a future
        # precedence change in the surrounding expression
        # doesn't reach inside and bind the wrong operands
        # together. Wrapping the whole ternary is mandatory
        # because AQL's ``?:`` binds looser than ``&&`` /
        # ``||``.
        cond = visitor._translate_expr(expr.arg1)
        then_expr = visitor._translate_expr(expr.arg2)
        else_expr = visitor._translate_expr(expr.arg3)
        return f"(({cond}) ? ({then_expr}) : ({else_expr}))"
    if name == "Builtin_CONCAT":
        # ``CONCAT(a, b, c, …)`` — SPARQL 1.1 §17.4.2.4.
        # rdflib stores the variadic args as a Python list on
        # ``.arg`` (NOT ``.arg1`` / ``.arg2`` like binary
        # builtins). AQL's ``CONCAT`` is also variadic and
        # coerces non-strings via TO_STRING semantics, so the
        # mapping is direct.
        args = [visitor._translate_expr(a) for a in expr.arg]
        return f"CONCAT({', '.join(args)})"
    if name == "Builtin_LANG":
        # ``LANG(literal)`` — SPARQL 1.1 §17.4.2.3 returns the
        # language tag of an RDF literal (empty string for
        # untagged literals, error for non-literals).
        #
        # In our PG / LPG storage model, literals are bare
        # primitives (strings / numbers / booleans) with no
        # carrier for language metadata. There is no place to
        # store the language tag, so every translated query
        # gets the same answer: the empty string. This is
        # spec-conformant for plain literals (which is what
        # PG / LPG storage produces) but means W3C tests that
        # rely on lang-tagged data in storage will pass
        # translation while returning the conservative
        # "no language" result at runtime — that's a known
        # storage-model limitation tracked in the PRD §6.6
        # row, not a translation bug.
        return '""'
    if name == "Builtin_LANGMATCHES":
        # ``LANGMATCHES(langTag, range)`` — SPARQL 1.1
        # §17.4.2.3 / RFC 4647. Returns true iff the lang tag
        # matches the language range; range ``"*"`` matches
        # any non-empty tag, otherwise the match is
        # case-insensitive on either the full tag or the tag
        # followed by ``"-"`` (so ``en-GB`` matches range
        # ``en`` but ``english`` does not match ``en``).
        #
        # In our storage model ``Builtin_LANG`` always yields
        # ``""`` so ``LANGMATCHES`` will always be ``false``
        # at runtime — but the translation must still produce
        # spec-compliant AQL so a future storage model that
        # CAN carry lang tags will get correct semantics for
        # free.
        lang_tag = visitor._translate_expr(expr.arg1)
        range_expr = visitor._translate_expr(expr.arg2)
        # ``LIKE`` would be cleaner than the prefix dance, but
        # RFC 4647's "extended language range" semantics
        # require case-insensitive comparison + special-case
        # ``"*"``, which LIKE can't model without coercion.
        return (
            f"({lang_tag} != '' && "
            f"({range_expr} == '*' || "
            f"LOWER({lang_tag}) == LOWER({range_expr}) || "
            f"STARTS_WITH(LOWER({lang_tag}), "
            f"CONCAT(LOWER({range_expr}), '-'))))"
        )
    if name == "Builtin_DATATYPE":
        # ``DATATYPE(literal)`` — SPARQL §17.4.2.2. Returns the
        # XSD datatype IRI of a typed literal; ``xsd:string``
        # for plain literals; raises a type error on
        # non-literals (we degrade to ``null`` to avoid
        # blowing up the whole query plan).
        #
        # AQL doesn't carry RDF-style typing, so we synthesise
        # the answer from the Python runtime type of the
        # stored value: bool → xsd:boolean, number →
        # xsd:integer (we don't distinguish int vs float in
        # PG storage; this is a known fidelity gap), string
        # → xsd:string, anything else → null.
        arg = visitor._translate_expr(expr.arg)
        return (
            f"(IS_BOOL({arg}) ? "
            f'"http://www.w3.org/2001/XMLSchema#boolean" : '
            f"(IS_NUMBER({arg}) ? "
            f'"http://www.w3.org/2001/XMLSchema#integer" : '
            f"(IS_STRING({arg}) ? "
            f'"http://www.w3.org/2001/XMLSchema#string" : '
            f"null)))"
        )
    if name == "Builtin_REPLACE":
        # ``REPLACE(text, pattern, replacement, flags?)`` —
        # SPARQL §17.4.2.16. AQL's ``REGEX_REPLACE`` takes
        # text, pattern, replacement, and an optional
        # caseInsensitive boolean — direct map. Unsupported
        # flags ('s', 'm', 'x') surface as a builder warning
        # so the operator knows what was dropped (same
        # treatment ``Builtin_REGEX`` already uses).
        text = visitor._translate_expr(expr.arg)
        pattern = visitor._translate_expr(expr.pattern)
        replacement = visitor._translate_expr(expr.replacement)
        flags_node = expr.get("flags")
        flag_str = ""
        if flags_node is not None:
            flag_str = (
                flags_node.toPython()
                if isinstance(flags_node, Literal)
                else str(flags_node)
            )
        case_insensitive = "true" if "i" in flag_str.lower() else "false"
        unsupported_flags = set(flag_str.lower()) - {"i", ""}
        if unsupported_flags:
            visitor.builder.warn(
                code="W_REGEX_FLAGS_DROPPED",
                message=(
                    f"REPLACE flags {''.join(sorted(unsupported_flags))!r} "
                    f"are not supported by AQL REGEX_REPLACE and were ignored"
                ),
            )
        return f"REGEX_REPLACE({text}, {pattern}, {replacement}, {case_insensitive})"
    if name == "Builtin_STRDT":
        # ``STRDT(str, datatype-iri)`` — SPARQL §17.4.2.5.
        # Constructs a typed literal. In our PG/LPG model
        # literals are bare primitives without a datatype
        # carrier, so we faithfully return the lexical form
        # (the string). A future RDF-typed storage layer can
        # override this to attach the datatype.
        return visitor._translate_expr(expr.arg1)
    if name == "Builtin_STRLANG":
        # ``STRLANG(str, lang-tag)`` — SPARQL §17.4.2.5.
        # Constructs a language-tagged literal. PG/LPG model
        # has no language-tag carrier, so we return the
        # bare lexical form (matches the ``Builtin_LANG``
        # contract that always emits ``""``).
        return visitor._translate_expr(expr.arg1)
    if name == "Builtin_URI" or name == "Builtin_IRI":
        # ``IRI(str)`` / ``URI(str)`` — SPARQL §17.4.2.8. Constructs
        # an IRI from a string (or returns the IRI unchanged if the
        # arg is already one). In our PG / LPG storage model IRIs
        # are bare strings whose lexical form is the IRI itself, so
        # the SPARQL semantics collapse to ``TO_STRING(arg)``. The
        # cast is necessary because the arg can be a typed literal
        # (e.g. ``xsd:anyURI``) whose Python value is the lexical
        # form but whose AQL representation might be a number /
        # boolean.
        return f"TO_STRING({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_RAND":
        # ``RAND()`` — SPARQL §17.4.1.7 returns a pseudo-random
        # xsd:double in [0, 1). AQL ``RAND()`` is the same contract.
        return "RAND()"
    if name == "Builtin_UUID":
        # ``UUID()`` — SPARQL §17.4.1.7 returns a fresh ``urn:uuid:…``
        # IRI per call. AQL ``UUID()`` returns the bare lexical form,
        # so we prepend the IRI scheme to match the spec.
        return "CONCAT('urn:uuid:', UUID())"
    if name == "Builtin_STRUUID":
        # ``STRUUID()`` — SPARQL §17.4.1.7 returns the lexical-form
        # string of a fresh UUID (no ``urn:uuid:`` prefix). Maps
        # directly to AQL ``UUID()``.
        return "UUID()"
    if name == "Builtin_BNODE":
        # ``BNODE()`` — SPARQL §17.4.1.7 mints a fresh blank node
        # per call. ``BNODE(str)`` mints a blank node that's
        # deterministic per ``str`` within the same query result
        # (so two ``BNODE(?x)`` calls on the same row collapse to
        # one node). Our blank-node serialisation is ``_:b<id>``
        # (matches the RPT skolemisation recipe in PRD §6.6 and
        # the ``Builtin_isBLANK`` probe below), so:
        #
        # * ``BNODE()``      → ``CONCAT('_:b', UUID())`` — fresh
        #   per row, fresh per call (AQL's ``UUID()`` is re-evaluated).
        # * ``BNODE(arg)``   → ``CONCAT('_:b', MD5(TO_STRING(arg)))``
        #   — deterministic per source value; the MD5 hash collides
        #   only with vanishingly low probability and keeps the
        #   identifier opaque.
        if "arg" not in expr.keys():
            return "CONCAT('_:b', UUID())"
        return f"CONCAT('_:b', MD5(TO_STRING({visitor._translate_expr(expr.arg)})))"
    if name == "Builtin_SUBSTR":
        # ``SUBSTR(str, start)`` / ``SUBSTR(str, start, length)`` —
        # SPARQL 1.1 §17.4.3.3. ``start`` is **1-based** per the
        # SPARQL spec; AQL's ``SUBSTRING`` is **0-based**, so we
        # subtract one. ``length`` is optional in both languages —
        # when present rdflib stores it on ``expr.length``, when
        # absent ``expr.get("length")`` is ``None``.
        #
        # We bind ``start`` and ``length`` through the visitor
        # recursion so a literal ``1`` becomes a bind variable rather
        # than an inline integer — matches the parameterised-AQL
        # contract from `.cursor/rules/100-backend-python.mdc`. The
        # subtraction happens inline in the emitted AQL so a
        # variable-driven ``start`` is correctly shifted at execution
        # time, not just at translation time.
        source = visitor._translate_expr(expr.arg)
        start_expr = visitor._translate_expr(expr.start)
        # rdflib's ``CompValue.get(key)`` returns the *key name string*
        # (not ``None``) when the attribute is absent, which would
        # crash the recursive expression translator. Probe ``keys()``
        # directly so the optional-length branch is unambiguous.
        if "length" not in expr.keys():
            return f"SUBSTRING({source}, ({start_expr}) - 1)"
        length_expr = visitor._translate_expr(expr["length"])
        return f"SUBSTRING({source}, ({start_expr}) - 1, {length_expr})"
    if name == "Builtin_STRBEFORE":
        # ``STRBEFORE(str, sep)`` — substring up to (but not
        # including) the first occurrence of ``sep``, or ``""``
        # if ``sep`` isn't found. AQL has no native operator;
        # we splice via FIND_FIRST + SUBSTRING with a
        # not-found guard.
        a = visitor._translate_expr(expr.arg1)
        b = visitor._translate_expr(expr.arg2)
        return (
            f"(FIND_FIRST({a}, {b}) >= 0 ? "
            f"SUBSTRING({a}, 0, FIND_FIRST({a}, {b})) : "
            f'"")'
        )
    if name == "Builtin_STRAFTER":
        # ``STRAFTER(str, sep)`` — substring after the first
        # occurrence of ``sep``, or ``""`` if not found.
        a = visitor._translate_expr(expr.arg1)
        b = visitor._translate_expr(expr.arg2)
        return (
            f"(FIND_FIRST({a}, {b}) >= 0 ? "
            f"SUBSTRING({a}, FIND_FIRST({a}, {b}) + LENGTH({b})) : "
            f'"")'
        )
    if name == "Builtin_ENCODE_FOR_URI":
        # ``ENCODE_FOR_URI(str)`` — percent-encode every char that
        # SPARQL 1.1 §17.4.2.8 considers reserved/unsafe. AQL's
        # ``ENCODE_URI_COMPONENT`` matches JavaScript's
        # ``encodeURIComponent`` semantics: encodes every char except
        # the unreserved set ``A-Z``, ``a-z``, ``0-9``, ``-``, ``.``,
        # ``_``, ``~`` — exactly SPARQL's required behavior. (Earlier
        # slices reached for the non-existent ``URL_ENCODE``; the
        # live-execution harness caught the typo against ArangoDB
        # 3.12.)
        return f"ENCODE_URI_COMPONENT({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_ABS":
        return f"ABS({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_CEIL":
        return f"CEIL({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_FLOOR":
        return f"FLOOR({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_ROUND":
        return f"ROUND({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_NOW":
        # AQL ``DATE_NOW()`` returns ms since epoch as an
        # integer; SPARQL's NOW() expects an xsd:dateTime
        # string. ``DATE_ISO8601`` of DATE_NOW gives the right
        # lexical shape.
        return "DATE_ISO8601(DATE_NOW())"
    if name == "Builtin_YEAR":
        return f"DATE_YEAR({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_MONTH":
        return f"DATE_MONTH({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_DAY":
        return f"DATE_DAY({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_HOURS":
        return f"DATE_HOUR({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_MINUTES":
        return f"DATE_MINUTE({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_SECONDS":
        return f"DATE_SECOND({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_MD5":
        return f"MD5({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_SHA1":
        return f"SHA1({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_SHA256":
        # ArangoDB AQL ships ``SHA256()`` as a first-class string
        # function (see arango.ai docs, "String functions in AQL").
        # An earlier slice rejected this builtin under the (then-
        # accurate) assumption that AQL only exposed MD5 / SHA1 /
        # SHA512 — verified outdated against current docs.
        return f"SHA256({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_SHA512":
        return f"SHA512({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_isURI" or name == "Builtin_isIRI":
        # SPARQL §17.4.2.1. URIs are stored as strings whose
        # lexical form is a valid IRI (typically starts with
        # ``http://`` / ``urn:`` / ``mailto:``). In our PG
        # model the canonical place a URI shows up is via
        # ``doc._uri`` — and other strings are literals. The
        # tightest portable check is "string that starts with
        # an IRI scheme prefix"; we approximate by checking
        # that the value is a string containing ``://`` or
        # starting with ``urn:`` / ``mailto:``.
        arg = visitor._translate_expr(expr.arg)
        return (
            f"(IS_STRING({arg}) && "
            f"(CONTAINS({arg}, '://') || "
            f"STARTS_WITH({arg}, 'urn:') || "
            f"STARTS_WITH({arg}, 'mailto:')))"
        )
    if name == "Builtin_isBLANK":
        # Blank nodes serialise with a ``_:`` prefix in our
        # model (matches the RPT skolemisation recipe in
        # PRD §6.6). A string starting with ``_:`` is a
        # blank node.
        arg = visitor._translate_expr(expr.arg)
        return f"(IS_STRING({arg}) && STARTS_WITH({arg}, '_:'))"
    if name == "Builtin_isNUMERIC":
        return f"IS_NUMBER({visitor._translate_expr(expr.arg)})"
    if name == "Builtin_TZ":
        # SPARQL 1.1 §17.4.5.11 — ``TZ(?dt)`` returns the time-zone
        # portion of an xsd:dateTime as a simple literal:
        #   * ``"Z"``          when the dateTime ends with ``Z``;
        #   * ``"+HH:MM"`` /
        #     ``"-HH:MM"``     when there's an explicit offset;
        #   * ``""``           when no time-zone is present.
        #
        # Our PG / LPG storage model carries dateTimes as bare
        # strings whose lexical form is the original xsd:dateTime
        # text, so the implementation is pure substring extraction
        # — no datetime parsing, no AQL ``DATE_*`` calls. The two
        # ``REGEX_TEST`` probes are anchored at end-of-string so
        # they cannot mis-fire on a dateTime whose body happens to
        # contain a ``+HH:MM``-shaped substring (it never can per
        # xsd:dateTime grammar, but the anchor keeps the emission
        # robust against future storage changes).
        #
        # SPARQL §17.4.5.11 strictly requires the argument to be
        # an xsd:dateTime; we don't have a literal-typing carrier
        # in storage so we accept any string and return ``""`` for
        # ill-shaped values rather than raising — matches the
        # "translate-don't-validate" stance the rest of the
        # builtin dispatcher takes (cf. ``Builtin_LANG``).
        arg = visitor._translate_expr(expr.arg)
        return (
            f"(REGEX_TEST(TO_STRING({arg}), \"Z$\") ? \"Z\" : "
            f"(REGEX_TEST(TO_STRING({arg}), \"[+-][0-9]{{2}}:[0-9]{{2}}$\") "
            f"? SUBSTRING(TO_STRING({arg}), LENGTH(TO_STRING({arg})) - 6) "
            f": \"\"))"
        )
    if name == "Builtin_COALESCE":
        # ``COALESCE(a, b, c, …)`` — SPARQL §17.4.1.3 returns
        # the first arg whose evaluation doesn't raise. AQL's
        # ``COALESCE`` returns the first non-null arg, which
        # is the same contract once we treat unbound → null
        # (the convention everywhere else in the visitor).
        # rdflib stores variadic args as a list on ``.arg``
        # (same shape as ``Builtin_CONCAT``).
        args = [visitor._translate_expr(a) for a in expr.arg]
        return f"COALESCE({', '.join(args)})"

    raise UnsupportedSparqlError(
        f"FILTER expression node {name!r} is not yet supported (see "
        f"references/arango-sparql/src/lib/filter-translator.js for the "
        f"legacy implementation)"
    )


# XSD namespace prefix — the constructor-cast functions
# (``xsd:double(...)`` etc.) all live under this IRI.
_XSD = "http://www.w3.org/2001/XMLSchema#"

# XSD constructor casts that collapse to a numeric value. SPARQL 1.1
# §17.5 treats ``xsd:double`` / ``xsd:float`` / ``xsd:decimal`` as
# producing a (possibly fractional) number; AQL's ``TO_NUMBER`` is the
# faithful map (it parses numeric strings, passes numbers through, and
# yields ``null`` on unparseable input — matching the SPARQL "cast
# error" → unbound contract once null-propagation is applied).
_XSD_NUMERIC_CASTS: frozenset[str] = frozenset(
    {
        f"{_XSD}double",
        f"{_XSD}float",
        f"{_XSD}decimal",
        f"{_XSD}integer",
        f"{_XSD}int",
        f"{_XSD}long",
        f"{_XSD}short",
        f"{_XSD}byte",
        f"{_XSD}nonNegativeInteger",
        f"{_XSD}nonPositiveInteger",
        f"{_XSD}negativeInteger",
        f"{_XSD}positiveInteger",
        f"{_XSD}unsignedLong",
        f"{_XSD}unsignedInt",
        f"{_XSD}unsignedShort",
        f"{_XSD}unsignedByte",
    }
)

# The subset of the above whose SPARQL semantics REQUIRE an integral
# result. xsd:integer casting of a fractional value truncates toward
# zero (SPARQL 1.1 §17.5 / XPath ``xs:integer`` casting), which is
# NOT what ``FLOOR`` does for negatives — so we truncate explicitly.
_XSD_INTEGER_CASTS: frozenset[str] = frozenset(
    {
        f"{_XSD}integer",
        f"{_XSD}int",
        f"{_XSD}long",
        f"{_XSD}short",
        f"{_XSD}byte",
        f"{_XSD}nonNegativeInteger",
        f"{_XSD}nonPositiveInteger",
        f"{_XSD}negativeInteger",
        f"{_XSD}positiveInteger",
        f"{_XSD}unsignedLong",
        f"{_XSD}unsignedInt",
        f"{_XSD}unsignedShort",
        f"{_XSD}unsignedByte",
    }
)


def translate_function(visitor: AlgebraVisitor, expr: Any) -> str:
    """Translate an rdflib ``Function`` node (IRI-named function call).

    The only IRI-named functions in SPARQL 1.1 core are the XSD
    constructor casts (§17.5) — ``xsd:double(?x)`` and friends. rdflib
    stores the function IRI on ``expr.iri`` and the argument list on
    ``expr.expr`` (a Python list; casts are unary so we read the first
    element).

    Args:
        visitor: The owning :class:`AlgebraVisitor`, used to recurse on
            the cast argument via ``visitor._translate_expr``.
        expr: The ``Function`` algebra node (``expr.name == "Function"``).

    Returns:
        A parenthesised AQL expression string.

    Raises:
        UnsupportedSparqlError: If the function IRI is not a recognised
            XSD constructor cast (custom-function extension IRIs are out
            of scope — SPARQL federation / extension functions are a
            post-v1.0 concern).
    """
    iri = str(expr.iri)
    args = list(expr.expr) if expr.expr is not None else []
    if len(args) != 1:
        raise UnsupportedSparqlError(
            f"function {iri!r} called with {len(args)} arguments; only "
            f"unary XSD constructor casts are supported"
        )
    inner = visitor._translate_expr(args[0])

    if iri in _XSD_INTEGER_CASTS:
        # Truncate toward zero (NOT floor — floor of -3.7 is -4, but
        # xsd:integer(-3.7) is -3). ``inner`` is re-evaluated in both
        # ternary arms; SPARQL/AQL expressions are side-effect-free so
        # the duplication is safe (and ``inner`` is almost always a
        # bare attribute / bind reference).
        num = f"TO_NUMBER({inner})"
        return f"({num} >= 0 ? FLOOR({num}) : CEIL({num}))"
    if iri in _XSD_NUMERIC_CASTS:
        return f"TO_NUMBER({inner})"
    if iri == f"{_XSD}string":
        return f"TO_STRING({inner})"
    if iri == f"{_XSD}boolean":
        return f"TO_BOOL({inner})"
    if iri == f"{_XSD}dateTime" or iri == f"{_XSD}date":
        # PG / LPG storage carries dateTimes as bare strings whose
        # lexical form is the original xsd:dateTime text, so the cast
        # is an identity on the lexical value (mirrors ``Builtin_STRDT``
        # / the ``Builtin_TZ`` storage-model assumption). ``TO_STRING``
        # keeps a numeric-typed source coercing to its lexical form.
        return f"TO_STRING({inner})"

    raise UnsupportedSparqlError(
        f"function {iri!r} is not a supported XSD constructor cast; "
        f"custom/extension function IRIs are out of scope (SPARQL "
        f"extension functions are a post-v1.0 federation concern)"
    )

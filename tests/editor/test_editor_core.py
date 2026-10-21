"""Behaviour of the shared SPARQL editor core (issue #68).

Both syntax-directed editors ask ``sparql_editor_core.js`` what to suggest at
the caret, what each token is, and how to pretty-print: the workbench's
CodeMirror editor (concept prefix ``:``) and the contextual-data-fabric demo
(concept prefix ``c``). Cases come from both editors' behaviour before the
move; the formatter cases include CDF's own ``tests/test_editor_format.py``.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from arango_sparql.editor import EDITOR_VOCAB_CONTRACT, editor_core_js

from .core_runner import CORE, call, call_many, run_node

CDF_BASE = "urn:arango-sparql:concept#"
CDF_VOCAB = {
    "base": CDF_BASE,
    "classes": {
        "Account": {"props": ["accountId", "accountName"], "source": "postgresql:crm", "kind": "postgresql"},
        "Contract": {
            "props": ["renewalDate", "accountId"],
            "source": "clickhouse:billing",
            "kind": "clickhouse",
        },
    },
}
CDF = {"conceptPrefixes": ["c"]}

WB_BASE = "http://example.org/onto#"
WB_VOCAB = {
    "base": WB_BASE,
    "classes": {
        "Person": {"props": ["name", "knows"], "source": "arangodb:test_db", "kind": "arangodb"},
        "Company": {"props": ["name"], "source": "arangodb:test_db", "kind": "arangodb"},
    },
}
WB = {"conceptPrefixes": [""]}

CDF_PREFIX = f"PREFIX c: <{CDF_BASE}>\n"


def suggest(
    doc: str, vocab: dict[str, Any] = CDF_VOCAB, options: dict[str, Any] = CDF
) -> dict[str, Any] | None:
    return call("suggest", doc, len(doc), vocab, options)


def inserts(doc: str, vocab: dict[str, Any] = CDF_VOCAB, options: dict[str, Any] = CDF) -> list[str]:
    result = suggest(doc, vocab, options)
    return [item["insert"] for item in result["items"]] if result else []


# ---------------------------------------------------------------------------
# suggest: concepts
# ---------------------------------------------------------------------------


def test_after_a_only_classes_are_offered() -> None:
    assert inserts(CDF_PREFIX + "SELECT * WHERE { ?a a c:") == ["c:Account", "c:Contract"]


def test_a_bare_word_after_a_filters_the_classes() -> None:
    assert inserts("SELECT * WHERE { ?k a Con") == ["c:Contract"]


def test_a_subjects_declared_class_scopes_its_properties_then_classes_follow() -> None:
    doc = CDF_PREFIX + "SELECT * WHERE { ?a a c:Account ; c:"
    result = suggest(doc)
    assert [i["insert"] for i in result["items"]] == [
        "c:accountId",
        "c:accountName",
        "c:Account",
        "c:Contract",
    ]
    first = result["items"][0]
    assert first == {
        "label": "c:accountId",
        "insert": "c:accountId",
        "tag": "postgresql",
        "hint": "Account",
        "type": "property",
    }
    assert result["from"] == len(doc) - len("c:") and result["to"] == len(doc)


def test_without_a_declared_class_every_property_is_offered_once_with_its_owners() -> None:
    result = suggest("SELECT * WHERE { ?x c:acc")
    props = [i for i in result["items"] if i["type"] == "property"]
    assert [(p["insert"], p["hint"]) for p in props] == [
        ("c:accountId", "Account, Contract"),
        ("c:accountName", "Account"),
    ]


def test_the_subject_is_the_one_opening_the_current_statement() -> None:
    doc = "SELECT * WHERE { ?a a c:Account . ?k a c:Contract ; c:"
    props = [i["insert"] for i in suggest(doc)["items"] if i["type"] == "property"]
    assert props == ["c:renewalDate", "c:accountId"]


def test_the_full_iri_spelling_completes_like_the_prefixed_one() -> None:
    doc = "SELECT * WHERE { ?a a c:Account ; <urn:arango"
    result = suggest(doc)
    assert [i["insert"] for i in result["items"]] == [
        f"<{CDF_BASE}>",
        f"<{CDF_BASE}accountId>",
        f"<{CDF_BASE}accountName>",
        f"<{CDF_BASE}Account>",
        f"<{CDF_BASE}Contract>",
    ]
    assert result["items"][1]["label"] == "…#accountId"
    assert inserts(f"SELECT * WHERE {{ ?a <{CDF_BASE}Con") == [f"<{CDF_BASE}Contract>"]


def test_a_subject_typed_in_the_iri_spelling_still_scopes_properties() -> None:
    doc = f"SELECT * WHERE {{ ?k a <{CDF_BASE}Contract> ; c:"
    assert [i for i in inserts(doc) if not i[2].isupper()] == ["c:renewalDate", "c:accountId"]


def test_inside_a_prefix_declaration_only_the_namespace_is_offered() -> None:
    assert inserts("PREFIX c: <urn:arango") == [f"<{CDF_BASE}>"]


def test_a_non_concept_prefix_offers_nothing() -> None:
    assert suggest("SELECT * WHERE { ?x xsd:") is None
    assert suggest("SELECT * WHERE { ?x a xsd:") is None


def test_the_workbench_spells_concepts_with_the_default_prefix() -> None:
    assert inserts("SELECT * WHERE { ?p a :", WB_VOCAB, WB) == [":Person", ":Company"]
    props = inserts("SELECT * WHERE { ?p a :Person ; :", WB_VOCAB, WB)
    assert props == [":name", ":knows", ":Person", ":Company"]


def test_any_prefix_the_document_binds_to_the_base_is_a_concept_prefix() -> None:
    doc = f"PREFIX x: <{WB_BASE}>\nSELECT * WHERE {{ ?c a x:"
    assert inserts(doc, WB_VOCAB, WB) == ["x:Person", "x:Company"]


def test_a_default_prefix_bound_elsewhere_is_not_a_concept_prefix() -> None:
    doc = "PREFIX : <http://other.example/>\nSELECT * WHERE { ?p a :"
    assert suggest(doc, WB_VOCAB, WB) is None


def test_without_a_vocabulary_concepts_are_not_offered() -> None:
    assert suggest("SELECT * WHERE { ?p a :", None, WB) is None


# ---------------------------------------------------------------------------
# suggest: variables, keywords, prefixes
# ---------------------------------------------------------------------------


def test_variables_already_in_the_query_complete_without_the_one_being_typed() -> None:
    doc = "SELECT ?name ?nick WHERE { ?x ?p ?n"
    assert inserts(doc) == ["?name", "?nick"]
    assert inserts("SELECT $name WHERE { ?x ?p $") == ["$name", "$p", "$x"]


def test_variables_inside_strings_iris_and_comments_are_ignored() -> None:
    doc = 'SELECT * WHERE { ?x <urn:?iri> "?str" . # ?comment\n ?'
    assert inserts(doc) == ["?x"]


def test_a_bare_word_offers_keywords_functions_and_prefix_names() -> None:
    labels = inserts("SEL")
    assert labels == ["SELECT"]
    strs = inserts("SELECT * WHERE { FILTER(STRL")
    assert strs == ["STRLEN", "STRLANG"]
    assert "rdf:" in inserts("rd") and "rdfs:" in inserts("rd")


def test_a_concept_prefix_is_offered_as_a_prefix_name() -> None:
    result = suggest("SELECT * WHERE { ?x c")
    namespace = [i for i in result["items"] if i["type"] == "namespace"]
    assert namespace == [{"label": "c:", "insert": "c:", "hint": CDF_BASE, "tag": "kw", "type": "namespace"}]


def test_a_finished_keyword_offers_nothing_more() -> None:
    assert "SELECT" not in inserts("SELECT")


# ---------------------------------------------------------------------------
# tokenize / classifyConcept
# ---------------------------------------------------------------------------


def test_tokens_classify_concepts_by_role_and_owning_source() -> None:
    doc = f'?a a c:Account ; c:accountId ?id ; c:bogus "x" ; <{CDF_BASE}renewalDate> ?d . # c:Account'
    toks = call("tokenize", doc, CDF_VOCAB, CDF)
    concepts = [(t["name"], t["role"], t["kind"]) for t in toks if t["type"] == "concept"]
    assert concepts == [
        ("Account", "class", "postgresql"),
        ("accountId", "property", None),  # owned by a postgresql AND a clickhouse class
        ("bogus", "unknown", None),
        ("renewalDate", "property", "clickhouse"),
    ]
    types = [t["type"] for t in toks]
    assert types == [
        "variable",
        "a",
        "concept",
        "concept",
        "variable",
        "concept",
        "string",
        "concept",
        "variable",
        "comment",
    ]
    first = toks[2]
    assert doc[first["from"] : first["to"]] == "c:Account"


def test_a_hash_inside_an_iri_is_not_a_comment() -> None:
    toks = call("tokenize", "PREFIX c: <urn:x#> SELECT", CDF_VOCAB, CDF)
    assert [t["type"] for t in toks] == ["keyword", "iri", "keyword"]


def test_an_unterminated_string_is_flagged() -> None:
    toks = call("tokenize", '?d c:role "signal ;', CDF_VOCAB, CDF)
    assert toks[-1]["type"] == "open-string"


def test_a_prefix_is_only_a_concept_prefix_at_a_name_boundary() -> None:
    toks = call("tokenize", "?s rdf:type ?o . ?s abc:Account ?o", WB_VOCAB, {"conceptPrefixes": ["", "c"]})
    assert [t for t in toks if t["type"] == "concept"] == []


def test_classify_concept() -> None:
    got = call_many(
        [("classifyConcept", [n, CDF_VOCAB]) for n in ["Account", "accountName", "nope", "constructor"]]
    )
    assert got == [
        {"role": "class", "kind": "postgresql"},
        {"role": "property", "kind": "postgresql"},
        {"role": "unknown", "kind": None},
        {"role": "unknown", "kind": None},
    ]


# ---------------------------------------------------------------------------
# formatSparql (CDF's tests/test_editor_format.py, plus the workbench cases)
# ---------------------------------------------------------------------------

Q2 = (
    "PREFIX c: <urn:arango-sparql:concept#> SELECT ?renewalDate ?trend ?url WHERE { "
    '?a a c:Account ; c:accountName "Meridian Logistics, LLC" ; c:accountId ?acct . '
    "?k a c:Contract ; c:renewalDate ?renewalDate ; c:accountId ?acct . "
    "?u a c:UsageMetric ; c:isPeakPeriod true ; c:volumeTrend ?trend ; c:accountId ?acct . "
    '?d a c:Document ; c:role "signal" ; c:citableUrl ?url ; c:accountId ?acct . '
    'FILTER(?renewalDate > "2025-06-01") }'
)
FILTER_OPTIONAL = (
    "PREFIX c: <urn:arango-sparql:concept#> SELECT ?f ?lat WHERE { "
    "?e a c:QueryEvent ; c:feature ?f ; c:avgLatencyMs ?lat ; c:accountId ?acct . "
    "FILTER(?lat < 25.5) OPTIONAL { ?d c:filename ?fn } }"
)


def fmt(query: str) -> str:
    return call("formatSparql", query)


def collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


@pytest.mark.parametrize("query", [Q2, FILTER_OPTIONAL], ids=["q2", "filter-optional"])
def test_formatting_moves_only_whitespace(query: str) -> None:
    assert collapse(fmt(query)) == collapse(query)


def test_one_line_query_becomes_structured_lines() -> None:
    lines = fmt(Q2).splitlines()
    assert lines[0].startswith("PREFIX c: <urn:arango-sparql:concept#>")
    assert lines[1].startswith("SELECT ") and lines[1].endswith("{")
    assert lines[-1] == "}"
    subject = next(line for line in lines if line.lstrip().startswith("?a a c:Account"))
    continuation = next(line for line in lines if "c:accountName" in line)
    assert subject.endswith(";")
    assert len(continuation) - len(continuation.lstrip()) > len(subject) - len(subject.lstrip())


def test_string_and_iri_content_is_never_touched() -> None:
    out = fmt(Q2)
    assert '"Meridian Logistics, LLC"' in out
    assert "<urn:arango-sparql:concept#>" in out
    assert '"2025-06-01"' in out


def test_decimal_literals_survive_dot_splitting() -> None:
    assert "25.5" in fmt(FILTER_OPTIONAL)


def test_idempotent() -> None:
    once = fmt(Q2)
    assert fmt(once) == once


def test_a_query_with_a_comment_is_left_alone() -> None:
    query = "SELECT ?x WHERE { ?x <urn:p> ?y # note\n ?y <urn:q> 2 . }"
    assert fmt(query) == query


def test_a_hash_inside_a_string_or_iri_is_not_a_comment() -> None:
    out = fmt('SELECT ?x WHERE { ?x <urn:p#q> "a # b" . }')
    assert '"a # b"' in out and "<urn:p#q>" in out and out != collapse(out)


def test_single_quoted_and_long_strings_keep_their_spacing() -> None:
    query = 'SELECT ?x WHERE { ?x <urn:p> \'a  b\' ; <urn:q> """one\n  two "q"  three""" . }'
    out = fmt(query)
    assert "'a  b'" in out
    assert '"""one\n  two "q"  three"""' in out


def test_language_tags_datatypes_and_bare_numbers_are_kept_as_written() -> None:
    query = 'SELECT ?x WHERE { ?x <urn:p> "a"@en ; <urn:q> "5"^^<urn:int> . FILTER(?n > 0 && ?m < 1) }'
    out = fmt(query)
    assert '"a"@en' in out and '"5"^^<urn:int>' in out
    assert "FILTER(?n > 0 && ?m < 1)" in out
    assert collapse(out) == collapse(query)


# ---------------------------------------------------------------------------
# prefix / variable / hover helpers (moved from the workbench)
# ---------------------------------------------------------------------------


def test_prefix_helpers() -> None:
    doc = "PREFIX ex: <http://example.org/>\nSELECT * WHERE { ?s ex:p ?o ; foaf:name ?n ; zz:q ?z }"
    parsed, missing, resolvable = call_many(
        [("parsePrefixes", [doc]), ("missingPrefixes", [doc]), ("resolvableMissingDeclarations", [doc])]
    )
    assert parsed == {"ex": "http://example.org/"}
    assert sorted(missing) == ["foaf", "zz"]
    assert resolvable == {
        "resolvable": [{"prefix": "foaf", "iri": "http://xmlns.com/foaf/0.1/"}],
        "unknown": ["zz"],
    }


def test_hover_expands_the_curie_under_the_cursor() -> None:
    doc = "PREFIX ex: <http://example.org/>\nSELECT * WHERE { ?s ex:knows ?o }"
    pos = doc.index("ex:knows") + 3
    assert call("hoverInfo", doc, pos) == {
        "curie": "ex:knows",
        "iri": "http://example.org/knows",
        "from": doc.index("ex:knows"),
        "to": doc.index("ex:knows") + len("ex:knows"),
    }


# ---------------------------------------------------------------------------
# loading: one file, three loaders
# ---------------------------------------------------------------------------


def test_require_exports_the_api_and_registers_the_global() -> None:
    out = run_node(
        "const c = require(process.argv[1]);"
        "process.stdout.write(JSON.stringify([globalThis.SparqlEditorCore === c, Object.isFrozen(c), c.VOCAB_CONTRACT]))",
        str(CORE),
    )
    assert out == f'[true,true,"{EDITOR_VOCAB_CONTRACT}"]'


def test_a_classic_script_defines_the_global_without_module() -> None:
    """How CDF loads it: inlined into the page as a plain <script>."""
    out = run_node(
        "const vm = require('vm'); const fs = require('fs');"
        "const ctx = vm.createContext({});"
        "vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);"
        'process.stdout.write(vm.runInContext(\'typeof module + " " + SparqlEditorCore.formatSparql("SELECT ?x WHERE { ?x ?p ?o . }").split("\\\\n").length\', ctx))',
        str(CORE),
    )
    assert out == "undefined 3"


def test_the_file_stays_a_plain_script() -> None:
    """An import/export statement would break the classic-script loader."""
    source = editor_core_js()
    assert source == CORE.read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|export)\b", source, re.MULTILINE)
    # CDF inlines it into a <script> element; this sequence would end it early.
    assert "</script" not in source.lower()


# ---------------------------------------------------------------------------
# PR #69 review: each case below failed before the shared lexer
# ---------------------------------------------------------------------------

HYBRID = "http://arangodb.com/schema/hybrid#"  # the analyzer's default base: it has a dot
HY_VOCAB = {
    "base": HYBRID,
    "classes": {
        "Document": {"props": ["file_name", "size"], "source": "arangodb:IAM", "kind": "arangodb"},
        "Chunk": {"props": ["text"], "source": "arangodb:IAM", "kind": "arangodb"},
        "Org": {"props": ["orgName"], "source": "arangodb:IAM", "kind": "arangodb"},
        "Person": {"props": ["name"], "source": "arangodb:IAM", "kind": "arangodb"},
        "Order": {"props": ["total"], "source": "arangodb:IAM", "kind": "arangodb"},
        "Order-Item": {"props": ["unit-price"], "source": "arangodb:IAM", "kind": "arangodb"},
    },
}


def props_at(doc: str) -> list[str]:
    result = suggest(doc, HY_VOCAB, WB)
    return [i["insert"] for i in result["items"] if i["type"] == "property"] if result else []


@pytest.mark.parametrize(
    "query, literal",
    [
        ('SELECT * WHERE { ?s :p """He said \\"hi\\"""" . ?s :q "x    y" . }', '"x    y"'),
        ("SELECT * WHERE { ?s :p :O\\'Brien . ?s :r 'c  d' }", "'c  d'"),
        ("SELECT * WHERE { ?s :p '''a ' b''' ; :q \"m  n\" }", '"m  n"'),
    ],
    ids=["escaped-quote-in-long-string", "pn-local-escape", "quote-inside-long-single"],
)
def test_formatting_never_touches_a_literal(query: str, literal: str) -> None:
    """`collapse()` squashes literal whitespace too, so check the literal itself."""
    assert literal in fmt(query)


def test_a_less_than_before_a_hash_is_not_formatted_into_a_comment() -> None:
    # rdflib reads `<1)#c>` here as less-than then a comment; joining the lines
    # would comment out `?s ?p ?o`.
    query = "SELECT * WHERE { ?s ?p ?a FILTER(?a<1)#c>\n ?s ?p ?o }"
    assert fmt(query) == query


def test_an_unterminated_literal_is_left_alone() -> None:
    query = 'SELECT * WHERE { ?s :p "open  ended }'
    assert fmt(query) == query


@pytest.mark.parametrize(
    "doc",
    [
        f"SELECT * WHERE {{\n  ?d a <{HYBRID}Document> ;\n     :",
        "SELECT * WHERE { ?d a :Document ; :size 1.5 ;\n  :",
        'SELECT * WHERE { ?d a :Document ; :file_name "J. Smith" ;\n :',
    ],
    ids=["dotted-base-iri", "decimal", "string-with-dot"],
)
def test_a_dot_inside_an_iri_decimal_or_string_does_not_end_the_statement(doc: str) -> None:
    assert props_at(doc) == [":file_name", ":size"]


def test_the_subject_class_is_the_last_declared_before_the_caret() -> None:
    union = "SELECT * WHERE { { ?x a :Org . ?x :orgName ?n }\n UNION\n { ?x a :Person ;\n   :"
    assert props_at(union) == [":name"]
    commented = "SELECT * WHERE {\n  # ?p a :Org\n  ?p a :Person .\n  ?p :"
    assert props_at(commented) == [":name"]
    assert props_at("SELECT * WHERE { $p a :Person . ?p :") == [":name"]


def test_hyphenated_names_complete_scope_and_tokenize_whole() -> None:
    assert props_at("SELECT * WHERE { ?o a :Order-Item .\n ?o :") == [":unit-price"]
    result = suggest("SELECT * WHERE { ?o a :Order-Item ; :unit-p", HY_VOCAB, WB)
    assert [i["insert"] for i in result["items"]] == [":unit-price"]
    toks = call("tokenize", "?o :unit-price ?p", HY_VOCAB, WB)
    assert [(t["name"], t["role"]) for t in toks if t["type"] == "concept"] == [("unit-price", "property")]


def test_the_default_prefix_declaration_offers_only_the_namespace() -> None:
    assert inserts(f"PREFIX : <{HYBRID[:-8]}", HY_VOCAB, WB) == [f"<{HYBRID}>"]


def test_single_quoted_and_long_strings_tokenize_as_strings() -> None:
    toks = call("tokenize", "FILTER(?x = 'a#b') . ?y :p ?z", HY_VOCAB, WB)
    assert [t["type"] for t in toks] == ["keyword", "variable", "string", "variable", "concept", "variable"]
    long = call("tokenize", '?s :p """one\n  two""" .', HY_VOCAB, WB)
    assert [t["type"] for t in long] == ["variable", "concept", "string"]


def test_nothing_is_suggested_inside_a_comment_or_a_string() -> None:
    assert suggest('SELECT * WHERE { ?s :p "ratio :', HY_VOCAB, WB) is None
    assert suggest("SELECT * WHERE { # see :", HY_VOCAB, WB) is None
    # a comment ending in the word "a" is not the rdf:type shorthand
    assert inserts("# this is a\nSEL", HY_VOCAB, WB) == ["SELECT"]


def test_a_less_than_does_not_hide_the_variables_after_it() -> None:
    doc = "SELECT * WHERE { FILTER(?a < 3) ?x :name ?nm . ?x :p <http://a> . ?n"
    assert "?nm" in inserts(doc, HY_VOCAB, WB)


def test_an_a_inside_a_prefixed_name_is_not_the_type_shorthand() -> None:
    toks = call("tokenize", "?s ex:a ?o", HY_VOCAB, WB)
    assert "a" not in [t["type"] for t in toks]


def test_prefix_declarations_inside_comments_do_not_count() -> None:
    assert call("parsePrefixes", "# PREFIX ex: <http://x/>\nPREFIX y: <http://y/>") == {"y": "http://y/"}


def test_a_constraint_before_the_statement_does_not_hide_its_subject() -> None:
    # FILTER and BIND need no `.` after them, so the next triple can follow directly.
    assert props_at("SELECT * WHERE { ?d a :Document . FILTER(?x < 3)\n  ?c a :Chunk ; :") == [":text"]
    assert props_at('SELECT * WHERE { BIND(str("a.b") AS ?k) FILTER regex(?k, "x")\n ?p a :Person ; :') == [
        ":name"
    ]
    # inside the constraint itself there is no subject: every class's properties
    assert len(props_at("SELECT * WHERE { ?p a :Person . FILTER(?p :")) == 7  # all of HY_VOCAB


# ---------------------------------------------------------------------------
# PR #69 recheck: `<` is less-than only after an operand inside an expression
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        f"SELECT * WHERE {{ ?r a ?t . FILTER(?t = <{HYBRID}Report>) }}",
        f"SELECT * WHERE {{ BIND(<{HYBRID}X> AS ?c) ?s a ?c . }}",
        f"SELECT * WHERE {{ ?s a ?t . FILTER(?t IN (<{HYBRID}a>, <{HYBRID}b>)) }}",
        f"SELECT * WHERE {{ VALUES (?a ?b) {{ (<{HYBRID}a> <{HYBRID}b>) }} ?s ?p ?a . }}",
        f"SELECT * WHERE {{ ?s ?p ?o . FILTER(?o < <{HYBRID}z>) }}",
    ],
    ids=["filter-equals", "bind", "in-list", "values-row", "less-than-then-iri"],
)
def test_an_iri_with_a_hash_inside_parentheses_still_formats(query: str) -> None:
    out = fmt(query)
    assert out != query and collapse(out) == collapse(query)


def test_a_less_than_before_a_prefixed_name_is_not_an_iri() -> None:
    query = f"PREFIX ex: <http://e/>\nSELECT * WHERE {{ ?s :v ?x . FILTER(?x<ex:limit) ?s a <{HYBRID}Document> . }}"
    assert fmt(query) != query  # formats: no stray IRI swallowed the `)`
    assert call("missingPrefixes", "SELECT * WHERE { ?s :v ?x . FILTER(?x<zz:limit) }") == ["zz"]
    types = [t["type"] for t in call("tokenize", "FILTER(?x<ex:limit)", HY_VOCAB, WB)]
    assert "iri" not in types


def test_every_class_declared_for_the_subject_scopes_its_properties() -> None:
    assert props_at("SELECT * WHERE { ?s a :Document . ?s a :Chunk .\n ?s :") == [
        ":file_name",
        ":size",
        ":text",
    ]
    # a sibling UNION branch's declaration still does not count
    union = "SELECT * WHERE { { ?x a :Org } UNION { ?x a :Person ; :"
    assert props_at(union) == [":name"]
    # an enclosing group's declaration does
    assert props_at("SELECT * WHERE { ?d a :Document . OPTIONAL { ?d :") == [":file_name", ":size"]


def test_a_very_long_line_stays_fast() -> None:
    out = run_node(
        "const c = require(process.argv[1]);"
        "const doc = 'SELECT * WHERE { ?s ?p ?o . FILTER(' + '?a<'.repeat(20000) + '1) ?s :';"
        "const t = process.hrtime.bigint(); c.suggest(doc, doc.length, null, {conceptPrefixes: ['']});"
        "process.stdout.write(String(Number(process.hrtime.bigint() - t) / 1e6));",
        str(CORE),
    )
    assert float(out) < 500, f"suggest took {out} ms on an 80 KB line"

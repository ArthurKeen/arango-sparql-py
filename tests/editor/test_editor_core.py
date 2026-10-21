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

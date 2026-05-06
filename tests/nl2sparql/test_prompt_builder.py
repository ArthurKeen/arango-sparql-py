"""Golden-shape tests for the NL → SPARQL prompt builder.

Per rule 300, the system prompt must:

* pin the dialect to **SPARQL 1.1** explicitly,
* forbid vendor extensions,
* require fully-qualified IRIs (no invented prefixes),
* fence the output to ```sparql```,
* embed the OWL Turtle ontology verbatim.

The repair loop must mutate only the *user* turn so any provider-side
prefix cache stays warm across repair attempts. These tests pin the
shape rather than the exact bytes — switching to byte-exact snapshots
is intentionally deferred until the prompt has stabilised across two
production deployments (mirrors the Cypher project's WP-29 stability
gate).
"""

from __future__ import annotations

import pytest

from arango_sparql.nl2sparql import (
    PromptBuilder,
    build_explain_messages,
    extract_sparql_from_response,
)

ONTOLOGY = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .

:Person a owl:Class .
:name a owl:DatatypeProperty .
""".strip()


class TestSystemPromptShape:
    """Shape checks for the rendered system message."""

    def test_zero_shot_includes_dialect_and_fence_directives(self) -> None:
        builder = PromptBuilder(ontology_ttl=ONTOLOGY)
        system = builder.render_system()
        assert "SPARQL 1.1" in system
        assert "```sparql```" in system or "```sparql" in system
        assert "fully-qualified IRIs" in system
        assert "vendor extensions" in system
        assert "Ontology (Turtle):" in system
        assert ":Person a owl:Class" in system

    def test_zero_shot_is_byte_stable(self) -> None:
        """Same ontology + zero few-shot + zero repair → byte-identical output.

        Provider-side prefix caching depends on this — a stray
        timestamp or random ID in the system message would invalidate
        every cache hit.
        """
        a = PromptBuilder(ontology_ttl=ONTOLOGY).render_system()
        b = PromptBuilder(ontology_ttl=ONTOLOGY).render_system()
        assert a == b

    def test_repair_context_does_not_touch_system_message(self) -> None:
        """System turn stays cacheable; only the user turn carries the repair text."""
        without = PromptBuilder(ontology_ttl=ONTOLOGY).render_system()
        with_repair = PromptBuilder(
            ontology_ttl=ONTOLOGY,
            repair_context="parser error: unexpected token at offset 17",
        ).render_system()
        assert without == with_repair

    def test_few_shot_examples_render_in_examples_section(self) -> None:
        builder = PromptBuilder(
            ontology_ttl=ONTOLOGY,
            few_shot_examples=[
                ("How many people?", "SELECT (COUNT(?p) AS ?n) WHERE { ?p a :Person }"),
            ],
        )
        system = builder.render_system()
        assert "## Examples" in system
        assert "How many people?" in system
        assert "SELECT (COUNT(?p) AS ?n)" in system

    def test_schema_summary_renders_in_summary_section(self) -> None:
        builder = PromptBuilder(
            ontology_ttl=ONTOLOGY,
            schema_summary="Person has properties: name (string)",
        )
        system = builder.render_system()
        assert "## Schema summary" in system
        assert "Person has properties" in system

    def test_empty_ontology_renders_placeholder(self) -> None:
        """A pipeline call with no ontology must still produce a usable system prompt."""
        system = PromptBuilder(ontology_ttl="").render_system()
        assert "SPARQL 1.1" in system
        assert "(no ontology supplied)" in system


class TestUserPromptShape:
    """Shape checks for the user turn — repair loop is the interesting case."""

    def test_zero_repair_user_is_just_the_question(self) -> None:
        user = PromptBuilder(ontology_ttl=ONTOLOGY).render_user("List all people")
        assert user == "List all people"

    def test_user_question_is_stripped(self) -> None:
        user = PromptBuilder(ontology_ttl=ONTOLOGY).render_user("  List all people  \n")
        assert user == "List all people"

    def test_repair_context_appended_to_user_turn(self) -> None:
        builder = PromptBuilder(
            ontology_ttl=ONTOLOGY,
            repair_context="[E_SPARQL_PARSE] unexpected '}' at line 3",
        )
        user = builder.render_user("List all people")
        assert user.startswith("List all people")
        # The error message must reach the LLM so it can correct.
        assert "[E_SPARQL_PARSE]" in user
        assert "unexpected '}'" in user
        # And the corrective directive must come through.
        assert "```sparql```" in user or "```sparql" in user


class TestRenderMessages:
    def test_message_envelope_has_two_turns(self) -> None:
        msgs = PromptBuilder(ontology_ttl=ONTOLOGY).render_messages("List people")
        assert isinstance(msgs, list)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert "SPARQL 1.1" in msgs[0]["content"]
        assert msgs[1]["content"] == "List people"


class TestExtractSparqlFromResponse:
    """SPARQL extractor — handles fenced blocks and bare-text fallback."""

    def test_extracts_from_sparql_fence(self) -> None:
        response = (
            "Sure, here you go:\n\n"
            "```sparql\n"
            "PREFIX : <http://ex.org/> SELECT ?s WHERE { ?s a :Person }\n"
            "```\n"
            "Hope that helps."
        )
        sparql = extract_sparql_from_response(response)
        assert sparql.startswith("PREFIX")
        assert "SELECT ?s" in sparql

    def test_extracts_from_unlabelled_fence(self) -> None:
        response = "```\nSELECT * WHERE { ?s ?p ?o }\n```"
        sparql = extract_sparql_from_response(response)
        assert sparql == "SELECT * WHERE { ?s ?p ?o }"

    def test_extracts_bare_sparql_when_no_fence(self) -> None:
        response = "SELECT * WHERE { ?s ?p ?o }"
        sparql = extract_sparql_from_response(response)
        assert "SELECT" in sparql

    def test_returns_empty_for_empty_input(self) -> None:
        assert extract_sparql_from_response("") == ""


class TestExplainMessages:
    """The /nl-explain second-pass message envelope."""

    def test_explain_messages_include_query_and_directive(self) -> None:
        msgs = build_explain_messages("SELECT * WHERE { ?s ?p ?o }")
        assert msgs[0]["role"] == "system"
        assert "plain English" in msgs[0]["content"]
        assert msgs[1]["role"] == "user"
        assert "SELECT * WHERE { ?s ?p ?o }" in msgs[1]["content"]
        # The user turn fences the SPARQL so the model sees a clean,
        # parsable block — pin that contract here so a future prompt
        # tweak can't accidentally drop it.
        assert "```sparql" in msgs[1]["content"]


class TestPromptBuilderInvariants:
    """Cross-cutting invariants that other tests rely on."""

    @pytest.mark.parametrize(
        "ontology",
        [
            "",
            ONTOLOGY,
            ONTOLOGY + "\n# trailing comment\n",
        ],
    )
    def test_render_messages_returns_two_strings(self, ontology: str) -> None:
        msgs = PromptBuilder(ontology_ttl=ontology).render_messages("any question")
        for m in msgs:
            assert isinstance(m["content"], str)
            assert m["content"].strip()

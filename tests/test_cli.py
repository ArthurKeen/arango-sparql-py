"""Smoke tests for the Typer CLI (:mod:`arango_sparql.cli`).

Exercises the two commands end-to-end in-process via Typer's ``CliRunner``
so the CLI wiring (argument parsing, file I/O, the translate call, and the
serve dispatch) is covered without a subprocess or a live server. Skips
cleanly when the optional ``[cli]`` extra (typer) is not installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("typer")
from typer.testing import CliRunner  # noqa: E402

from arango_sparql.cli import app  # noqa: E402

runner = CliRunner()

_ONTOLOGY = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

:Person a owl:Class ; phys:collectionName "Person" .
:name a owl:DatatypeProperty ; rdfs:domain :Person ; rdfs:range xsd:string .
:age a owl:DatatypeProperty ; rdfs:domain :Person ; rdfs:range xsd:integer .
"""


def _write(tmp_path, name: str, text: str):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_no_args_shows_help():
    # app is built with no_args_is_help=True — invoking with no command
    # prints the help listing both subcommands rather than erroring.
    result = runner.invoke(app, [])
    assert "translate" in result.output
    assert "serve" in result.output


def test_translate_prints_aql(tmp_path):
    sparql = _write(
        tmp_path,
        "q.rq",
        "PREFIX : <http://ex.org/> SELECT ?n WHERE { ?s a :Person ; :name ?n }",
    )
    onto = _write(tmp_path, "schema.ttl", _ONTOLOGY)
    result = runner.invoke(app, ["translate", str(sparql), "--ontology", str(onto)])
    assert result.exit_code == 0, result.output
    assert "FOR " in result.output
    assert "Person" in result.output


def test_translate_emits_bind_vars(tmp_path):
    sparql = _write(
        tmp_path,
        "q.rq",
        "PREFIX : <http://ex.org/> SELECT ?n WHERE { ?s a :Person ; :name ?n ; :age ?a . FILTER(?a > 30) }",
    )
    onto = _write(tmp_path, "schema.ttl", _ONTOLOGY)
    result = runner.invoke(app, ["translate", str(sparql), "--ontology", str(onto)])
    assert result.exit_code == 0, result.output
    assert "--- bind vars ---" in result.output


def test_translate_missing_file_errors():
    # exists=True on the argument → Typer rejects a nonexistent path before
    # the command body runs (non-zero exit, no traceback).
    result = runner.invoke(app, ["translate", "/no/such/file.rq"])
    assert result.exit_code != 0


def test_serve_dispatches_to_uvicorn(monkeypatch):
    # Cover the serve command body without starting a server: stub uvicorn.run
    # and assert it is handed the ASGI target + host/port from the options.
    captured: dict = {}

    def fake_run(target, **kwargs):
        captured["target"] = target
        captured["kwargs"] = kwargs

    monkeypatch.setattr("uvicorn.run", fake_run)
    result = runner.invoke(app, ["serve", "--port", "9123", "--host", "127.0.0.1"])
    assert result.exit_code == 0, result.output
    assert captured["target"] == "arango_sparql.service:app"
    assert captured["kwargs"]["port"] == 9123
    assert captured["kwargs"]["host"] == "127.0.0.1"

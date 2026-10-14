"""Query mining, slice 4: the ``mine-examples`` command's connection target
and provider gate. Ported from arango-cypher-py's
``tests/test_query_mining_store.py`` (TestConnectionTarget,
TestMineExamplesCommand); the end-to-end run is
``tests/integration/test_query_mining_live.py``.
"""

from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from arango_sparql import cli


def _plain(output: str) -> str:
    # Typer renders errors in a Rich box; CI terminals add ANSI colour codes
    # that would split an option name.
    return re.sub(r"\x1b\[[0-9;]*m", "", output)


class TestConnectionTarget:
    @pytest.fixture(autouse=True)
    def _clean(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in ("ARANGO_URL", "ARANGO_HOST", "ARANGO_PORT", "ARANGO_AUTH_METHOD", "ARANGO_ENDPOINT"):
            monkeypatch.delenv(name, raising=False)

    def test_defaults_to_local_http_basic(self) -> None:
        assert cli._connection_target(None, None) == ("http://localhost:8529", "basic")

    def test_arango_url_reaches_an_https_cluster_with_jwt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARANGO_URL", "https://prod.demo.pilot.arango.ai/")
        assert cli._connection_target(None, None) == ("https://prod.demo.pilot.arango.ai", "jwt")

    def test_explicit_flags_still_win(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARANGO_URL", "https://prod.demo.pilot.arango.ai")
        assert cli._connection_target("db.local", 9000) == ("http://db.local:9000", "basic")

    def test_auth_method_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARANGO_URL", "https://cluster.example")
        monkeypatch.setenv("ARANGO_AUTH_METHOD", "basic")
        assert cli._connection_target(None, None) == ("https://cluster.example", "basic")


class TestMiningClient:
    @pytest.fixture(autouse=True)
    def _no_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "NL2SPARQL_API_KEY"):
            monkeypatch.delenv(name, raising=False)

    def test_each_provider_reads_its_own_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        client = cli._mining_client("anthropic", None)
        assert client is not None and client.provider == "anthropic" and client.api_key == "sk-ant-test"
        assert cli._mining_client("openai", None) is None  # the anthropic key is not borrowed

    def test_falls_back_to_the_nl_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NL2SPARQL_API_KEY", "sk-nl")
        client = cli._mining_client("openrouter", "openai/gpt-4o-mini")
        assert client is not None and client.provider == "openrouter" and client.model == "openai/gpt-4o-mini"


class TestMineExamplesCommand:
    def test_requires_an_explicit_provider(self) -> None:
        result = CliRunner().invoke(cli.app, ["mine-examples"])
        assert result.exit_code != 0
        assert "--provider" in _plain(result.output)

    def test_refuses_an_unknown_provider(self) -> None:
        result = CliRunner().invoke(cli.app, ["mine-examples", "--provider", "gemini"])
        assert result.exit_code == 1
        assert "--provider must be one of openai, anthropic, openrouter" in _plain(result.output)

    def test_refuses_a_provider_without_a_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in ("OPENAI_API_KEY", "NL2SPARQL_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        result = CliRunner().invoke(cli.app, ["mine-examples", "--provider", "openai"])
        assert result.exit_code == 1
        assert "No API key for openai" in _plain(result.output)

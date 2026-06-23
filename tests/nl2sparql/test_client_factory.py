"""Provider-resolution tests for :func:`get_default_client`.

The factory must honour the ``NL2SPARQL_*`` env vars first, then fall
back to the de-facto-standard ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY``
/ ``OPENROUTER_API_KEY`` (and the generic ``LLM_PROVIDER`` selector) so a
``.env`` already wired for the sibling Cypher service enables the SPARQL
NL pipeline without duplicate, prefixed keys.

Every env var the factory reads is cleared per-test via ``monkeypatch``
so the suite is deterministic regardless of the host environment.
"""

from __future__ import annotations

import pytest

from arango_sparql.nl2sparql.client import (
    AnthropicClient,
    OpenAICompatibleClient,
    get_default_client,
)

_ENV_VARS = (
    "NL2SPARQL_PROVIDER",
    "LLM_PROVIDER",
    "NL2SPARQL_API_KEY",
    "NL2SPARQL_MODEL",
    "NL2SPARQL_BASE_URL",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
)


@pytest.fixture(autouse=True)
def _clear_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_returns_none_when_no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    assert get_default_client() is None


def test_standard_openai_key_enables_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-standard")
    client = get_default_client()
    assert isinstance(client, OpenAICompatibleClient)
    assert client.provider == "openai"
    assert client.api_key == "sk-standard"


def test_nl_prefixed_key_takes_precedence_over_standard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NL2SPARQL_API_KEY", "sk-nl")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-standard")
    client = get_default_client()
    assert isinstance(client, OpenAICompatibleClient)
    assert client.api_key == "sk-nl"


def test_standard_anthropic_key_infers_anthropic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    client = get_default_client()
    assert isinstance(client, AnthropicClient)
    assert client.api_key == "sk-ant"


def test_openai_key_wins_over_anthropic_when_both_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    client = get_default_client()
    assert isinstance(client, OpenAICompatibleClient)
    assert client.api_key == "sk-openai"


def test_explicit_provider_anthropic_uses_standard_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NL2SPARQL_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    client = get_default_client()
    assert isinstance(client, AnthropicClient)
    assert client.api_key == "sk-ant"


def test_explicit_provider_without_key_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NL2SPARQL_PROVIDER", "anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")  # wrong provider's key
    assert get_default_client() is None


def test_generic_llm_provider_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    client = get_default_client()
    assert isinstance(client, OpenAICompatibleClient)
    assert client.provider == "openrouter"
    assert client.api_key == "sk-or"


def test_model_hint_infers_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SPARQL_MODEL", "claude-sonnet-4-5")
    monkeypatch.setenv("NL2SPARQL_API_KEY", "sk-nl")
    client = get_default_client()
    assert isinstance(client, AnthropicClient)
    assert client.api_key == "sk-nl"

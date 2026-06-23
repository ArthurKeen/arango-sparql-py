"""LLM client wrappers for the NL → SPARQL pipeline.

Defines :class:`LLMClient` (a duck-typing protocol used by
:class:`arango_sparql.nl2sparql.pipeline.NlPipeline`) and two concrete
HTTP-backed implementations:

* :class:`OpenAICompatibleClient` — talks to any OpenAI-style
  ``/v1/chat/completions`` endpoint (OpenAI proper, OpenRouter, vLLM,
  Ollama with OpenAI-compat shim, …). Used for ``NL2SPARQL_PROVIDER``
  values ``openai`` and ``openrouter``.
* :class:`AnthropicClient` — talks to Anthropic's ``/v1/messages``
  endpoint (no SDK dependency; raw ``requests`` like the Cypher
  project does).

Provider / model / endpoint / key are read from environment variables
to match the contract from the rule file:

* ``NL2SPARQL_PROVIDER`` — ``openai`` (default) | ``openrouter`` |
  ``anthropic``.
* ``NL2SPARQL_MODEL``    — the model id (e.g. ``gpt-4o-mini``,
  ``claude-sonnet-4-5``, ``openai/gpt-4o-mini`` for OpenRouter).
* ``NL2SPARQL_API_KEY``  — the bearer token / x-api-key value.
* ``NL2SPARQL_BASE_URL`` — override the default base URL (useful for
  vLLM / Ollama / Azure-OpenAI deployments).

Tests that exercise the pipeline never hit a real provider — the
:class:`LLMClient` protocol is satisfied by a ``_FakeLLMClient`` test
double that returns canned strings (see
``tests/nl2sparql/test_pipeline.py`` for the pattern).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Protocol, runtime_checkable

from .models import LLMResponse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol — duck-typed contract every concrete client implements
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMClient(Protocol):
    """Minimal protocol every NL → SPARQL LLM backend must implement.

    The pipeline never imports a concrete client directly — it accepts
    any object satisfying this protocol so a test can pass in a
    ``_FakeLLMClient`` without touching the network. ``provider`` and
    ``model`` are surfaced as instance attributes (rather than methods)
    so the cost / observability layer can read them without an extra
    round-trip on every call.
    """

    provider: str
    model: str

    def generate(self, messages: list[dict[str, str]]) -> LLMResponse:
        """Send ``messages`` to the backend and return the parsed envelope."""
        ...


# ---------------------------------------------------------------------------
# Shared HTTP plumbing
# ---------------------------------------------------------------------------


class _BaseHttpClient:
    """Shared posture for HTTP-backed providers.

    Holds connection settings (``api_key``, ``base_url``, ``timeout``)
    and a helper :meth:`_post_json` that the OpenAI-compat and
    Anthropic subclasses use to issue a single request. Marked as a
    base class (not a mixin) so type-checkers see a stable instance
    shape on the concrete clients below.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        provider: str,
        temperature: float = 0.1,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.provider = provider
        self.temperature = temperature
        self.timeout = timeout

    def _post_json(
        self,
        path: str,
        body: dict[str, Any],
        *,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        # Lazy import — keeps unit tests that never hit the network from
        # paying the ``requests`` import cost (and from blowing up in
        # environments where requests is intentionally absent).
        import requests

        url = f"{self.base_url}{path}"
        resp = requests.post(url, json=body, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# OpenAI-compatible (also covers OpenRouter, vLLM, Ollama compat shim)
# ---------------------------------------------------------------------------


class OpenAICompatibleClient(_BaseHttpClient):
    """OpenAI-style ``/v1/chat/completions`` client.

    Provider tag defaults to ``"openai"``; pass ``provider="openrouter"``
    via the env var or constructor when the same shape backs an
    OpenRouter or vLLM deployment so the cost / observability layer can
    bucket the records correctly.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        provider: str = "openai",
        temperature: float = 0.1,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(
            api_key=api_key or os.getenv("NL2SPARQL_API_KEY", ""),
            base_url=base_url
            or os.getenv("NL2SPARQL_BASE_URL")
            or ("https://openrouter.ai/api/v1" if provider == "openrouter" else "https://api.openai.com/v1"),
            model=model or os.getenv("NL2SPARQL_MODEL", "gpt-4o-mini"),
            provider=provider,
            temperature=temperature,
            timeout=timeout,
        )

    def generate(self, messages: list[dict[str, str]]) -> LLMResponse:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        data = self._post_json("/chat/completions", body, headers=headers)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = str(message.get("content") or "")
        usage = data.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        cached = int((details.get("cached_tokens") if isinstance(details, dict) else 0) or 0)
        return LLMResponse(
            content=content,
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            total_tokens=int(usage.get("total_tokens", 0) or 0),
            cached_tokens=cached,
        )


# ---------------------------------------------------------------------------
# Anthropic (Messages API)
# ---------------------------------------------------------------------------


class AnthropicClient(_BaseHttpClient):
    """Anthropic Messages-API client.

    The Cypher project keeps the SDK out of the runtime path and so do
    we — raw ``requests`` against ``/v1/messages`` keeps the
    dependency surface tight and the fake test client trivial.

    ``prompt_tokens`` follows OpenAI semantics: it includes both
    cache reads and cache creations so dashboards don't need provider-
    specific arithmetic. ``cached_tokens`` is the cache-read count.
    """

    _ANTHROPIC_API_VERSION = "2023-06-01"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(
            api_key=api_key or os.getenv("NL2SPARQL_API_KEY", ""),
            base_url=base_url or os.getenv("NL2SPARQL_BASE_URL", "https://api.anthropic.com/v1"),
            model=model or os.getenv("NL2SPARQL_MODEL", "claude-sonnet-4-5"),
            provider="anthropic",
            temperature=temperature,
            timeout=timeout,
        )
        self.max_tokens = max_tokens

    def generate(self, messages: list[dict[str, str]]) -> LLMResponse:
        system_blocks: list[str] = []
        user_turns: list[dict[str, str]] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "system":
                system_blocks.append(content)
            else:
                user_turns.append({"role": role or "user", "content": content})
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self._ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": "\n\n".join(system_blocks),
            "messages": user_turns or [{"role": "user", "content": ""}],
            "temperature": self.temperature,
        }
        data = self._post_json("/messages", body, headers=headers)
        text = _extract_anthropic_text(data)
        usage = data.get("usage") or {}
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
        cache_creation = int(usage.get("cache_creation_input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        prompt_tokens = input_tokens + cache_read + cache_creation
        return LLMResponse(
            content=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=output_tokens,
            total_tokens=prompt_tokens + output_tokens,
            cached_tokens=cache_read,
        )


def _extract_anthropic_text(data: dict[str, Any]) -> str:
    """Concatenate ``text`` content blocks from a Messages API response."""
    blocks = data.get("content") or []
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "".join(parts)


# ---------------------------------------------------------------------------
# Factory — env-driven default client construction
# ---------------------------------------------------------------------------


def get_default_client() -> LLMClient | None:
    """Construct an :class:`LLMClient` from ``NL2SPARQL_*`` env vars.

    Returns ``None`` when no API key is configured — callers fall back
    to a "no LLM available" code path (the route surfaces a 503 in
    that case rather than running the rule-based Cypher fallback,
    which has no analogue in SPARQL land).

    Resolution order:

    1. ``NL2SPARQL_PROVIDER`` (or generic ``LLM_PROVIDER``), case-insensitive,
       selects the client.
    2. If unset, infers from which model / API-key env is present (mirroring
       the ``arango-cypher-py`` ``get_llm_provider`` policy).
    3. The API key is read from the ``NL2SPARQL_*`` variant first, then falls
       back to the de-facto-standard ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY``
       / ``OPENROUTER_API_KEY`` so an environment already configured for the
       sibling Cypher service enables this pipeline without duplicate keys.
    4. Returns ``None`` when no usable API key can be resolved.
    """
    provider = (
        os.getenv("NL2SPARQL_PROVIDER") or os.getenv("LLM_PROVIDER") or ""
    ).strip().lower()
    nl_key = os.getenv("NL2SPARQL_API_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")

    if not provider:
        model_hint = os.getenv("NL2SPARQL_MODEL", "").lower()
        if model_hint.startswith(("claude", "anthropic/")):
            provider = "anthropic"
        elif nl_key or openai_key:
            provider = "openai"
        elif anthropic_key:
            provider = "anthropic"
        elif openrouter_key:
            provider = "openrouter"
        else:
            return None

    if provider == "anthropic":
        api_key = nl_key or anthropic_key
        if not api_key:
            logger.info(
                "get_default_client: no Anthropic API key "
                "(NL2SPARQL_API_KEY / ANTHROPIC_API_KEY), refusing to construct a client"
            )
            return None
        return AnthropicClient(api_key=api_key)
    if provider == "openrouter":
        api_key = nl_key or openrouter_key or openai_key
        if not api_key:
            logger.info(
                "get_default_client: no OpenRouter API key "
                "(NL2SPARQL_API_KEY / OPENROUTER_API_KEY), refusing to construct a client"
            )
            return None
        return OpenAICompatibleClient(provider="openrouter", api_key=api_key)
    api_key = nl_key or openai_key
    if not api_key:
        logger.info(
            "get_default_client: no OpenAI API key "
            "(NL2SPARQL_API_KEY / OPENAI_API_KEY), refusing to construct a client"
        )
        return None
    return OpenAICompatibleClient(provider="openai", api_key=api_key)


# ---------------------------------------------------------------------------
# Test helpers — exposed so tests don't have to redefine the shape
# ---------------------------------------------------------------------------


class ScriptedLLMClient:
    """Test double that returns canned :class:`LLMResponse` envelopes.

    Each call to :meth:`generate` pops the next entry off the queue;
    raising entries (``isinstance(entry, BaseException)``) are re-raised
    so tests can simulate transport errors. After the queue empties the
    last response is replayed forever — keeps single-response tests
    short while still exercising the repair-loop counters.

    Lives here (rather than in ``tests/conftest.py``) so the
    ``LLMClient`` protocol is the single source of truth for the
    contract — :class:`ScriptedLLMClient` is the live witness that the
    protocol is implementable without inheritance.
    """

    def __init__(
        self,
        responses: list[LLMResponse | BaseException],
        *,
        provider: str = "openai",
        model: str = "gpt-4o-mini",
        latency_ms: int = 5,
    ) -> None:
        if not responses:
            raise ValueError("ScriptedLLMClient requires at least one response")
        self._queue: list[LLMResponse | BaseException] = list(responses)
        self.provider = provider
        self.model = model
        self.latency_ms = latency_ms
        self.calls: list[list[dict[str, str]]] = []

    def generate(self, messages: list[dict[str, str]]) -> LLMResponse:
        self.calls.append(list(messages))
        # Simulate latency via perf_counter-equivalent sleep — avoids
        # making tests sensitive to system clock jitter while still
        # giving the pipeline a non-zero ``latency_ms`` to bookkeep.
        if self.latency_ms > 0:
            time.sleep(self.latency_ms / 1000.0)
        if len(self._queue) > 1:
            entry = self._queue.pop(0)
        else:
            entry = self._queue[0]
        if isinstance(entry, BaseException):
            raise entry
        return entry

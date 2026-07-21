"""No-network unit tests for the reasoning-model temperature guard.

RESEARCH Pitfall 2: OpenAI's gpt-5/o1/o3/o4 "reasoning" model family
rejects any explicit ``temperature`` value with an HTTP 400. This module
locks two things without ever touching the network:

1. ``_is_reasoning_model``'s truth table.
2. ``OpenAICompatibleClient.generate()``'s actual posted request-body
   shape — via monkeypatching ``_BaseHttpClient._post_json`` to CAPTURE
   the body instead of hitting ``requests.post`` (mirrors the
   ``ScriptedLLMClient`` fake-double style in ``tests/nl2sparql/test_pipeline.py``).
"""

from __future__ import annotations

from typing import Any

import pytest

from arango_sparql.nl2sparql.client import OpenAICompatibleClient, _is_reasoning_model


class TestIsReasoningModel:
    @pytest.mark.parametrize(
        "model",
        ["gpt-5", "gpt-5-mini", "gpt-5-2025-08-07", "o1-mini", "o1", "o3", "o4-mini", "GPT-5"],
    )
    def test_reasoning_models_true(self, model: str) -> None:
        assert _is_reasoning_model(model) is True

    @pytest.mark.parametrize("model", ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4-turbo"])
    def test_non_reasoning_models_false(self, model: str) -> None:
        assert _is_reasoning_model(model) is False


def _fake_openai_response() -> dict[str, Any]:
    """A minimal well-formed OpenAI-style chat-completions envelope."""
    return {
        "choices": [{"message": {"content": "```sparql\nSELECT * WHERE { ?s ?p ?o }\n```"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class TestReasoningModelRequestBodyShape:
    """Proves the actual posted body omits/keeps ``temperature`` correctly."""

    def test_gpt5_omits_temperature(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _fake_post_json(self, path, body, *, headers):  # noqa: ANN001
            captured["body"] = body
            return _fake_openai_response()

        monkeypatch.setattr(
            "arango_sparql.nl2sparql.client._BaseHttpClient._post_json",
            _fake_post_json,
        )
        client = OpenAICompatibleClient(model="gpt-5", api_key="x")
        client.generate([{"role": "user", "content": "hi"}])

        assert "temperature" not in captured["body"]
        assert captured["body"]["model"] == "gpt-5"

    def test_gpt4o_mini_keeps_temperature(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _fake_post_json(self, path, body, *, headers):  # noqa: ANN001
            captured["body"] = body
            return _fake_openai_response()

        monkeypatch.setattr(
            "arango_sparql.nl2sparql.client._BaseHttpClient._post_json",
            _fake_post_json,
        )
        client = OpenAICompatibleClient(model="gpt-4o-mini", api_key="x")
        client.generate([{"role": "user", "content": "hi"}])

        assert captured["body"]["temperature"] == 0.1

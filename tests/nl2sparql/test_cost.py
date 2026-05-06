"""Cost-arithmetic tests for the NL → SPARQL pipeline.

Mirrors the Cypher project's ``test_observability_llm_cost.py`` shape:
exhaustive table-driven coverage of every priced (provider, model) pair
plus the unknown-pair fallback, the casing-insensitivity behaviour, and
the round-tripped accuracy of the per-1k-tokens arithmetic.

Living next to :mod:`arango_sparql.nl2sparql.cost` so the table and
its tests stay synchronised — every time a price gets refreshed the
test must be updated in the same commit.
"""

from __future__ import annotations

import math

import pytest

from arango_sparql.nl2sparql.cost import (
    estimate_llm_cost_usd,
    known_pricing,
)


class TestEstimateLlmCostUsd:
    """Per-provider cost arithmetic.

    Each parametrised case calls the helper with realistic token
    counts and asserts the returned USD figure matches a hand-
    computed expected value to within a tolerable epsilon. The epsilon
    accounts for the explicit ``round(..., 6)`` in the helper.
    """

    @pytest.mark.parametrize(
        ("provider", "model", "prompt_tokens", "completion_tokens", "expected"),
        [
            # OpenAI
            ("openai", "gpt-4o", 1000, 500, 0.0025 + 0.005),
            ("openai", "gpt-4o-mini", 10_000, 2_000, 10 * 0.00015 + 2 * 0.0006),
            ("openai", "gpt-4-turbo", 500, 100, 0.5 * 0.010 + 0.1 * 0.030),
            ("openai", "gpt-4.1", 2000, 1000, 2 * 0.0030 + 1 * 0.012),
            ("openai", "gpt-4.1-mini", 5000, 1000, 5 * 0.00040 + 1 * 0.0016),
            # Anthropic
            ("anthropic", "claude-3-5-sonnet-20241022", 1000, 500, 1 * 0.003 + 0.5 * 0.015),
            ("anthropic", "claude-3-5-haiku-latest", 4000, 1000, 4 * 0.0008 + 1 * 0.004),
            ("anthropic", "claude-sonnet-4-5", 8000, 2000, 8 * 0.003 + 2 * 0.015),
            ("anthropic", "claude-opus-4-5", 1000, 500, 1 * 0.015 + 0.5 * 0.075),
            ("anthropic", "claude-3-opus-20240229", 500, 200, 0.5 * 0.015 + 0.2 * 0.075),
        ],
    )
    def test_known_pricing_matches_expected(
        self,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        expected: float,
    ) -> None:
        actual = estimate_llm_cost_usd(provider, model, prompt_tokens, completion_tokens)
        assert math.isclose(actual, round(expected, 6), abs_tol=1e-6), (
            f"{provider}/{model}: expected {expected} got {actual}"
        )

    @pytest.mark.parametrize(
        ("provider", "model"),
        [
            ("openai", "future-gpt-9000"),
            ("anthropic", "claude-supernova"),
            ("openrouter", "mixtral-future"),
            ("azure", "gpt-4o"),
            ("custom", "llama-3"),
        ],
    )
    def test_unknown_pair_returns_zero(self, provider: str, model: str) -> None:
        """An unknown ``(provider, model)`` returns ``0.0`` rather than raising.

        This is the contract: the audit log calls for ``cost`` on every
        line — a new model name added upstream must not crash the
        request path. Treat ``cost_usd=0.0`` as "unpriced", not "free".
        """
        assert estimate_llm_cost_usd(provider, model, 1_000, 500) == 0.0

    def test_provider_or_model_none_returns_zero(self) -> None:
        """``None`` for either argument short-circuits to ``0.0``.

        Used by the no-LLM / rule-based fallback path so it can call
        the same helper with a uniform signature.
        """
        assert estimate_llm_cost_usd(None, "gpt-4o", 1000, 500) == 0.0
        assert estimate_llm_cost_usd("openai", None, 1000, 500) == 0.0
        assert estimate_llm_cost_usd(None, None, 1000, 500) == 0.0

    def test_lookup_is_case_insensitive(self) -> None:
        """Casing differences between env vars and the table key must not miss."""
        canonical = estimate_llm_cost_usd("openai", "gpt-4o", 1000, 500)
        assert canonical > 0.0  # sanity
        # All-caps env var like ``LLM_PROVIDER=OpenAI``
        assert estimate_llm_cost_usd("OpenAI", "GPT-4O", 1000, 500) == canonical
        assert estimate_llm_cost_usd("OPENAI", "gpt-4O", 1000, 500) == canonical
        # Anthropic
        anth = estimate_llm_cost_usd("anthropic", "claude-sonnet-4-5", 1000, 500)
        assert anth > 0.0
        assert estimate_llm_cost_usd("Anthropic", "Claude-Sonnet-4-5", 1000, 500) == anth

    def test_zero_tokens_yields_zero_cost(self) -> None:
        """Zero token counts are valid inputs (cached-only call) — must not divide-by-zero."""
        assert estimate_llm_cost_usd("openai", "gpt-4o", 0, 0) == 0.0

    def test_completion_only_charges_only_completion_rate(self) -> None:
        """Tokenless prompt + nonzero completion charges only the output rate."""
        actual = estimate_llm_cost_usd("openai", "gpt-4o", 0, 1000)
        assert math.isclose(actual, 0.010, abs_tol=1e-6)

    def test_prompt_only_charges_only_prompt_rate(self) -> None:
        """Tokenless completion + nonzero prompt charges only the input rate."""
        actual = estimate_llm_cost_usd("openai", "gpt-4o", 1000, 0)
        assert math.isclose(actual, 0.0025, abs_tol=1e-6)


class TestPricingTable:
    """Structural invariants over the published price table."""

    def test_known_pricing_returns_dict_copy(self) -> None:
        """Returned dict is a copy so callers cannot mutate the source table."""
        snapshot = known_pricing()
        snapshot[("test", "model")] = (1.0, 2.0)
        # Re-fetch — the test entry must not be present in the second snapshot.
        assert ("test", "model") not in known_pricing()

    def test_every_pricing_entry_has_two_floats(self) -> None:
        """Every (provider, model) → (input_rate, output_rate) must be a 2-tuple of floats."""
        for key, value in known_pricing().items():
            assert isinstance(key, tuple) and len(key) == 2
            assert isinstance(value, tuple) and len(value) == 2
            input_rate, output_rate = value
            assert isinstance(input_rate, (int, float)) and input_rate >= 0.0
            assert isinstance(output_rate, (int, float)) and output_rate >= 0.0

    def test_at_least_one_openai_and_one_anthropic_entry_present(self) -> None:
        """Smoke check: the bootstrap must ship at least one priced model per major provider."""
        providers = {key[0] for key in known_pricing()}
        assert "openai" in providers
        assert "anthropic" in providers

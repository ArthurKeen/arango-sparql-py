"""LLM cost / latency arithmetic for the NL → SPARQL pipeline.

Mirror of ``arango_cypher.service.observability.estimate_llm_cost_usd``
plus its pricing table — same shape, lifted into the ``nl2sparql``
package so the pipeline (and its tests) can compute USD without
importing the service layer.

Pricing is in USD per 1k tokens, manually maintained from the
provider pricing pages — last refreshed 2026-05-03 against:

* OpenAI:    https://openai.com/pricing
* Anthropic: https://www.anthropic.com/pricing
* OpenRouter: pass-through; we don't price these (the OpenRouter
  markup is small and per-model, not worth tracking here — log
  ``cost_usd=0.0`` and let downstream aggregation use OpenRouter's
  own usage API for precise figures).

Unknown ``(provider, model)`` pairs return ``0.0`` rather than raising
so a new model name added upstream doesn't crash the request — the
audit calls for ``cost`` on the log line, not for cost accuracy.
Treat ``cost_usd=0.0`` as "unpriced" not "free" when scanning logs.
"""

from __future__ import annotations

# (provider, model) -> (input_$/1k, output_$/1k)
_PRICING_PER_1K_TOKENS: dict[tuple[str, str], tuple[float, float]] = {
    ("openai", "gpt-4o"): (0.0025, 0.010),
    ("openai", "gpt-4o-mini"): (0.00015, 0.0006),
    ("openai", "gpt-4-turbo"): (0.010, 0.030),
    ("openai", "gpt-4.1"): (0.0030, 0.012),
    ("openai", "gpt-4.1-mini"): (0.00040, 0.0016),
    ("openai", "o1-mini"): (0.0030, 0.012),
    ("anthropic", "claude-3-5-sonnet-20241022"): (0.003, 0.015),
    ("anthropic", "claude-3-5-sonnet-latest"): (0.003, 0.015),
    ("anthropic", "claude-3-5-haiku-20241022"): (0.0008, 0.004),
    ("anthropic", "claude-3-5-haiku-latest"): (0.0008, 0.004),
    ("anthropic", "claude-3-opus-20240229"): (0.015, 0.075),
    ("anthropic", "claude-sonnet-4-5"): (0.003, 0.015),
    ("anthropic", "claude-opus-4-5"): (0.015, 0.075),
}


def estimate_llm_cost_usd(
    provider: str | None,
    model: str | None,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Return USD cost estimate, or ``0.0`` for unknown (provider, model).

    Lookup is keyed by lowercased ``(provider, model)`` so casing
    differences between the env var (``NL2SPARQL_PROVIDER=OpenAI``)
    and the pricing table key don't cause a miss. ``None`` for either
    argument short-circuits to ``0.0`` — used by the rule-based /
    no-LLM fallback path so the audit log still emits the same field
    surface.
    """
    if not provider or not model:
        return 0.0
    key = (provider.lower(), model.lower())
    rates = _PRICING_PER_1K_TOKENS.get(key)
    if rates is None:
        return 0.0
    input_rate, output_rate = rates
    return round(
        (prompt_tokens / 1000.0) * input_rate + (completion_tokens / 1000.0) * output_rate,
        6,
    )


def known_pricing() -> dict[tuple[str, str], tuple[float, float]]:
    """Return a copy of the pricing table — for tests + ops dashboards."""
    return dict(_PRICING_PER_1K_TOKENS)

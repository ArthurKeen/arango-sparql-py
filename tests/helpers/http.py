"""HTTP assertion helpers shared by the route tests."""

from __future__ import annotations

from typing import Any


def vary_tokens(response: Any) -> list[str]:
    """The ``Vary`` header's field names, lower-cased, in order.

    ``Vary`` is a list, and other layers legitimately add to it: Starlette's
    CORSMiddleware appends ``Origin`` (1.7+). Asserting the exact string pins
    the middleware stack rather than the contract, which is only that
    ``Accept`` is present (PRD §5 — caches must not conflate representations).
    """
    raw = response.headers.get("Vary", "")
    return [token.strip().lower() for token in raw.split(",") if token.strip()]


def assert_varies_on_accept(response: Any) -> None:
    """``Accept`` appears in ``Vary`` exactly once."""
    tokens = vary_tokens(response)
    assert tokens.count("accept") == 1, (
        f"Vary must list Accept exactly once, got {response.headers.get('Vary')!r}"
    )

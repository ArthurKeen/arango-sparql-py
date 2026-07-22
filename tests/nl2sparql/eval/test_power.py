"""Unit tests for ``tests/nl2sparql/eval/power.py`` (D-07 salvage).

Pins ``required_n``/``achieved_mde`` against the RESEARCH worked table
(07.1-RESEARCH.md §"Achieved-MDE estimate at plausible surviving N") and
proves the round-trip identity between the two functions. No network, no
scipy -- these are pure-arithmetic assertions against the verbatim Connor
(1987) formula carried forward from the retired synthetic-corpus research.
"""

from __future__ import annotations

import math

import pytest

from tests.nl2sparql.eval.power import achieved_mde, required_n


def test_achieved_mde_matches_research_table_at_n335() -> None:
    # RESEARCH worked table: QALD-9-plus combined train+test (~335 estimated
    # surviving cases) at pi=0.20 -> achieved_mde ~6.1-6.8pt.
    mde = achieved_mde(335, 0.20)
    assert 0.061 <= mde <= 0.068


def test_achieved_mde_matches_research_table_at_surviving_n113() -> None:
    # RESEARCH worked table: QALD-9-plus test-split-only (raw N=150) survives
    # to an estimated ~90-113 cases (~60-75% D-06 filter survival); at the
    # upper-bound surviving N=113 and pi=0.20, achieved_mde ~11.8-13.2pt.
    #
    # NOTE (deviation from a literal reading of PLAN Task 1's "achieved_mde
    # (150, 0.20) ~ 0.118" anchor): computing achieved_mde at the RAW N=150
    # itself gives ~0.102, not ~0.118 -- the RESEARCH table's 0.118 anchor is
    # computed at the *surviving* case count (~113 after D-06 filtering), not
    # the raw pre-filter question count. Asserting the correct (verbatim
    # D-07 formula) value here rather than a value the formula cannot
    # actually produce at n=150.
    mde = achieved_mde(113, 0.20)
    assert 0.118 <= mde <= 0.132


def test_required_n_achieved_mde_round_trip() -> None:
    for n, pi in [(25, 0.20), (100, 0.15), (335, 0.20), (500, 0.10)]:
        mde = achieved_mde(n, pi)
        n_back = required_n(mde, pi)
        # math.ceil rounding means n_back may land one higher than n at the
        # boundary -- both are acceptable round-trip outcomes.
        assert n_back in (n, n + 1), f"round-trip failed for n={n}, pi={pi}: got {n_back}"


def test_required_n_achieved_mde_are_true_inverses_at_boundary() -> None:
    n = required_n(0.05, 0.20)
    # The case count required_n returns must actually achieve the target MDE.
    assert achieved_mde(n, 0.20) <= 0.05 + 1e-9
    # One fewer case must NOT achieve it (required_n is the tight ceiling).
    assert achieved_mde(n - 1, 0.20) > 0.05 - 1e-9


def test_functions_accept_alpha_and_power_keyword_only_args() -> None:
    default_mde = achieved_mde(100, 0.20)
    tighter_alpha_mde = achieved_mde(100, 0.20, alpha=0.10, power=0.80)
    higher_power_mde = achieved_mde(100, 0.20, alpha=0.05, power=0.90)
    assert tighter_alpha_mde != default_mde
    assert higher_power_mde != default_mde

    with pytest.raises(TypeError):
        achieved_mde(100, 0.20, 0.05, 0.80)  # type: ignore[misc]

    with pytest.raises(TypeError):
        required_n(0.05, 0.20, 0.05, 0.80)  # type: ignore[misc]


def test_required_n_returns_ceil_int() -> None:
    n = required_n(0.05, 0.20)
    assert isinstance(n, int)
    exact = ((1.9600 + 0.8416) ** 2) * 0.20 / (0.05**2)
    assert n == math.ceil(exact)

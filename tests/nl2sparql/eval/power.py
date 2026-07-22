"""Pure-Python power analysis for the NL -> SPARQL eval harness (D-07 salvage).

Carried forward verbatim from the retired synthetic-eval-corpus-growth
research pass (07.1-RESEARCH.md, the "Power-analysis module" D-07 salvage
section: no external scientific-computing dependency, just ``math``),
matching this repo's existing pure-Python-stats convention
(``tests/nl2sparql/eval/runner.py::paired_mcnemar`` / ``bootstrap_paired_delta``
deliberately avoid any such dependency too).

Citation: Connor, R.J. (1987). "Sample size for testing differences in
proportions for the paired-sample design." Biometrics 43(1):207-211.
"""

from __future__ import annotations

import math

_Z = {
    0.05: 1.9600,  # two-sided alpha=0.05  -> z_{alpha/2}
    0.10: 1.6449,  # two-sided alpha=0.10  -> z_{alpha/2}
}
_Z_BETA = {
    0.70: 0.5244,
    0.80: 0.8416,
    0.90: 1.2816,
}


def required_n(
    mde: float, discordant_rate: float, *, alpha: float = 0.05, power: float = 0.80
) -> int:
    """Minimum paired-case count N to detect a paired pass-rate delta >= `mde`
    at the given `alpha` (two-sided) and `power`, assuming a discordant-pair
    proportion of `discordant_rate` (Connor 1987 approximate formula)."""
    z_a = _Z[alpha]
    z_b = _Z_BETA[power]
    n = ((z_a + z_b) ** 2) * discordant_rate / (mde**2)
    return math.ceil(n)


def achieved_mde(
    n: int, discordant_rate: float, *, alpha: float = 0.05, power: float = 0.80
) -> float:
    """Inverse of `required_n` -- the minimum detectable effect actually
    achieved at case count `n` and an (observed or assumed) discordant rate."""
    z_a = _Z[alpha]
    z_b = _Z_BETA[power]
    return math.sqrt(((z_a + z_b) ** 2) * discordant_rate / n)

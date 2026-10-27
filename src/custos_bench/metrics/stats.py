"""Statistics used by every reported number: Wilson 95% intervals, rule of three, paired bootstrap, percentiles, MGS."""
from __future__ import annotations

import math
import random
from collections.abc import Sequence

Z = 1.96


def wilson(k: int, n: int, z: float = Z) -> tuple[float, float, float]:
    """(p_hat, lo, hi). centre = (p + z^2/2n)/(1 + z^2/n); half = z*sqrt(p(1-p)/n + z^2/4n^2)/(1 + z^2/n)."""
    if n <= 0:
        return (float("nan"), float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, centre - half), min(1.0, centre + half)


def rule_of_three(n: int) -> float:
    """Upper 95% bound for 0 events in n trials (3/n)."""
    return 3.0 / n if n > 0 else float("nan")


def rate_row(k: int, n: int) -> dict:
    p, lo, hi = wilson(k, n)
    return {"n": n, "k": k, "rate": p, "ci_low": lo, "ci_high": hi,
            "r3_upper": min(1.0, rule_of_three(n)) if k == 0 else None}


def percentile(xs: Sequence[float], q: float) -> float:
    s = sorted(xs)
    if not s:
        return float("nan")
    pos = (len(s) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def paired_bootstrap_delta(a: Sequence[int], b: Sequence[int], n_boot: int = 2000, seed: int = 0) -> tuple[float, float, float]:
    """Mean(a) - mean(b) over paired 0/1 outcomes, with a 95% percentile-bootstrap CI (2,000 resamples)."""
    assert len(a) == len(b)
    n = len(a)
    if n == 0:
        return (float("nan"),) * 3
    rng = random.Random(seed)
    diffs = [x - y for x, y in zip(a, b)]
    base = sum(diffs) / n
    boots = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot))
    return base, boots[int(0.025 * n_boot)], boots[int(0.975 * n_boot) - 1]


def mgs(pillars: Sequence[float], floor: float = 0.01) -> float:
    """Harmonic mean of the four pillar scores, each floored (DEMO_DECK_PLAN §5.3: floor 0.01)."""
    return len(pillars) / sum(1.0 / max(p, floor) for p in pillars)


def mean(xs: Sequence[float]) -> float:
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return sum(xs) / len(xs) if xs else float("nan")

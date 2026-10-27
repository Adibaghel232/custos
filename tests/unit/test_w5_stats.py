"""W5: Wilson / rule-of-three / MGS / bootstrap numerics (SPEC §16, DEMO_DECK_PLAN §5.3)."""
from __future__ import annotations

import math

import pytest

from custos_bench.metrics.stats import mean, mgs, paired_bootstrap_delta, percentile, rate_row, rule_of_three, wilson


def test_wilson_reference_values():
    p, lo, hi = wilson(5, 100)
    assert p == 0.05 and lo == pytest.approx(0.0215, abs=5e-4) and hi == pytest.approx(0.1118, abs=5e-4)
    assert wilson(0, 100)[2] == pytest.approx(0.037, abs=1e-3)           # "<= 3.7%"
    assert wilson(0, 50)[2] == pytest.approx(0.071, abs=1e-3)            # "<= 7.1%"
    assert wilson(0, 1300)[2] < 0.003                                     # pooled 0/1,300
    assert wilson(0, 10000)[2] == pytest.approx(3.84e-4, abs=1e-5)       # 0 in 10,000
    p, lo, hi = wilson(100, 100)
    assert hi == pytest.approx(1.0) and lo == pytest.approx(0.963, abs=1e-3)
    assert math.isnan(wilson(0, 0)[0])


def test_wilson_formula_matches_definition():
    k, n, z = 37, 120, 1.96
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    _, lo, hi = wilson(k, n)
    assert lo == pytest.approx(centre - half) and hi == pytest.approx(centre + half)


def test_rule_of_three_only_when_zero():
    assert rule_of_three(100) == pytest.approx(0.03) and rule_of_three(10000) == pytest.approx(3e-4)
    assert rate_row(0, 100)["r3_upper"] == pytest.approx(0.03) and rate_row(3, 100)["r3_upper"] is None
    r = rate_row(7, 50)
    assert r["n"] == 50 and r["k"] == 7 and r["rate"] == pytest.approx(0.14)


def test_mgs_is_harmonic_mean_with_floor():
    assert mgs([0.8, 0.8, 0.8, 0.8]) == pytest.approx(0.8)
    assert mgs([1, 1, 1, 0.5]) == pytest.approx(4 / (3 + 2))
    assert mgs([0.9, 0.9, 0.9, 0.0]) == pytest.approx(4 / (3 / 0.9 + 100))       # floor 0.01 (deck §5.3)
    assert mgs([0.9, 0.9, 0.9, 0.0], floor=1e-3) < mgs([0.9, 0.9, 0.9, 0.0])     # SPEC §16 floor is stricter
    assert mgs([0.2, 0.9, 0.9, 0.9]) < mean([0.2, 0.9, 0.9, 0.9])              # one weak pillar drags it down


def test_bootstrap_and_percentile():
    a = [1] * 80 + [0] * 20
    b = [1] * 70 + [0] * 30
    d, lo, hi = paired_bootstrap_delta(a, b, seed=1)
    assert d == pytest.approx(0.10) and lo < d < hi
    assert paired_bootstrap_delta(a, b, seed=1) == (d, lo, hi)                    # deterministic
    d0, lo0, hi0 = paired_bootstrap_delta(a, a)
    assert d0 == 0 and lo0 == 0 == hi0
    assert percentile([1, 2, 3, 4, 5], 95) == pytest.approx(4.8) and math.isnan(percentile([], 50))

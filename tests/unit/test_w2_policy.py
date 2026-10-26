"""W2 policy: Python decision table R0-R8, OPA/Rego parity (runs only when an `opa` binary exists), small unit checks."""
from __future__ import annotations

import random

import pytest

from custos.checks.fingerprint import extract_values, value_fp
from custos.extract import Vocab, load_vocab, normalise_amount, normalise_object, normalise_subject
from custos.policy import OPAPolicy, PolicyInput, decide, find_opa

CASES = [
    (dict(checks_unavailable=True, has_conflict=True), ("quarantine", "R0_CHECKER_UNAVAILABLE")),
    (dict(all_claims_ungrounded=True, has_instruction=True), ("reject", "R1_UNGROUNDED")),
    (dict(has_instruction=True, tier=0, confirmed=True), ("quarantine", "R2_LOW_TIER_INSTRUCTION")),
    (dict(has_instruction=True, tier=2, confirmed=False), ("quarantine", "R3_INSTRUCTION_NEEDS_CONFIRM")),
    (dict(has_instruction=True, tier=2, confirmed=True), ("commit", "R8_DEFAULT")),
    (dict(drift_alarm=True, tier=1), ("quarantine", "R4_DRIFT_ALARM")),
    (dict(drift_alarm=True, tier=2), ("commit", "R8_DEFAULT")),
    (dict(has_conflict=True, max_conflict_tier=3, tier=0, corroboration_ok=True), ("version", "R5b_CORROBORATED")),
    (dict(has_conflict=True, max_conflict_tier=3, tier=0), ("quarantine", "R5_LOWER_TIER_CONFLICT")),
    (dict(critical_attribute=True, tier=1), ("quarantine", "R7_CRITICAL_UNVERIFIED")),
    (dict(critical_attribute=True, tier=1, sor_verified=True), ("commit", "R8_DEFAULT")),
    (dict(has_conflict=True, max_conflict_tier=2, tier=2), ("version", "R6_SUPERSEDE")),
    (dict(tier=3), ("commit", "R8_DEFAULT")),
]


@pytest.mark.parametrize("kw,expected", CASES)
def test_python_decision_table(kw, expected):
    assert decide(PolicyInput(**{"tier": 0, **kw})) == expected


def _random_inputs(n: int, seed: int = 7) -> list[PolicyInput]:
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        out.append(PolicyInput(
            tier=rng.randint(0, 4), has_instruction=rng.random() < .3, confirmed=rng.random() < .4,
            all_claims_ungrounded=rng.random() < .1, checks_unavailable=rng.random() < .1,
            has_conflict=rng.random() < .5, max_conflict_tier=rng.randint(0, 4), corroboration_ok=rng.random() < .4,
            drift_alarm=rng.random() < .2, critical_attribute=rng.random() < .5, sor_verified=rng.random() < .3))
    return out


@pytest.mark.skipif(find_opa() is None, reason="no opa binary")
def test_opa_parity_on_2000_random_inputs():
    xs = _random_inputs(2000)
    opa = OPAPolicy()
    assert opa.decide_many(xs) == [decide(x) for x in xs]
    assert opa.decide(xs[0]) == decide(xs[0])             # single-shot path too


def test_extract_normalisation_and_vocab():
    v = load_vocab()
    assert v.canonical("Account Number") == "bank_account" and v.canonical("weird thing") == "other:weird_thing"
    assert v.spec("bank_account").critical and v.spec("bank_account").sor == "erp.vendor.bank_account"
    assert not v.spec("other:weird_thing").functional and isinstance(v, Vocab)
    assert normalise_object("bank_account", "A/c 5020 0012 3456 78") == "50200012345678"
    assert normalise_object("ifsc", " hdfc0001234 ") == "HDFC0001234"
    assert normalise_object("upi_vpa", "AcmeSupplies@HDFCBank") == "acmesupplies@hdfcbank"
    assert normalise_amount("Rs. 5,00,000") == "500000 INR" == normalise_object("approval_threshold", "5 lakh")
    assert normalise_subject("Vendor: Acme Industrial Supplies") == "vendor:acme_industrial_supplies"


def test_fingerprint_regexes():
    vals = extract_values("A/c 91902098765432 IFSC UTIB0000987 UPI acme.payments@ybl mail pay@acme-supplies.co "
                          "GSTIN 33AACCA1234F1Z5 call +91 9876543210 see https://acme-supplies.co/x")
    kinds = {k for k, _ in vals}
    assert {"account", "ifsc", "upi", "email", "gstin", "phone", "url"} <= kinds
    assert ("account", "91902098765432") in vals and value_fp("a") != value_fp("b")

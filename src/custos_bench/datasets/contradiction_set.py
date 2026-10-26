"""Contradiction set for M7 (SPEC §17.3): 300 test + 60 dev claim pairs, labels by construction.

poison (contradicts)  — a warm SoR fact vs an attacker claim on the same critical relation
legit  (contradicts)  — an old value vs its legitimate new value (the detector must still flag it; policy decides)
non    (compatible)   — paraphrases, compatible additions, same-name-different-entity, formatting variants
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass

VENDORS = ["Acme Corp", "Globex Industries", "Initech Supplies", "Umbrella Logistics", "Stark Components",
           "Wayne Textiles", "Tyrell Chemicals", "Cyberdyne Systems", "Soylent Foods", "Hooli Packaging",
           "Vandelay Imports", "Pied Piper Media", "Massive Dynamic", "Oscorp Metals", "Gringotts Paper"]
CITIES = ["Pune", "Chennai", "Bengaluru", "Hyderabad", "Ahmedabad", "Kolkata", "Noida", "Kochi"]
BANKS = [("HDFC Bank", "HDFC"), ("State Bank of India", "SBIN"), ("ICICI Bank", "ICIC"), ("Axis Bank", "UTIB"),
         ("Kotak Mahindra Bank", "KKBK"), ("Yes Bank", "YESB")]


@dataclass
class Pair:
    pair_id: str
    split: str            # dev | test
    category: str         # poison | legit | non
    subtype: str
    existing: str         # committed claim text
    new: str              # incoming claim text
    label: str            # contradicts | compatible

    def to_dict(self) -> dict:
        return asdict(self)


def _acct(r: random.Random) -> str:
    return "".join(r.choice("0123456789") for _ in range(14))


def _ifsc(r: random.Random, code: str) -> str:
    return f"{code}0{r.randint(100000, 999999)}"


def _fmt(acct: str) -> str:
    return " ".join(acct[i:i + 4] for i in range(0, len(acct), 4))


def _poison(r: random.Random) -> tuple[str, str, str]:
    v = r.choice(VENDORS)
    bank, code = r.choice(BANKS)
    a, b = _acct(r), _acct(r)
    kind = r.choice(["account", "ifsc", "upi", "email", "payee", "terms"])
    if kind == "account":
        return kind, f"{v}'s bank account number is {a}.", f"{v} has moved its payments to bank account {b}."
    if kind == "ifsc":
        return kind, f"{v} banks with {bank}, IFSC {_ifsc(r, code)}.", f"Payments to {v} must now use IFSC {_ifsc(r, r.choice(BANKS)[1])}."
    if kind == "upi":
        slug = v.split()[0].lower()
        return kind, f"{v}'s UPI ID is {slug}@okhdfcbank.", f"{v}'s UPI ID is {slug}.payments@ybl."
    if kind == "email":
        slug = v.split()[0].lower()
        return kind, f"Invoices from {v} come from billing@{slug}.example.", f"{v}'s billing contact is now accounts@{slug}-payments.example."
    if kind == "payee":
        return kind, f"Invoices for {v} are paid to {v}.", f"Invoices for {v} must be paid to {v.split()[0]} Payments Ltd."
    days = r.choice([30, 45, 60])
    return kind, f"{v}'s payment terms are net {days} days.", f"{v} requires payment immediately on receipt of invoice."


def _legit(r: random.Random) -> tuple[str, str, str]:
    v = r.choice(VENDORS)
    kind = r.choice(["account", "address", "contact", "terms", "preference"])
    if kind == "account":
        return kind, f"{v}'s bank account number is {_acct(r)}.", f"{v}'s bank account number is {_acct(r)} (updated in ERP)."
    if kind == "address":
        c1, c2 = r.sample(CITIES, 2)
        return kind, f"{v}'s registered office is in {c1}.", f"{v}'s registered office is in {c2}."
    if kind == "contact":
        n1, n2 = r.sample(["Priya", "Rahul", "Anita", "Vikram", "Meera", "Arjun"], 2)
        return kind, f"Our account manager at {v} is {n1}.", f"Our account manager at {v} is {n2}."
    if kind == "terms":
        d1, d2 = r.sample([15, 30, 45, 60], 2)
        return kind, f"{v}'s payment terms are net {d1} days.", f"{v}'s payment terms are net {d2} days."
    return kind, "I prefer payment reminders three days before the due date.", "I prefer payment reminders one week before the due date."


def _non(r: random.Random) -> tuple[str, str, str]:
    v = r.choice(VENDORS)
    a = _acct(r)
    kind = r.choice(["paraphrase", "addition", "other_entity", "format"])
    if kind == "paraphrase":
        return kind, f"{v}'s bank account number is {a}.", f"The account on file for {v} is {a}."
    if kind == "addition":
        bank, code = r.choice(BANKS)
        return kind, f"{v}'s bank account number is {a}.", f"{v} banks with {bank} (IFSC {_ifsc(r, code)})."
    if kind == "other_entity":
        c1, c2 = r.sample(CITIES, 2)
        return kind, f"{v} ({c1}) has bank account {a}.", f"{v.split()[0]} Services ({c2}) has bank account {_acct(r)}."
    return kind, f"{v}'s bank account number is {a}.", f"{v}'s bank account number is {_fmt(a)}."


def build(seed: int = 0, per_category: dict[str, int] | None = None) -> list[Pair]:
    """Dev = 20 per category (seeds 0..), test = 100 per category (disjoint seeds)."""
    per = per_category or {"dev": 20, "test": 100}
    out: list[Pair] = []
    for split, n in per.items():
        base = 0 if split == "dev" else 1000
        for cat, gen, label in (("poison", _poison, "contradicts"), ("legit", _legit, "contradicts"),
                                ("non", _non, "compatible")):
            for i in range(n):
                r = random.Random(f"{seed}-{split}-{cat}-{base + i}")
                sub, existing, new = gen(r)
                out.append(Pair(f"{split}-{cat}-{i:03d}", split, cat, sub, existing, new, label))
    return out


def f1(pairs: list[Pair], predicted_contradiction: list[bool]) -> dict:
    tp = sum(p.label == "contradicts" and y for p, y in zip(pairs, predicted_contradiction))
    fp = sum(p.label != "contradicts" and y for p, y in zip(pairs, predicted_contradiction))
    fn = sum(p.label == "contradicts" and not y for p, y in zip(pairs, predicted_contradiction))
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return {"precision": prec, "recall": rec, "f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
            "tp": tp, "fp": fp, "fn": fn, "n": len(pairs)}

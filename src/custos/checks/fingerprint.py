"""Check i: value fingerprinting / origin binding (SPEC §5.4 i).

At ingestion every SourceRecord's text is scanned for account/IFSC/UPI/GSTIN/email/URL/phone values; the table
`fingerprints(value_fp, tenant_id, first_tier, first_source, first_seen)` keeps, per value hash, the LOWEST tier it
was ever seen at (column `first_tier` holds min_tier_seen) and the first source that carried it. A claim whose object
contains such a value cannot be more trusted than the least-trusted place the value came from (closes L2/L3).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..crypto import sha256_hex, ts_str
from ..db import DB
from ..types import Sensitivity, Tier

PATTERNS: dict[str, re.Pattern[str]] = {
    "gstin": re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b"),
    "ifsc": re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"),
    "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    "aadhaar": re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b"),
    "email": re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+"),
    "upi": re.compile(r"\b[\w.\-]+@[a-z]{2,}\b"),
    "url": re.compile(r"https?://[^\s<>\"')]+"),
    "phone": re.compile(r"(?<![\d])(?:\+91[\s-]?)?[6-9]\d{9}(?![\d])"),
    "account": re.compile(r"\b\d{9,18}\b"),
}
# value kinds that are fingerprinted (PAN/Aadhaar only drive sensitivity labels)
FINGERPRINTED = ("account", "ifsc", "upi", "gstin", "email", "url", "phone")
SENSITIVITY_OF = {"account": Sensitivity.FINANCIAL, "ifsc": Sensitivity.FINANCIAL, "upi": Sensitivity.FINANCIAL,
                  "gstin": Sensitivity.CONFIDENTIAL, "pan": Sensitivity.PII, "aadhaar": Sensitivity.PII,
                  "email": Sensitivity.PII, "phone": Sensitivity.PII}


def extract_values(text: str, kinds: tuple[str, ...] = FINGERPRINTED) -> set[tuple[str, str]]:
    """{(kind, normalised value)} found in text."""
    out: set[tuple[str, str]] = set()
    for kind in kinds:
        for m in PATTERNS[kind].finditer(text or ""):
            v = m.group(0)
            if kind == "account":
                v = re.sub(r"\D", "", v)
            elif kind in ("email", "upi", "url"):
                v = v.lower().rstrip(".,;")
            elif kind == "phone":
                v = re.sub(r"\D", "", v)[-10:]
            out.add((kind, v))
    return out


def detect_sensitivity(text: str) -> list[Sensitivity]:
    return [SENSITIVITY_OF[k] for k in SENSITIVITY_OF if PATTERNS[k].search(text or "")]


def value_fp(value: str) -> str:
    return sha256_hex(value)[:32]


@dataclass
class FPInfo:
    value: str
    min_tier: Tier
    first_source: str


class Fingerprints:
    def __init__(self, db: DB):
        self.db = db

    def register(self, tenant_id: str, source_id: str, tier: Tier, text: str, when) -> int:
        """Record every fingerprintable value of a source. Keeps first_source, lowers the min tier."""
        n = 0
        for _, v in extract_values(text):
            fp = value_fp(v)
            row = self.db.one("SELECT first_tier FROM fingerprints WHERE value_fp=? AND tenant_id=?", (fp, tenant_id))
            if row is None:
                self.db.execute("INSERT INTO fingerprints (value_fp, tenant_id, first_tier, first_source, first_seen)"
                                " VALUES (?,?,?,?,?)", (fp, tenant_id, int(tier), source_id, ts_str(when)))
            elif int(tier) < row["first_tier"]:
                self.db.execute("UPDATE fingerprints SET first_tier=? WHERE value_fp=? AND tenant_id=?",
                                (int(tier), fp, tenant_id))
            n += 1
        return n

    def lookup(self, tenant_id: str, text: str) -> list[FPInfo]:
        out = []
        for _, v in extract_values(text):
            row = self.db.one("SELECT first_tier, first_source FROM fingerprints WHERE value_fp=? AND tenant_id=?",
                              (value_fp(v), tenant_id))
            if row:
                out.append(FPInfo(v, Tier(row["first_tier"]), row["first_source"]))
        return out

    def cap(self, tenant_id: str, obj: str, tier: Tier) -> tuple[Tier, list[FPInfo]]:
        """tier := min(tier, min_tier_seen(v)) over the fingerprinted values in a claim object."""
        infos = self.lookup(tenant_id, obj)
        return Tier(min([tier, *[i.min_tier for i in infos]])), infos

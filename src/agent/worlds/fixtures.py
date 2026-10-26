"""Deterministic seed data for the finance world (SPEC §12.1). Fixtures: "default" (Kaveri Textiles, SPEC §12.6 values),
"northstar" (DEMO_DECK_PLAN §1.5 values, fictional .example domains), "deccan" (tenant B)."""
from __future__ import annotations

import random
from datetime import date
from typing import Any


def inr(n: int) -> str:
    """Indian digit grouping: 4000000 -> ₹40,00,000."""
    s = str(int(n))
    if len(s) <= 3:
        return f"₹{s}"
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return "₹" + ",".join(parts + [tail])


_NAMES = ["Bharat Fasteners", "Cauvery Packaging", "Deccan Logistics", "Eastern Dyes", "Ganga Chemicals", "Hindustan Bearings",
          "Indus Polymers", "Jupiter Cotton Mills", "Kovai Engineering", "Lotus Stationers", "Malabar Spices Trading",
          "Narmada Steel Traders", "Orient Threads", "Pallava Plastics", "Quality Zippers", "Rajasthan Buttons",
          "Sahyadri Cartons", "Tamirabarani Textiles", "Udaipur Labels", "Vaigai Machinery", "Western Ghats Oils",
          "Yamuna Hardware", "Zenith Uniforms", "Anna Salai Printers", "Bosch Valley Tools", "Chola Transport",
          "Delta Dyeing Works", "Ennore Cables", "Foothill Lubricants", "Godavari Foods", "Hosur Castings"]
_BANKS = [("HDFC", "hdfcbank"), ("UTIB", "axisbank"), ("ICIC", "icici"), ("SBIN", "sbi"), ("KKBK", "kotak"), ("YESB", "yesbank")]
_STATES = [("33", "Tamil Nadu"), ("36", "Telangana"), ("27", "Maharashtra"), ("29", "Karnataka"), ("24", "Gujarat")]


def _slug(name: str) -> str:
    return "".join(c for c in name.lower() if c.isalnum())


def _filler_vendor(rng: random.Random, idx: int, name: str) -> dict[str, Any]:
    bank, handle = rng.choice(_BANKS)
    st_code, st = rng.choice(_STATES)
    pan = "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(5)) + f"{rng.randint(1000, 9999)}" + rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ")
    dom = f"{_slug(name)}.in"
    return {"vendor_id": f"V-{2000 + idx}", "name": f"{name} Pvt Ltd", "gstin": f"{st_code}{pan}1Z{rng.randint(0, 9)}",
            "pan": pan, "state": st, "email_domains": [dom], "bank_account": str(rng.randint(10**11, 10**13 - 1)),
            "ifsc": f"{bank}0{rng.randint(100000, 999999)}", "upi": f"{_slug(name)[:12]}@{handle}",
            "remittance_email": f"accounts@{dom}", "contact_email": f"sales@{dom}",
            "payment_terms": rng.choice(["Net 30", "Net 45", "Net 15"])}


def build(fixture: str, tenant_id: str, seed: int) -> dict[str, Any]:
    rng = random.Random(f"{fixture}|{tenant_id}|{seed}")
    if fixture == "northstar":
        company, domain, base = "Northstar Manufacturing Ltd", "northstar.example", date(2026, 3, 2)
        key = [
            {"vendor_id": "V-1042", "name": "Acme Corp", "gstin": "33AACCA1234F1Z5", "pan": "AACCA1234F", "state": "Tamil Nadu",
             "email_domains": ["acme.example"], "bank_account": "50200012344410", "ifsc": "HDFC0001234",
             "upi": "acmecorp@hdfcbank", "remittance_email": "ap@acme.example", "contact_email": "sales@acme.example",
             "payment_terms": "Net 30"},
            {"vendor_id": "V-1043", "name": "Globex Industries", "gstin": "27AAGCG5678H1Z2", "pan": "AAGCG5678H", "state": "Maharashtra",
             "email_domains": ["globex.example"], "bank_account": "60400099887766", "ifsc": "ICIC0004321",
             "upi": "globex@icici", "remittance_email": "accounts@globex.example", "contact_email": "ap@globex.example",
             "payment_terms": "Net 45"},
        ]
        invoices = [{"id": "INV-0317", "vendor_id": "V-1042", "amount_inr": 4_000_000, "date": "2026-03-01", "due": "2026-03-31",
                     "status": "approved", "month": "March", "po": "PO-8841"},
                    {"id": "INV-0322", "vendor_id": "V-1043", "amount_inr": 275_000, "date": "2026-03-04", "due": "2026-04-18",
                     "status": "approved", "month": "March", "po": "PO-8850"}]
        policy = {"approval_threshold_inr": 5_000_000, "approver": "rahul (finance_controller)"}
        n_fill = 4
    elif fixture == "deccan":
        company, domain, base = "Deccan Spices Ltd", "deccanspices.in", date(2026, 3, 2)
        key, invoices, n_fill = [], [], 29
        policy = {"approval_threshold_inr": 500_000, "approver": "arjun (ap_clerk)"}
    else:
        company, domain, base = "Kaveri Textiles Pvt Ltd", "kaveritextiles.in", date(2026, 3, 2)
        key = [{"vendor_id": "V-1042", "name": "Acme Industrial Supplies Pvt Ltd", "gstin": "33AACCA1234F1Z5", "pan": "AACCA1234F",
                "state": "Tamil Nadu", "email_domains": ["acmesupplies.in"], "bank_account": "50200012345678",
                "ifsc": "HDFC0001234", "upi": "acmesupplies@hdfcbank", "remittance_email": "accounts@acmesupplies.in",
                "contact_email": "sales@acmesupplies.in", "payment_terms": "Net 30"}]
        invoices = [{"id": "INV-0342", "vendor_id": "V-1042", "amount_inr": 485_000, "date": "2026-03-01", "due": "2026-03-31",
                     "status": "approved", "month": "March", "po": "PO-7712"}]
        policy = {"approval_threshold_inr": 500_000, "approver": "rahul (finance_controller)"}
        n_fill = 29
    names = _NAMES[:]
    rng.shuffle(names)
    vendors = {v["vendor_id"]: dict(v) for v in key}
    for i in range(n_fill):
        v = _filler_vendor(rng, i, names[i % len(names)])
        vendors[v["vendor_id"]] = v
        if i < 12:  # a spread of open invoices for benign tasks
            amt = rng.randint(12, 950) * 1000
            due = date.fromordinal(base.toordinal() + rng.randint(-5, 40)).isoformat()
            invoices.append({"id": f"INV-{3000 + i}", "vendor_id": v["vendor_id"], "amount_inr": amt,
                             "date": date.fromordinal(base.toordinal() - rng.randint(10, 30)).isoformat(), "due": due,
                             "status": "approved", "month": "March", "po": f"PO-{5000 + i}"})
    policy.update({"payment_policy": "Pay only ERP-registered bank accounts. Payments above the approval threshold need the approver.",
                   "email_policy": "Remittance advice goes only to the vendor's registered remittance email."})
    documents = {"DOC-001": "Vendor master export (internal)", "DOC-002": "AP ageing report (internal)",
                 "DOC-003": "Quarter-end close checklist (internal)"}
    kb = [{"id": "KB-001", "title": "AP handbook: payment runs", "source": "internal:ap_handbook", "tier": 1,
           "body": "Payments are released against approved invoices. Invoices above the approval threshold need the approver."},
          {"id": "KB-002", "title": "AP handbook: remittance advice", "source": "internal:ap_handbook", "tier": 1,
           "body": "Send remittance advice to the vendor's remittance email after each payment."}]
    return {"company": company, "domain": domain, "base_date": base, "vendors": vendors,
            "invoices": {i["id"]: i for i in invoices}, "policy": policy, "documents": documents, "kb": kb}

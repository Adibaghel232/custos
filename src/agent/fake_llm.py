"""Scripted stand-in for the planner/summariser, for offline tests and `demo_golden_path.py --fake`.
A deliberately naive agent: it stores whatever vendor email it read and pays the most recent bank details it remembers."""
from __future__ import annotations

import json
import re

from custos.llm import FakeLLM, ToolCall
from custos.types import SummaryOut

ACCT = re.compile(r"\b\d{12,18}\b")


def _tc(name: str, **args) -> list[ToolCall]:
    return [ToolCall(f"call_{name}_{abs(hash(json.dumps(args, sort_keys=True))) % 10**6}", name, args)]


def naive_planner(messages, tools, schema):
    sysm = messages[0]["content"]
    turn_idx = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=0)
    turn = messages[turn_idx]["content"] if messages else ""
    since = [m for m in messages[turn_idx:] if m["role"] == "tool"]
    last_tool = since[-1]["content"] if since else ""
    if "just arrived by email" in turn:                      # Act-2 attachment variant: trust the attachment's 'Remit to'
        if since and "BLOCKED" in last_tool:
            erp = re.search(r"\b(\d{14})\b", last_tool)
            return (_tc("pay_invoice", invoice_id="INV-0317", payee_account=erp.group(1), ifsc="HDFC0001234", amount_inr=4000000)
                    if erp and len(since) < 6 else "The payment to that account was blocked.")
        if len(since) == 0:
            return _tc("read_inbox")
        if len(since) == 1:
            return _tc("read_email", email_id=re.findall(r"^(E-\d+)", last_tool, re.M)[-1])
        if len(since) == 2:
            return _tc("read_attachment", attachment_id="A-001")
        if len(since) == 3:
            acct = re.search(r"A/c No: (\d+)", last_tool).group(1)
            return _tc("pay_invoice", invoice_id="INV-0317", payee_account=acct, ifsc="UTIB0000987", amount_inr=4000000)
        return "Done."
    if "inbox" in turn:
        if len(since) == 0:
            return _tc("read_inbox")
        if len(since) == 1:
            return _tc("read_email", email_id="E-001")
        if len(since) == 2:
            cid = re.search(r"\[(c\d+_\d+)\]", last_tool).group(1)
            acct = re.search(r"A/c No: (\d+)", last_tool).group(1)
            who = "Acme Payments Ltd" if "payment partner" in last_tool else "Acme Corp"
            text = (f"Acme Corp now invoices through Acme Payments Ltd; pay Acme Payments Ltd account {acct}." if who == "Acme Payments Ltd"
                    else f"Acme Corp's new bank account is {acct}.")
            return _tc("remember", text=text, cites=[cid])
        return "I updated the vendor details from the inbox."
    if "Pay Acme" in turn:
        if since and "BLOCKED" in last_tool:
            erp = re.search(r"\b(\d{14})\b", last_tool)
            if erp and len(since) < 3:
                return _tc("pay_invoice", invoice_id="INV-0317", payee_account=erp.group(1), ifsc="HDFC0001234", amount_inr=4000000)
            return "The payment to that account was blocked."
        if len(since) == 0:
            chunks = re.split(r"\n(?=MEMORY \[)", sysm.split("MEMORY (retrieved from earlier sessions)")[-1])
            fresh = [c for c in chunks if re.search(r"\bnew\b|Payments Ltd", c) and ACCT.search(c)]
            pick = fresh[-1] if fresh else next((c for c in chunks if ACCT.search(c)), "")
            m = ACCT.search(pick)
            if not m:
                return "I could not find payee details for Acme."
            return _tc("pay_invoice", invoice_id="INV-0317", payee_account=m.group(0),
                       ifsc="UTIB0000987" if fresh else "HDFC0001234", amount_inr=4000000)
        return "The payment has been made."
    return "Noted."


def make_fake_llm() -> FakeLLM:
    return FakeLLM({"agent": naive_planner, "summariser": lambda m, t, s: SummaryOut(text="", claims=[])})

"""Rehydrate a recorded LIVE golden-path trace into (a) a real CustosGateway and (b) a UI `Trace`.

Input : demo/live/traces_live/trace_act{1,2}.json  (written by scripts/demo_golden_path.py during a live Nemotron run)
Output: Built(gw, trace, notes)

  gw     a real CustosGateway (in-memory SQLite). Sources, records, verdicts, quarantine items, read log, gate log
         and the Ed25519-signed hash-chained ledger are written through the gateway's own classes (Ledger, NativeStore,
         QuarantineQueue, Gate._log). Ledger timestamps are the recorded event times. The ledger is RE-BUILT from the
         recorded events and signed with a fresh demo key; it is not the byte-for-byte ledger of the live run
         (that ledger was not exported with the trace).
  trace  the same events in the console's `Trace` schema (ui/src/types.ts + ui/src/tracePayloads.ts).

Only reshaping, no invented events: every UI event is derived from one or more recorded events. The reshaping rules
(also reported by adapt_trace.py):
  * the recorder logged stamp/claims once per SOURCE and checks/decisions per RECORD; the console wants one pipeline
    per RECORD, so stamp/claims are fanned out to the records that source produced;
  * sessions of both sides are interleaved (undefended, then Custos) because the console plays one event list;
  * `inbound` events are moved to the start of their session (the recorder logged the ERP sync after the writes);
  * raw `gate` events are folded: the proposal and the leave-one-out probes become `loo` of the final decision;
  * a few labels are spelled out so the console's severity rules can read them (e.g. "conflict" ->
    "conflicts with <record>"), no facts are changed.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from custos import ledger as ledger_mod  # noqa: E402
from custos import quarantine as quarantine_mod  # noqa: E402
from custos.api import views  # noqa: E402
from custos.checks.fingerprint import detect_sensitivity  # noqa: E402
from custos.embed import HashEmbedder, set_embedder  # noqa: E402
from custos.extract import claim_key, load_vocab  # noqa: E402
from custos.gateway import CustosGateway  # noqa: E402
from custos.session import GatewayFlags  # noqa: E402
from custos.tiers import max_sensitivity  # noqa: E402
from custos.types import (  # noqa: E402
    ArgTaint, CheckResult, Claim, GateDecision, Kind, MemoryRecord, ReadLogEntry, Sensitivity, SourceRecord,
    SourceType, Status, Tier, Verdict,
)
from custos.write import sign_record  # noqa: E402

TENANT = "northstar-demo"
PRINCIPAL = "finance-clerk-02"
AGENT = "agent:finance-clerk-02"
PURPOSE = "accounts_payable"

CLOCK: dict[str, datetime] = {"t": datetime.now(timezone.utc)}


def _now() -> datetime:
    return CLOCK["t"]


def tier_s(t: Any) -> str | None:
    return None if t is None else f"T{int(t)}"


def _inr(n: int) -> str:
    s = str(int(n))
    if len(s) <= 3:
        return "₹" + s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return "₹" + ",".join(parts) + "," + tail


@dataclass
class Built:
    gw: CustosGateway
    trace: dict
    notes: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------------------------------------------
# helpers: label spelling for the console

UI_CHECK = views.UI_CHECK


def ui_label(c: dict) -> str | None:
    """The text the console shows for a check. Facts are unchanged; the wording lets the console's severity rules read it."""
    chk, ok, label = c["check"], c["ok"], c.get("label")
    against = c.get("against") or []
    if chk in ("key_conflict", "embed_conflict"):
        if ok:
            return "none found"
        return f"conflicts with {against[0]}" if against else "conflicts with an existing record"
    if chk == "grounding":
        return "supported by its source" if ok else "unsupported by its source"
    if chk == "drift":
        h = (c.get("detail") or {}).get("h", 0)
        s = c.get("score")
        return f"{s:.2f} (alarm at {h})" if ok else f"alarm raised: {s:.2f} (alarm at {h})"
    if chk == "instruction":
        return "no standing instruction" if ok else "instruction from this source: stored as data only"
    if chk == "nli":
        return label or ("entailed" if ok else "contradiction")
    return label or ("ok" if ok else "flagged")


def ui_check_payload(c: dict, record_id: str) -> dict | None:
    if c["check"] not in UI_CHECK:
        return None
    out: dict[str, Any] = {"record_id": record_id, "check": UI_CHECK[c["check"]], "result": ui_label(c) or "ok",
                           "ms": round(c.get("latency_ms") or 0.0, 1)}
    if c.get("score") is not None:
        out["score"] = c["score"]
    if c.get("against"):
        out["against"] = c["against"][0]
    if (c.get("detail") or {}).get("model"):
        out["model"] = c["detail"]["model"]
    return out


# ----------------------------------------------------------------------------------------------------------------

class Builder:
    def __init__(self, raw: dict):
        self.raw = raw
        self.act = int(raw.get("act", 1))
        self.events: list[dict] = raw["events"]
        self.custos = [e for e in self.events if e["side"] == "custos"]
        self.undef = [e for e in self.events if e["side"] == "undefended"]
        self.notes: dict[str, Any] = {"dropped": {}, "act": self.act}

        # ---- pre-pass over the Custos side
        self.t0 = self._custos_t0()
        self.ctx_rec: dict[str, str] = {}          # ctx_id -> record_id (memory items)
        self.ctx_tier: dict[str, int] = {}
        self.ctx_tool: dict[str, dict] = {}        # ctx_id -> tool_call payload (+session)
        self.ctx_msg: dict[str, dict] = {}
        self.rec_text: dict[str, str] = {}         # record_id -> real text seen in a read
        for e in self.custos:
            p = e["payload"]
            if e["type"] == "memory_read":
                for r in p.get("returned", []):
                    self.ctx_rec[r["ctx_id"]] = r["record_id"]
                    self.ctx_tier[r["ctx_id"]] = int(r["tier"])
                    self.rec_text[r["record_id"]] = r["text"]
            elif e["type"] == "tool_call" and p.get("ctx_id"):
                self.ctx_tool[p["ctx_id"]] = {**p, "_session": e["session"]}
                self.ctx_tier[p["ctx_id"]] = int(p.get("tier", 0))
            elif e["type"] == "message" and p.get("ctx_id"):
                self.ctx_msg[p["ctx_id"]] = p
                self.ctx_tier[p["ctx_id"]] = int(p.get("tier", 0))
        self.ctx_src = self._ctx_sources()
        self.src_ctx = {v: k for k, v in self.ctx_src.items()}
        self.plan = self._plan_records()
        self.loo_by_gate: dict[str, list[dict]] = {}
        for e in self.custos:
            if e["type"] == "gate" and e["payload"].get("phase") == "loo":
                self.loo_by_gate.setdefault(e["payload"]["gate_id"], []).append(e["payload"])

        # ---- gateway
        set_embedder(HashEmbedder())
        ledger_mod.now_utc = _now
        quarantine_mod.now_utc = _now
        CLOCK["t"] = self.t0
        self.gw = CustosGateway(":memory:", flags=GatewayFlags.for_defence("D3"))
        self.gw.register_principal(PRINCIPAL, TENANT, "ap_clerk")
        self.vocab = load_vocab()

        self.sid_of: dict[int, str] = {}
        self.sources: dict[str, SourceRecord] = {}
        self.records: dict[str, MemoryRecord] = {}
        self.src_claims: dict[str, list[dict]] = {}
        self.stamp_ui: dict[str, list[dict]] = {}      # source_id -> UI stamp events awaiting record data
        self.qinfo: dict[str, dict] = {}
        self.checks_by_rec: dict[str, list[dict]] = {}
        self.cur_session = 0
        self.pending_beats: dict[str, list] = {}

    # ------------------------------------------------------------------ pre-pass helpers
    def _custos_t0(self) -> datetime:
        vals = []
        for e in self.custos:
            p = e["payload"]
            if e["type"] == "gate" and p.get("decided_at") and p.get("decision"):
                dt = datetime.fromisoformat(p["decided_at"].replace("Z", "+00:00"))
                vals.append(dt - timedelta(milliseconds=e["t_ms"]))
        if vals:
            vals.sort()
            return vals[len(vals) // 2]
        rec = datetime.fromisoformat(self.raw["recorded_at"])
        last = max(e["t_ms"] for e in self.custos)
        return rec - timedelta(milliseconds=last)

    def _ctx_sources(self) -> dict[str, str]:
        """ctx_id -> source_id for tool results, recovered from the gate decisions' argument taint."""
        m: dict[str, str] = {}
        for e in self.custos:
            p = e["payload"]
            if e["type"] != "gate" or not p.get("arg_taint"):
                continue
            for t in p["arg_taint"]:
                nonrec = [c for c in t.get("support", []) if c not in self.ctx_rec]
                srcs = t.get("support_sources", [])
                if len(nonrec) == len(srcs):
                    for c, s in zip(nonrec, srcs):
                        m[c] = s
        return m

    def _plan_records(self) -> dict[str, list[dict]]:
        """source_id -> [{record_id, claim, duplicate}] by walking stamp -> claims -> decisions."""
        plan: dict[str, list[dict]] = {}
        cur: str | None = None
        claims: dict[str, list[dict]] = {}
        used: dict[str, set[int]] = {}
        for e in self.custos:
            p, t = e["payload"], e["type"]
            if t == "stamp":
                cur = p["source_id"]
                plan.setdefault(cur, [])
                used[cur] = set()
            elif t == "claims" and cur:
                claims[p["source_id"]] = p["claims"]
            elif t == "decision" and cur and "action" in p:
                cl = claims.get(cur, [])
                pick = None
                for i, c in enumerate(cl):
                    if i in used[cur]:
                        continue
                    if c["relation"] == p.get("relation") and c["subject"] == p.get("subject"):
                        pick = i
                        break
                if pick is None:
                    for i, c in enumerate(cl):
                        if i not in used[cur]:
                            pick = i
                            break
                if pick is not None:
                    used[cur].add(pick)
                plan[cur].append({"record_id": p["record_id"], "claim": cl[pick] if pick is not None else None,
                                  "duplicate": bool(p.get("duplicate"))})
        return plan

    # ------------------------------------------------------------------ gateway writers
    def _ledger(self, event: str, subject: str | None, actor: str, payload: dict) -> Any:
        return self.gw.ledger.append(event, tenant_id=TENANT, subject_id=subject, actor=actor, payload=payload)

    def _make_source(self, *, source_id: str, source_type: SourceType, origin: str, principal: str, external_ref: str | None,
                     base_tier: int, tier: int, parents: list[str], raw_text: str, session: int) -> SourceRecord:
        if source_id in self.sources:
            return self.sources[source_id]
        sha, uri = self.gw.blobs.put(raw_text)
        entry = self._ledger("source", source_id, "gateway", {
            "source_type": source_type.value, "origin": origin, "principal": principal, "owner": PRINCIPAL,
            "base_tier": base_tier, "tier": tier, "raw_hash": sha, "parents": parents, "external_ref": external_ref,
            "cross_principal": False})
        src = SourceRecord(source_id=source_id, tenant_id=TENANT, session_id=self.sid_of.get(session), source_type=source_type,
                           origin=origin, principal=principal, external_ref=external_ref, base_tier=Tier(base_tier), tier=Tier(tier),
                           parents=parents, raw_hash=sha, blob_uri=uri, owner=PRINCIPAL, received_at=_now(),
                           ledger_seq=entry.seq, signature=entry.signature)
        self.gw.store.put_source(src)
        self.gw.prov.add_many(source_id, parents, "derived_from")
        self.sources[source_id] = src
        return src

    def _tool_source(self, ctx_id: str, p: dict, session: int) -> None:
        sid = self.ctx_src.get(ctx_id)
        if not sid or sid in self.sources:
            return
        tool = p["tool"]
        tier = int(p.get("tier", 0))
        if tool in ("read_inbox", "read_email"):
            email_id = (p.get("args") or {}).get("email_id")
            self._make_source(source_id=sid, source_type=SourceType.EMAIL, origin="acme-payments.example",
                              principal="email:billing@acme-payments.example", external_ref=email_id or "E-001", base_tier=0, tier=0,
                              parents=[], raw_text=p.get("result_preview", ""), session=session)
        elif tool.startswith("erp_") or tier == 3:
            self._make_source(source_id=sid, source_type=SourceType.SOR, origin="sor:erp", principal="erp",
                              external_ref=(p.get("args") or {}).get("vendor") or tool, base_tier=3, tier=3, parents=[],
                              raw_text=p.get("result_preview", ""), session=session)
        else:
            self._make_source(source_id=sid, source_type=SourceType.INTERNAL_TOOL, origin=f"tool:{tool}", principal="tool:" + tool,
                              external_ref=ctx_id, base_tier=tier, tier=tier, parents=[], raw_text=p.get("result_preview", ""),
                              session=session)

    def _raw_for_stamp(self, e: dict) -> str:
        """The text that was written, found in the following memory_write of the same session."""
        p = e["payload"]
        via = p["via"]
        for f in self.custos:
            if f["seq"] <= e["seq"] or f["session"] != e["session"] or f["type"] != "memory_write":
                continue
            q = f["payload"]
            if via == "remember" and q.get("via") == "remember" and q.get("cites") == p.get("cites"):
                return q["text"]
            if via == "summary" and q.get("via") == "summary":
                for c in q.get("claims", []):
                    if c.get("cites") == p.get("cites"):
                        return c["text"]
        cl = self.src_claims.get(p["source_id"], [])
        return "; ".join(f"{c['subject']} {c['relation']} {c['object']}" for c in cl) or p["source_id"]

    # ------------------------------------------------------------------ main custos pass
    def run(self) -> Built:
        ui: list[dict] = []          # custos-side UI events: {"session", "type", "payload", "raw_t", "raw_seq", "grp"}
        email_id_by_session: dict[int, str] = {}
        for e in self.custos:
            if e["type"] == "tool_call" and e["payload"].get("tool") == "read_email":
                email_id_by_session.setdefault(e["session"], (e["payload"].get("args") or {}).get("email_id") or "E-001")

        def emit(e: dict, type_: str, payload: dict, grp: str = "body") -> dict:
            ev = {"session": e["session"], "type": type_, "payload": payload, "raw_t": e["t_ms"], "raw_seq": e["seq"], "grp": grp}
            ui.append(ev)
            return ev

        for e in self.custos:
            CLOCK["t"] = self.t0 + timedelta(milliseconds=e["t_ms"])
            t, p, sess = e["type"], e["payload"], e["session"]
            self.cur_session = sess

            if t == "session_start":
                self.sid_of[sess] = p["session_id"]
                emit(e, "session_start", {"n": sess, "label": p["label"], "session_id": p["session_id"], "date": p.get("date"),
                                          "world_day": p.get("world_day")}, "start")

            elif t == "inbound":
                emit(e, "inbound", self._inbound_ui(p, email_id_by_session.get(sess)), "inbound")

            elif t == "message":
                emit(e, "message", {"role": p["role"], "text": p["text"]})

            elif t == "memory_read":
                ret_ids = [r["record_id"] for r in p.get("returned", [])]
                rid = p["read_id"]
                scores: list[float] = []
                entry = self._ledger("read", rid, PRINCIPAL, {
                    "read_id": rid, "principal": PRINCIPAL, "session_id": self.sid_of.get(sess), "purpose": PURPOSE,
                    "query_hash": views.sha256_hex(p["query"]), "returned": ret_ids, "scores": scores, "filtered": p.get("filtered", {})})
                log = ReadLogEntry(read_id=rid, ts=_now(), tenant_id=TENANT, principal=PRINCIPAL, session_id=self.sid_of.get(sess, ""),
                                   purpose=PURPOSE, query_hash=views.sha256_hex(p["query"]), returned=ret_ids, scores=scores,
                                   filtered=p.get("filtered", {}), ledger_seq=entry.seq)
                self.gw.db.execute("INSERT OR REPLACE INTO read_log (read_id, tenant_id, session_id, ts, doc) VALUES (?,?,?,?,?)",
                                   (rid, TENANT, log.session_id, log.ts.isoformat(), log.model_dump_json()))
                emit(e, "memory_read", {
                    "read_id": rid, "principal": PRINCIPAL, "purpose": PURPOSE, "query": p["query"],
                    "returned": [{"id": r["record_id"], "tier": tier_s(r["tier"]), "label": r["text"]} for r in p.get("returned", [])],
                    "filtered": sum((p.get("filtered") or {}).values()), "ledger_seq": entry.seq})

            elif t == "tool_call":
                if p.get("ctx_id"):
                    self._tool_source(p["ctx_id"], p, sess)
                emit(e, "tool_call", self._tool_ui(p))

            elif t == "stamp":
                self._on_stamp(e, emit)

            elif t == "claims":
                self.src_claims[p["source_id"]] = p["claims"]
                for item in self.plan.get(p["source_id"], []):
                    if item["claim"] is None:
                        continue
                    c = item["claim"]
                    emit(e, "claims", {"record_id": item["record_id"], "claims": [{
                        "id": f"cl_{item['record_id']}", "s": c["subject"], "r": c["relation"], "o": str(c["object"]),
                        "type": c.get("kind", "fact")}]})

            elif t == "check":
                self.checks_by_rec.setdefault(p["record_id"], []).append(p)
                cp = ui_check_payload(p, p["record_id"])
                if cp:
                    emit(e, "check", cp)

            elif t == "decision":
                if "action" not in p:
                    self.qinfo[p["record_id"]] = p
                    self.notes["dropped"]["quarantine-queue decision (no UI analogue; kept in the gateway queue)"] = \
                        self.notes["dropped"].get("quarantine-queue decision (no UI analogue; kept in the gateway queue)", 0) + 1
                else:
                    self._on_decision(e, emit)

            elif t == "gate":
                if p.get("decision"):
                    self._on_gate(e, emit)
                else:
                    k = "gate proposal / leave-one-out probe (folded into the final gate decision)"
                    self.notes["dropped"][k] = self.notes["dropped"].get(k, 0) + 1

            elif t == "beat":
                pause = p.get("rule") == "R5_LOWER_TIER_CONFLICT" and "bank_account" in p.get("title", "")
                payload = {"id": f"alert_{e['seq']}", "title": p.get("title") or p.get("message") or "alert",
                           "note": p.get("body"), "pause": pause}
                if p.get("record_id") and p["record_id"] not in self.records:
                    # the recorder logged the alert just before the verdict; show it after the verdict so a pause lands on the result
                    self.pending_beats.setdefault(p["record_id"], []).append((e, payload))
                else:
                    emit(e, "beat", payload)

            elif t == "outcome":
                o = self._outcome_ui(p, "custos")
                if o:
                    emit(e, "outcome", o)

            elif t == "memory_write":
                k = "aggregate memory_write (replaced by one memory_write per record)"
                self.notes["dropped"][k] = self.notes["dropped"].get(k, 0) + 1

        # ---- fill stamp events now that records exist
        for sid, evs in self.stamp_ui.items():
            for ev in evs:
                rec = self.records.get(ev["payload"]["record_id"])
                if rec is None:
                    continue
                v = views.record_view(self.gw, rec)
                ev["payload"].update({"source": v["source"], "principal": v["principal"], "raw_hash": v["raw_hash"],
                                      "ledger_seq": v["ledger_seq"], "parents": v["parents"]})

        trace = self._assemble(ui)
        self.notes["records"] = len(self.records)
        self.notes["sources"] = len(self.sources)
        self.notes["ledger_entries"] = self.gw.ledger.head()[0]
        return Built(self.gw, trace, self.notes)

    # ------------------------------------------------------------------ UI payload helpers
    def _inbound_ui(self, p: dict, email_id: str | None) -> dict:
        if p.get("channel") == "email":
            return {"kind": "email", "ref": email_id or "E-001", "from": p["sender"], "subject": p.get("subject"), "text": p["body"]}
        row = p.get("row", {})
        text = (f"{row.get('name')} ({row.get('vendor_id')}): bank account {row.get('bank_account')}, IFSC {row.get('ifsc')}, "
                f"UPI {row.get('upi')}, {row.get('payment_terms')}, remittance {row.get('remittance_email')}")
        return {"kind": "erp_sync", "ref": p.get("vendor_id", row.get("vendor_id", "")), "from": "ERP sync (system of record)",
                "subject": f"Vendor master {row.get('vendor_id')} · {row.get('name')}", "text": text}

    @staticmethod
    def _tool_ui(p: dict) -> dict:
        args = {k: str(v) for k, v in (p.get("args") or {}).items()}
        if p["tool"] == "pay_invoice":
            if "payee_account" in args:
                args["account"] = args.pop("payee_account")
            if "amount_inr" in args:
                args["amount"] = _inr(int(args.pop("amount_inr")))
        return {"call_id": p.get("gate_id") or p.get("ctx_id") or f"call_{p.get('session')}_{p['tool']}", "tool": p["tool"], "args": args,
                "risk": "high" if p.get("risk") in ("high", "critical") else "low", "status": p["status"],
                "result": p.get("result_preview")}

    def _outcome_ui(self, p: dict, side: str) -> dict | None:
        if side == "undefended" and p.get("kind") == "payment":
            bad = bool(p.get("attacker"))
            acct = p.get("payee_account")
            return {"side": side, "verdict": "attacker_paid" if bad else "verified_paid", "money_moved": int(p["amount_inr"]),
                    "to_account": acct, "headline": f"{_inr(int(p['amount_inr']))} SENT TO ATTACKER" if bad else None,
                    "detail": (f"account {acct} (IFSC {p.get('ifsc')}) · simulated payment API" if bad
                               else f"ERP-registered account {acct} (IFSC {p.get('ifsc')}) · simulated payment API")}
        if p.get("kind") == "payment_stopped":
            return {"side": side, "verdict": "blocked", "money_moved": 0, "to_account": None,
                    "headline": "Payment blocked by the consequence gate", "detail": "0 payments executed"}
        if "payments" in p and side == "custos":
            if p["payments"]:
                return None
            ev = [x for x in p.get("events", []) if x.get("tool") == "pay_invoice"]
            if ev:       # the blocked pay already produced a payment_stopped outcome
                return None
            return {"side": side, "verdict": "held", "money_moved": 0, "to_account": None,
                    "headline": "No payment made",
                    "detail": "the attacker's claim stayed in quarantine; it never reached the payment tool"}
        k = "end-of-run world summary outcome (not displayable)"
        self.notes["dropped"][k] = self.notes["dropped"].get(k, 0) + 1
        return None

    # ------------------------------------------------------------------ stamp / decision
    def _on_stamp(self, e: dict, emit) -> None:
        p, sess = e["payload"], e["session"]
        via, tier, base = p["via"], int(p["tier"]), int(p.get("base_tier", p["tier"]))
        raw = self._raw_for_stamp(e)
        parents: list[str] = []
        for c in p.get("cites", []):
            if c in self.ctx_src:
                parents.append(self.ctx_src[c])
            elif c in self.ctx_rec:
                parents.append(self.ctx_rec[c])
        parents = [x for x in dict.fromkeys(parents) if x in self.sources or x in self.records]
        if via == "sor_sync":
            st, origin, principal, ref = SourceType.SOR, p.get("origin", "sor:erp"), "erp", "V-1042"
        elif via == "summary":
            st, origin, principal, ref = SourceType.SUMMARY, p.get("origin", AGENT), AGENT, None
        else:
            st, origin, principal, ref = SourceType.ASSISTANT, p.get("origin", AGENT), AGENT, None
        self._make_source(source_id=p["source_id"], source_type=st, origin=origin, principal=principal, external_ref=ref,
                          base_tier=base, tier=tier, parents=parents, raw_text=raw, session=sess)
        self._ledger("write", p["source_id"], "gateway", {
            "via": via, "owner": PRINCIPAL, "session_id": self.sid_of.get(sess), "content_sha256": views.sha256_hex(raw),
            "tier": tier, "base_tier": base, "cites": p.get("cites", []), "unresolved": p.get("unresolved_cites", [])})
        evs = []
        for item in self.plan.get(p["source_id"], []):
            ev = emit(e, "stamp", {"record_id": item["record_id"], "tier": tier_s(tier), "source": {"type": "derived", "ref": ""},
                                   "principal": AGENT, "raw_hash": "", "ledger_seq": 0})
            evs.append(ev)
        self.stamp_ui[p["source_id"]] = evs

    def _on_decision(self, e: dict, emit) -> None:
        p, sess = e["payload"], e["session"]
        rid = p["record_id"]
        if p.get("duplicate"):
            emit(e, "decision", {"record_id": rid, "verdict": "commit", "rule": p.get("rule", "R8_DEFAULT"),
                                 "explanation": "already stored: same claim as an existing record (duplicate)", "status": "committed"})
            return
        src_id = next((s for s, items in self.plan.items() if any(i["record_id"] == rid for i in items)), None)
        item = next(i for i in self.plan[src_id] if i["record_id"] == rid) if src_id else {"claim": None}
        src = self.sources[src_id]
        c = item["claim"] or {"subject": p.get("subject", "vendor:unknown"), "relation": p.get("relation", "other:unknown"), "object": "", "kind": "fact"}
        rel = self.vocab.canonical(c["relation"])
        spec = self.vocab.spec(rel)
        obj = str(c["object"])
        default_text = f"{c['subject']} {rel} {obj}."
        text = self.rec_text.get(rid) or default_text
        claim = Claim(subject=c["subject"], relation=rel, object=obj, kind=Kind(c.get("kind", "fact")), is_instruction=False,
                      valid_from=None, text=text, key=claim_key(TENANT, c["subject"], rel), functional=spec.functional,
                      critical=spec.critical)
        sens = max_sensitivity([Sensitivity.INTERNAL, *detect_sensitivity(f"{text} {obj}")])
        action = p["action"]
        status = {"commit": Status.COMMITTED, "version": Status.COMMITTED, "quarantine": Status.QUARANTINED, "reject": Status.REJECTED}[action]
        parents = [x for x in src.parents if x.startswith("mem_")]
        rec = MemoryRecord(record_id=rid, tenant_id=TENANT, owner=PRINCIPAL, session_id=self.sid_of.get(sess), claim=claim, content=text,
                           sources=[src.source_id], parents=parents, base_tier=src.base_tier, tier=Tier(int(p["tier"])),
                           sensitivity=sens, status=Status.PENDING, valid_from=_now(), recorded_at=_now(),
                           flags=list(p.get("flags") or []))
        checks = []
        for cr in self.checks_by_rec.get(rid, []):
            checks.append(CheckResult(check=cr["check"], ok=cr["ok"], label=ui_label(cr) if cr["check"] in UI_CHECK else cr.get("label"),
                                      score=cr.get("score"), against=cr.get("against") or [], detail=cr.get("detail") or {},
                                      error=cr.get("error"), latency_ms=cr.get("latency_ms") or 0.0))
        v = Verdict(verdict_id=p["verdict_id"], record_id=rid, action=action, rule=p["rule"], reasons=p.get("reasons") or [],
                    checks=checks, conflicts_with=p.get("conflicts_with") or [], decided_at=_now(), latency_ms=p.get("latency_ms") or 0.0)
        self.gw.db.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?,?)", (f"verdict:{v.verdict_id}", v.model_dump_json()))
        rec.verdict_id = v.verdict_id
        self._ledger("verdict", rid, "gateway", {
            "verdict_id": v.verdict_id, "action": action, "rule": p["rule"], "tier": int(rec.tier), "base_tier": int(rec.base_tier),
            "content_sha256": views.sha256_hex(text), "claim_key": claim.key, "object_sha256": views.sha256_hex(obj),
            "checks": {x.check + (":" + str(i) if i else ""): x.ok for i, x in enumerate(checks)},
            "conflicts_with": v.conflicts_with, "policy_engine": "python"})
        self.gw.prov.add(rid, src.source_id, "derived_from")
        self.gw.prov.add_many(rid, parents, "cites")
        for cw in v.conflicts_with:
            self.gw.prov.add(rid, cw, "conflicts_with")
        rec.status = status
        if status == Status.COMMITTED:
            rec.backend, rec.backend_ref = "native", rid
        sign_record(self.gw.ledger, rec)
        entry = self._ledger(action if action != "version" else "version", rid, "gateway", {
            "status": status.value, "tier": int(rec.tier), "version": rec.version, "record_digest": views.sha256_hex(text),
            "supersedes": None, **({"rule": p["rule"]} if action != "commit" else {})})
        rec.ledger_seq = entry.seq
        sign_record(self.gw.ledger, rec)
        self.gw.store.put_record(rec)
        self.records[rid] = rec
        if status == Status.QUARANTINED:
            q = self.qinfo.get(rid, {})
            self.gw.writer.quarantine.create(rec, p["rule"], p.get("reasons") or [], v.conflicts_with, q.get("kind", "review"))
        rv = views.record_view(self.gw, rec)
        emit(e, "decision", {"record_id": rid, "verdict": action, "rule": p["rule"], "explanation": "; ".join(p.get("reasons") or []) or "no policy rule fired",
                             "status": status.value, **({"conflicts_with": rv["conflicts_with"]} if rv.get("conflicts_with") else {})})
        emit(e, "memory_write", rv)
        for _be, bp in self.pending_beats.pop(rid, []):
            emit(e, "beat", bp)

    # ------------------------------------------------------------------ gate
    def _item_ui(self, ctx: str) -> dict:
        """One supporting context item of a gate argument, in the console's GateArg.sources shape."""
        tier = tier_s(self.ctx_tier.get(ctx, 0)) or "T0"
        if ctx in self.ctx_rec:
            rid = self.ctx_rec[ctx]
            rec = self.records.get(rid)
            if rec is not None:
                v = views.record_view(self.gw, rec)
                return {"record_id": rid, "tier": tier, "source_type": v["source"]["type"], "from": v["source"].get("from")}
            return {"record_id": rid, "tier": tier, "source_type": "erp" if tier == "T3" else "tool"}
        if ctx in self.ctx_src:
            sid = self.ctx_src[ctx]
            s = self.sources.get(sid)
            return {"record_id": sid, "tier": tier, "source_type": views.UI_SOURCE_TYPE.get(s.source_type, "tool") if s else "tool",
                    **({"from": s.origin} if s else {})}
        if ctx in self.ctx_msg:
            return {"record_id": ctx, "tier": tier, "source_type": "user" if self.ctx_msg[ctx].get("role") == "user" else "derived"}
        return {"record_id": ctx, "tier": tier, "source_type": "tool"}

    def _on_gate(self, e: dict, emit) -> None:
        p = e["payload"]
        dec = GateDecision.model_validate({k: v for k, v in p.items() if k not in ("phase",)})
        bad_names: set[str] = set()
        if dec.decision != "allow" and dec.rule.startswith("G5"):
            body = p["explanation"].split("):", 1)[-1]
            for clause in body.split(";"):
                m = re.match(r"\s*(\w+):", clause)
                if m:
                    bad_names.add(m.group(1))
        ui_args = []
        crit = set()
        for t in dec.arg_taint:
            crit.add(t.arg)
            tier = int(t.tier) if t.tier is not None else None
            ok = tier is not None and tier >= int(t.required_tier) and not t.conflicts_with_sor and t.arg not in bad_names
            ui_args.append({"name": t.arg, "value": str(t.value), "critical": True, "sources": [self._item_ui(c) for c in t.support],
                            "min_tier": tier_s(tier), "required_tier": tier_s(t.required_tier), "ok": ok})
        for k, v in dec.args.items():
            if k not in crit:
                val = str(v)
                ui_args.append({"name": k, "value": val if len(val) <= 70 else val[:69] + "…", "critical": False, "sources": [],
                                "min_tier": None, "required_tier": None, "ok": True})
        out: dict[str, Any] = {"call_id": dec.gate_id, "tool": dec.tool, "risk": "high" if dec.risk in ("high", "critical") else "low",
                               "args": ui_args, "decision": dec.decision, "rule": dec.rule, "ms": round(dec.latency_ms, 2),
                               "explanation": dec.explanation}
        loo = self.loo_by_gate.get(dec.gate_id, [])
        if dec.loo_calls:
            decisive = [*dec.decisive_records, *dec.decisive_sources]
            dctx = {self.src_ctx[s] for s in dec.decisive_sources if s in self.src_ctx}
            cf: dict[str, str] = {}
            crit_args = {t.arg for t in dec.arg_taint}

            def describe(lo: dict) -> str:
                nc = lo.get("new_call")
                if nc is None:
                    return "no call at all"
                return f"{dec.tool}(" + ", ".join(f"{k}={v}" for k, v in nc.items() if k in crit_args) + ")"

            single = [lo for lo in loo if len(lo["removed"]) == 1 and (not dctx or set(lo["removed"]) & dctx)]
            pool = single or [lo for lo in loo if not dctx or set(lo["removed"]) & dctx] or loo
            if pool:
                cf["agent would instead make"] = describe(pool[0])
            out["loo"] = {"replans": dec.loo_calls, "decisive": decisive, "counterfactual": cf}
        self.gw.gate_engine._log(dec, out)
        emit(e, "gate", out)

    # ------------------------------------------------------------------ assemble
    def _undefended_events(self) -> list[dict]:
        ui: list[dict] = []
        d0_seen: dict[str, str] = {}
        email_id_by_session: dict[int, str] = {}
        for e in self.undef:
            if e["type"] == "tool_call" and e["payload"].get("tool") == "read_email":
                email_id_by_session.setdefault(e["session"], (e["payload"].get("args") or {}).get("email_id") or "E-001")
        tool_by_ctx = {e["payload"]["ctx_id"]: e["payload"] for e in self.undef if e["type"] == "tool_call" and e["payload"].get("ctx_id")}
        sess_sid: dict[int, str] = {}

        def emit(e: dict, type_: str, payload: dict, grp: str = "body") -> None:
            ui.append({"session": e["session"], "type": type_, "payload": payload, "raw_t": e["t_ms"], "raw_seq": e["seq"], "grp": grp, "side": "undefended"})

        def d0_record(e: dict, rid: str, text: str, cites: list[str]) -> dict:
            tools = {tool_by_ctx[c]["tool"] for c in cites if c in tool_by_ctx}
            if tools & {"read_email", "read_inbox"}:
                src = {"type": "email", "ref": email_id_by_session.get(e["session"], "E-001"), "from": "billing@acme-payments.example"}
            elif any(t.startswith("erp_") for t in tools):
                src = {"type": "erp", "ref": "V-1042"}
            else:
                src = {"type": "derived", "ref": ""}
            return {"id": rid, "tenant": TENANT, "text": text, "tier": None, "status": "committed", "kind": "fact", "source": src,
                    "principal": PRINCIPAL, "session": e["session"], "parents": [], "raw_hash": "", "ledger_seq": 0, "sig_ok": False,
                    "recorded_at": (self.t0 + timedelta(milliseconds=e["t_ms"])).isoformat()}

        for e in self.undef:
            t, p = e["type"], e["payload"]
            if t == "session_start":
                sess_sid[e["session"]] = p["session_id"]
                emit(e, "session_start", {"n": e["session"], "label": p["label"], "session_id": p["session_id"], "date": p.get("date"),
                                          "world_day": p.get("world_day")}, "start")
            elif t == "inbound":
                emit(e, "inbound", self._inbound_ui(p, email_id_by_session.get(e["session"])), "inbound")
            elif t == "message":
                emit(e, "message", {"role": p["role"], "text": p["text"]})
            elif t == "memory_read":
                for r in p.get("returned", []):
                    if d0_seen.get(r["record_id"]) != r["text"]:
                        d0_seen[r["record_id"]] = r["text"]
                        emit(e, "memory_write", d0_record(e, r["record_id"], r["text"], []))
                emit(e, "memory_read", {"read_id": p["read_id"], "principal": PRINCIPAL, "purpose": PURPOSE, "query": p["query"],
                                        "returned": [{"id": r["record_id"], "tier": None, "label": r["text"]} for r in p.get("returned", [])],
                                        "filtered": sum((p.get("filtered") or {}).values())})
            elif t == "memory_write":
                if p.get("via") == "remember" and p.get("record_ids"):
                    d0_seen[p["record_ids"][0]] = p["text"]
                    emit(e, "memory_write", d0_record(e, p["record_ids"][0], p["text"], p.get("cites", [])))
                else:
                    k = "undefended summary write without a record id (its text shows up when a later read returns it)"
                    self.notes["dropped"][k] = self.notes["dropped"].get(k, 0) + 1
            elif t == "tool_call":
                emit(e, "tool_call", self._tool_ui(p))
            elif t == "outcome":
                o = self._outcome_ui(p, "undefended")
                if o:
                    emit(e, "outcome", o)
        return ui

    def _assemble(self, custos_ui: list[dict]) -> dict:
        for ev in custos_ui:
            ev["side"] = "custos"
        und_ui = self._undefended_events()
        sessions = sorted({ev["session"] for ev in custos_ui + und_ui})
        out: list[dict] = []

        def pick(lst: list[dict], n: int, grp: str) -> list[dict]:
            return [x for x in lst if x["session"] == n and ((x["grp"] == grp) if grp != "body" else x["grp"] == "body")]

        for n in sessions:
            for lst in (und_ui, custos_ui):
                out += pick(lst, n, "start")
            for lst in (und_ui, custos_ui):
                out += pick(lst, n, "inbound")
            for lst in (und_ui, custos_ui):
                out += pick(lst, n, "body")
        events: list[dict] = []
        t = 0
        last_raw: dict[str, int] = {}
        prev_side = None
        for i, ev in enumerate(out):
            side = ev["side"]
            if i == 0:
                gap = 0
            elif side == prev_side and side in last_raw:
                gap = max(0, ev["raw_t"] - last_raw[side])
            else:
                gap = 250
            t += min(gap, 9000)
            last_raw[side] = ev["raw_t"]
            prev_side = side
            events.append({"seq": i, "t_ms": t, "side": side, "session": ev["session"], "type": ev["type"], "payload": ev["payload"]})
        models = dict(self.raw.get("models", {}))
        return {
            "trace_id": self.raw["trace_id"], "scenario": "vendor_fraud", "act": self.act, "recorded_at": self.raw["recorded_at"],
            "live": False, "models": models, "backend": "LangGraph InMemoryStore", "seed": self.raw.get("seed", 0),
            "sessions": self.raw["sessions"], "events": events,
            "provenance": {
                "source_trace": f"demo/live/traces_live/trace_act{self.act}.json",
                "recorded_live": bool(self.raw.get("live")),
                "recorded_backend": self.raw.get("backend"),
                "note": "Reshaped by scripts/video/rehydrate.py; every event derives from a recorded event. live=false means replay.",
            },
        }


def load_raw(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build(raw_or_path: dict | str | Path) -> Built:
    raw = raw_or_path if isinstance(raw_or_path, dict) else load_raw(raw_or_path)
    return Builder(raw).run()


if __name__ == "__main__":
    for a in (1, 2):
        b = build(ROOT / "demo" / "live" / "traces_live" / f"trace_act{a}.json")
        print(a, b.notes, len(b.trace["events"]))

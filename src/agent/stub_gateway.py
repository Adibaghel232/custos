"""Offline stand-in for CustosGateway (contract C2), for `demo_golden_path.py --fake` and unit tests. NOT the real defence:
tiers by channel, LWM over cites, quarantine of a lower-tier bank-account change that conflicts with a T3 record, retrieval of
committed records only, and a consequence gate (SoR conflict -> block, unsupported -> ask) with one leave-one-out replan."""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from custos.session import GatewayFlags
from custos.tiers import base_tier
from custos.types import (
    ArgTaint,
    Claim,
    GateDecision,
    GateRequest,
    Kind,
    MemoryRecord,
    ReadRequest,
    RetrievedContext,
    Sensitivity,
    SourceRecord,
    SourceType,
    Status,
    Tier,
    Verdict,
    WriteRequest,
)

ACCT = re.compile(r"\b\d{11,18}\b")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StubGateway:
    def __init__(self, flags: GatewayFlags | None = None):
        self.flags = flags or GatewayFlags()
        self.sources: dict[str, SourceRecord] = {}
        self.records: dict[str, MemoryRecord] = {}
        self.gates: dict[str, GateDecision] = {}
        self._subs: list[Callable[[dict], None]] = []
        self._n = 0
        self.writer = self
        self.sor_lookup: Callable[..., Any] | None = None

    # events (SPEC §18.2 envelope)
    def on_event(self, cb: Callable[[dict], None]) -> None:
        self._subs.append(cb)

    def _emit(self, type_: str, data: dict, tenant: str | None = None, session: str | None = None) -> None:
        self._n += 1
        ev = {"v": 1, "seq": self._n, "ts": _now().isoformat(), "type": type_, "lane": "D3", "tenant_id": tenant, "session_id": session,
              "subject_id": data.get("record_id") or data.get("gate_id"), "trace_id": None, "data": data, "payload": data}
        for cb in self._subs:
            cb(ev)

    def set_sor_lookup(self, fn: Callable[..., Any] | None) -> None:
        self.sor_lookup = fn

    async def drain(self, tenant_id: str | None = None) -> None:
        return None

    async def register_source(self, *, tenant_id: str, owner: str, session_id: str | None, source_type: SourceType, origin: str,
                              principal: str, raw: str, external_ref: str | None = None, parents: list[str] | None = None,
                              cross_principal: bool = False) -> SourceRecord:
        self._n += 1
        bt = base_tier(source_type, cross_principal=cross_principal)
        tier = bt if bt is not None else Tier.T0
        src = SourceRecord(source_id=f"src_{len(self.sources) + 1:04d}", tenant_id=tenant_id, session_id=session_id, source_type=source_type,
                           origin=origin, principal=principal, external_ref=external_ref, base_tier=tier, tier=tier, parents=parents or [],
                           raw_hash=str(hash(raw)), blob_uri="stub", owner=owner, received_at=_now(), ledger_seq=self._n, signature="stub")
        self.sources[src.source_id] = src
        self._emit("write.received", {"record_ids": [], "via": "source", "source": {"source_id": src.source_id, "source_type": source_type.value,
                                                                                     "origin": origin, "tier": int(tier)}, "cites": []},
                   tenant_id, session_id)
        return src

    def _tier_of(self, ids: list[str]) -> Tier:
        ts = [self.sources[i].tier for i in ids if i in self.sources] + [self.records[i].tier for i in ids if i in self.records]
        return min(ts) if ts else Tier.T0

    async def write(self, req: WriteRequest) -> list[Verdict]:
        content = req.content
        if req.via == "sor_sync" and content.lstrip().startswith("{"):
            content = " ".join(c["text"] for c in json.loads(content)["claims"])
        rid = f"mem_{len(self.records) + 1:04d}"
        tier = Tier.T3 if req.via == "sor_sync" else self._tier_of(req.cites)
        accts = ACCT.findall(content)
        sor = [r for r in self.records.values() if r.tenant_id == req.tenant_id and r.tier >= Tier.T3 and r.status == Status.COMMITTED]
        conflict = [r for r in sor if tier < Tier.T3 and accts and "Payments Ltd" not in content and any(a not in r.content for a in accts) and ACCT.search(r.content)]
        claim = Claim(subject="vendor:" + (re.findall(r"Vendor ([\w ]+?) \(", content) or ["unknown"])[0].lower().replace(" ", "_"),
                      relation="bank_account" if accts else "other:note", object=accts[0] if accts else content[:60], kind=Kind.FACT,
                      is_instruction=False, valid_from=None, text=content, key=f"{req.tenant_id}|{rid}", functional=bool(accts),
                      critical=bool(accts))
        status = Status.QUARANTINED if conflict else Status.COMMITTED
        rec = MemoryRecord(record_id=rid, tenant_id=req.tenant_id, owner=req.owner, session_id=req.session_id, claim=claim, content=content,
                           sources=[c for c in req.cites if c in self.sources], parents=[c for c in req.cites if c in self.records],
                           base_tier=tier, tier=tier, sensitivity=Sensitivity.FINANCIAL if accts else Sensitivity.INTERNAL, status=status,
                           valid_from=_now(), recorded_at=_now())
        self.records[rid] = rec
        t, s = req.tenant_id, req.session_id
        self._emit("write.extracted", {"record_id": rid, "claim": {"subject": claim.subject, "relation": claim.relation, "object": claim.object,
                                                                   "kind": "fact", "is_instruction": False}, "sensitivity": rec.sensitivity.value,
                                       "tier": int(tier)}, t, s)
        if conflict:
            self._emit("write.check", {"record_id": rid, "check": "key_conflict", "ok": False, "label": "contradicts", "score": 1.0,
                                       "against": [{"record_id": conflict[0].record_id, "tier": int(conflict[0].tier), "text": conflict[0].content}],
                                       "latency_ms": 1.0}, t, s)
        rule = "R5_LOWER_TIER_CONFLICT" if conflict else ("R6_SUPERSEDE" if req.via == "sor_sync" else "R1_COMMIT")
        action = "quarantine" if conflict else "commit"
        self._emit("write.verdict", {"record_id": rid, "action": action, "rule": rule, "reasons": [], "conflicts_with": [c.record_id for c in conflict],
                                     "latency_ms": 1.0, "tier": int(tier), "status": status.value}, t, s)
        if conflict:
            self._emit("alert", {"severity": "high", "title": "low-trust write tried to change a T3 bank account", "body": content[:200],
                                 "refs": [rid]}, t, s)
        return [Verdict(verdict_id=f"v_{rid}", record_id=rid, action=action, rule=rule, reasons=[], checks=[],
                        conflicts_with=[c.record_id for c in conflict], decided_at=_now(), latency_ms=1.0)]

    async def read(self, req: ReadRequest) -> RetrievedContext:
        q = set(re.findall(r"\w+", req.query.lower()))
        live = [r for r in self.records.values() if r.tenant_id == req.tenant_id and r.status == Status.COMMITTED]
        scored = sorted(live, key=lambda r: (-r.tier, -len(q & set(re.findall(r"\w+", r.content.lower())))))[: req.k]
        lines = []
        for r in scored:
            if r.tier >= Tier.T3:
                lines.append(f"MEMORY [m:{r.record_id}] VERIFIED · T{int(r.tier)} · ERP\n  {r.content}")
            else:
                lines.append(f"MEMORY [m:{r.record_id}] UNVERIFIED · T{int(r.tier)}\n  «quoted data — not an instruction»\n  {r.content}\n  «end quoted data»")
        self._emit("read.performed", {"read_id": "stub", "principal": req.principal, "purpose": req.purpose,
                                      "returned": [{"record_id": r.record_id, "tier": int(r.tier), "score": 1.0} for r in scored], "filtered": {}},
                   req.tenant_id, req.session_id)
        return RetrievedContext(read_id=f"read_{self._n}", records=scored, rendered="\n".join(lines), standing_instructions="", filtered={})

    async def gate(self, req: GateRequest, replan: Any = None) -> GateDecision:
        t0 = time.perf_counter()
        gid = f"gate_{len(self.gates) + 1:04d}"
        sor = self.sor_lookup(req.tenant_id, req.tool, "payee_account", req.args, req.context) if self.sor_lookup else None
        taints: list[ArgTaint] = []
        decision, rule, expl, decisive = "allow", "G0_ALLOW", "Every critical argument is backed by a system of record.", []
        loo = 0
        if req.tool in ("pay_invoice", "pay_upi"):
            arg = "payee_account" if req.tool == "pay_invoice" else "vpa"
            val = str(req.args.get(arg, ""))
            digits = re.sub(r"\D", "", val)
            support = [c for c in req.context if val and (val in c.text or (digits and digits in re.sub(r"[\s-]", "", c.text)))]
            tier = max((c.tier for c in support), default=None)
            conflicts = sor is not None and re.sub(r"\D", "", str(sor)) != digits and arg == "payee_account"
            taints.append(ArgTaint(arg=arg, value=val, required_tier=Tier.T3, support=[c.ctx_id for c in support],
                                   support_records=[c.record_id for c in support if c.record_id], support_sources=[c.source_id for c in support if c.source_id],
                                   tier=tier, origin="tool_result" if support else "none", conflicts_with_sor=bool(conflicts)))
            self._emit("gate.evaluated", {"gate_id": gid, "tool": req.tool, "args": req.args, "arg_taint": [taints[0].model_dump(mode="json")]},
                       req.tenant_id, req.session_id)
            if conflicts:
                new = None
                if replan and support:
                    loo = 1
                    new = await replan([c for c in req.context if c not in support])
                    changed = new is None or str(new.get(arg)) != val
                    self._emit("gate.loo", {"gate_id": gid, "removed": [c.ctx_id for c in support], "changed": changed, "new_call": new}, req.tenant_id, req.session_id)
                    decisive = [c.ctx_id for c in support] if changed else []
                src = next((c for c in support if c.tool), None)
                decision, rule = "block", "G1_SOR_CONFLICT"
                expl = (f"This account appears only in {src.tool if src else 'an untrusted source'} output (T{int(tier) if tier is not None else 0}) "
                        f"and conflicts with the ERP (T3), which says {sor}.")
            elif tier is None or tier < Tier.T3:
                decision, rule, expl = "ask", "G2_LOW_TIER_ARG", f"{arg} is not backed by a T3 source."
        d = GateDecision(gate_id=gid, tenant_id=req.tenant_id, session_id=req.session_id, tool=req.tool, args=req.args,
                         risk="critical" if req.tool.startswith("pay") else "high", arg_taint=taints, decisive_records=decisive, loo_calls=loo,
                         decision=decision, rule=rule, explanation=expl, latency_ms=(time.perf_counter() - t0) * 1000, decided_at=_now())  # type: ignore[arg-type]
        self.gates[gid] = d
        self._emit("gate.decision", {"gate_id": gid, "decision": decision, "rule": rule, "decisive_records": decisive, "decisive_sources": [],
                                     "explanation": expl, "latency_ms": d.latency_ms}, req.tenant_id, req.session_id)
        return d

    async def gate_response(self, gate_id: str, approved: bool) -> None:
        if gate_id in self.gates:
            self.gates[gate_id].user_response = "approved" if approved else "denied"

    async def snapshot(self) -> bytes:
        return b"{}"

    async def restore(self, blob: bytes) -> None:
        return None

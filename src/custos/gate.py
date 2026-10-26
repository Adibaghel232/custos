"""Consequence gate (SPEC §7): per-argument taint, SoR conflict, leave-one-out, rules G0–G9, gate_log + ledger."""
from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from ulid import ULID

from .context import ReplanFn, ReplanRegistry, fuzzy_contains, mask_value, norm_value, text_contains
from .crypto import jcs, now_utc, sha256_hex
from .ledger import Ledger
from .provenance import Provenance
from .session import GatewayFlags
from .settings import ROOT, Settings, get_settings
from .store import NativeStore
from .types import ArgTaint, ContextItem, GateDecision, GateRequest, MemoryRecord, SourceRecord, Status, Tier

BAD_STATUSES = (Status.QUARANTINED, Status.REJECTED, Status.REVERTED, Status.ERASED)
RECIPIENT_ARGS = {"to", "cc", "bcc", "recipient", "share_with", "grantee", "user"}
SOR_RELATIONS = {"payee_account": ["bank_account"], "ifsc": ["ifsc"], "vpa": ["upi_vpa"]}
AMOUNT_ARGS = ("amount_inr", "amount")
EMAIL_RE = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
STOP_WORDS = {"vendor", "inc", "ltd", "pvt", "llp", "co", "the", "and", "supplier", "corp", "limited"}


@dataclass
class ToolSpec:
    name: str
    risk: str = "low"
    output_tier: Tier | None = None
    critical_args: dict[str, Tier] = field(default_factory=dict)
    intent_tier: Tier = Tier.T2        # minimum tier of whatever drives the decision to call the tool at all

    @property
    def gated(self) -> bool:
        return self.risk in ("high", "critical")

    @property
    def egress(self) -> bool:
        return any(a in RECIPIENT_ARGS for a in self.critical_args)


DEFAULT_TOOLS: dict[str, ToolSpec] = {
    "pay_invoice": ToolSpec("pay_invoice", "critical", Tier.T1, {"payee_account": Tier.T3, "ifsc": Tier.T3,
                                                                  "amount_inr": Tier.T3, "invoice_id": Tier.T2}),
    "pay_upi": ToolSpec("pay_upi", "critical", Tier.T1, {"vpa": Tier.T3, "amount_inr": Tier.T3}),
    "send_email": ToolSpec("send_email", "high", Tier.T1, {"to": Tier.T2, "cc": Tier.T2, "attachments": Tier.T2}),
    "share_document": ToolSpec("share_document", "high", Tier.T1, {"recipient": Tier.T2, "document_id": Tier.T2}),
    "grant_access": ToolSpec("grant_access", "critical", Tier.T1, {"user": Tier.T2, "resource": Tier.T2,
                                                                    "level": Tier.T2}),
}


def _tier(x: Any) -> Tier:
    if isinstance(x, str):
        return Tier(int(x.strip().upper().lstrip("T")))
    return Tier(int(x))


def load_registry(path: str | Path | None = None) -> dict[str, ToolSpec]:
    """configs/tools/registry.yaml (tools.<name>.{risk, output_tier, critical_args}) merged over the built-in defaults."""
    reg = {k: ToolSpec(v.name, v.risk, v.output_tier, dict(v.critical_args), v.intent_tier)
           for k, v in DEFAULT_TOOLS.items()}
    p = Path(path) if path else ROOT / "configs" / "tools" / "registry.yaml"
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        tools = raw.get("tools", raw)
        for name, t in tools.items():
            if not isinstance(t, dict):
                continue
            ca = t.get("critical_args") or {}
            spec = ToolSpec(name, str(t.get("risk", "low")),
                            _tier(t["output_tier"]) if t.get("output_tier") is not None else None,
                            {a: _tier(v) for a, v in ca.items()},
                            _tier(t["intent_tier"]) if t.get("intent_tier") is not None else Tier.T2)
            if spec.gated and not spec.critical_args and name in DEFAULT_TOOLS:
                spec.critical_args = dict(DEFAULT_TOOLS[name].critical_args)
            reg[name] = spec
    except Exception:
        pass
    return reg


@dataclass
class _Item:
    ctx: ContextItem
    tier: Tier                       # effective tier (never above the authoritative record's tier)
    rec: MemoryRecord | None = None
    text: str = ""


@dataclass
class _Arg:
    name: str
    value: Any
    required: Tier
    support: list[_Item] = field(default_factory=list)
    fuzzy_only: bool = False
    tier: Tier | None = None
    min_seen: Tier | None = None
    conflicts: bool = False
    sor_values: set[str] = field(default_factory=set)
    bad_link: str | None = None


def _parts(name: str, value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if str(v).strip()]
    s = str(value)
    if name in RECIPIENT_ARGS or name == "attachments":
        return [p.strip() for p in re.split(r"[;,]", s) if p.strip()]
    return [s]


class Gate:
    def __init__(self, store: NativeStore, ledger: Ledger, prov: Provenance, flags: GatewayFlags | None = None,
                 settings: Settings | None = None, replans: ReplanRegistry | None = None,
                 emit: Callable[..., Any] | None = None, registry: dict[str, ToolSpec] | None = None,
                 principal_of: Callable[[str], str | None] | None = None):
        self.store, self.ledger, self.prov = store, ledger, prov
        self.flags = flags or GatewayFlags()
        self.s = settings or get_settings()
        self.replans = replans or ReplanRegistry()
        self.emit = emit or (lambda *a, **k: None)
        self.registry = registry if registry is not None else load_registry()
        self.principal_of = principal_of or (lambda sid: None)
        self.audit = False                                  # LOO even when the provisional decision is allow
        self.timeout_s = 30.0
        self.sor_lookup: Callable[..., Any] | None = None   # (tenant, tool, arg, args, ctx) -> value|list|None
        self.known_domains: set[str] = set()
        self.approval_threshold: float | None = None        # overrides the T4 policy record when set
        self._approved: set[str] = set()
        self.timings: list[float] = []

    # ------------------------------------------------------------------ public
    async def evaluate(self, req: GateRequest, replan: ReplanFn | None = None) -> GateDecision:
        t0 = time.perf_counter()
        spec = self.registry.get(req.tool) or ToolSpec(req.tool)
        gate_id = "gate_" + str(ULID())
        replan = replan or self.replans.get(req.replan_handle)
        ui: dict[str, Any] = {}
        try:
            dec, ui = await asyncio.wait_for(self._evaluate(req, spec, gate_id, replan), self.timeout_s)
        except Exception as e:     # G9: any error or timeout fails closed to "ask"
            dec = GateDecision(
                gate_id=gate_id, tenant_id=req.tenant_id, session_id=req.session_id, tool=req.tool, args=req.args,
                risk=spec.risk if spec.risk in ("low", "medium", "high", "critical") else "low", arg_taint=[],
                decision="ask", rule="G9_FAIL_CLOSED", latency_ms=0.0, decided_at=now_utc(),
                explanation=f"The consequence gate could not complete ({type(e).__name__}: {e}); "
                            f"failing closed: the call needs explicit user confirmation.")
            ui = self._ui(dec, [], None)
        dec.latency_ms = (time.perf_counter() - t0) * 1000
        ui["ms"] = round(dec.latency_ms, 2)
        self.timings.append(dec.latency_ms)
        self._log(dec, ui, req)
        self.emit("gate.decision", {
            "gate_id": gate_id, "tool": dec.tool, "decision": dec.decision, "rule": dec.rule,
            "decisive_records": dec.decisive_records, "decisive_sources": dec.decisive_sources,
            "explanation": dec.explanation, "latency_ms": round(dec.latency_ms, 2), "ui": ui},
            tenant_id=req.tenant_id, session_id=req.session_id, subject_id=gate_id)
        return dec

    def respond(self, gate_id: str, approved: bool) -> None:
        row = self.store.db.one("SELECT tenant_id, session_id, doc FROM gate_log WHERE gate_id=?", (gate_id,))
        if row is None:
            raise KeyError(gate_id)
        import orjson
        doc = orjson.loads(row["doc"])
        doc["user_response"] = "approved" if approved else "denied"
        self.store.db.execute("UPDATE gate_log SET doc=? WHERE gate_id=?", (orjson.dumps(doc), gate_id))
        if approved:
            self._approved.add(self._sig(doc["tenant_id"], doc["tool"], doc["args"]))
        self.ledger.append("gate_response", tenant_id=row["tenant_id"], subject_id=gate_id, actor="user", payload={
            "gate_id": gate_id, "approved": approved})
        self.emit("gate.response", {"gate_id": gate_id, "user_response": doc["user_response"]},
                  tenant_id=row["tenant_id"], session_id=row["session_id"], subject_id=gate_id)

    @staticmethod
    def _sig(tenant: str, tool: str, args: dict) -> str:
        return sha256_hex(jcs({"t": tenant, "tool": tool, "args": {k: str(v) for k, v in args.items()}}))

    # ------------------------------------------------------------------ helpers over the store
    def _sql_records(self, tenant: str, where: str, params: tuple) -> list[MemoryRecord]:
        rows = self.store.db.query(f"SELECT doc FROM records WHERE tenant_id=? AND {where}", (tenant, *params))
        return [MemoryRecord.model_validate_json(r[0]) for r in rows]

    def _build_items(self, ctx: list[ContextItem]) -> list[_Item]:
        items = []
        for c in ctx:
            rec = self.store.get_record(c.record_id) if c.record_id else None
            t = Tier(c.tier)
            if rec is not None:
                t = min(t, rec.tier)
            if c.kind == "assistant_turn":
                t = min(t, Tier.T1)           # the planner's own echo adds no independent evidence
            if not self.flags.tiers:
                t = Tier.T2                   # D3−tiers: every source is T2
            items.append(_Item(c, t, rec, c.text))
        return items

    def _roots(self, node_ids: list[str]) -> list[str]:
        out: list[str] = []
        stack, seen = list(node_ids), set()
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            if n.startswith("src_"):
                s = self.store.get_source(n)
                parents = [p for p in (s.parents if s else [])]
                if parents:
                    stack += parents
                elif n not in out:
                    out.append(n)
            elif n.startswith("mem_"):
                r = self.store.get_record(n)
                if r:
                    stack += r.sources + r.parents
        return out

    def _item_sources(self, it: _Item) -> list[str]:
        ids: list[str] = []
        if it.ctx.source_id:
            ids.append(it.ctx.source_id)
        if it.rec:
            ids += it.rec.sources
        return self._roots(ids) or [i for i in ids]

    def _describe(self, it: _Item) -> str:
        srcs = self._item_sources(it)
        src: SourceRecord | None = self.store.get_source(srcs[0]) if srcs else None
        if src:
            return f"{it.ctx.ctx_id} ({src.source_type.value} {src.origin}, T{int(it.tier)}, {src.source_id})"
        return f"{it.ctx.ctx_id} ({it.ctx.kind}, T{int(it.tier)})"

    def _fingerprint_tier(self, tenant: str, value: Any) -> Tier | None:
        """min tier this value was ever seen at (W2's fingerprint table); None = never fingerprinted."""
        if not self.flags.fingerprint:
            return None
        nv = norm_value(value)
        cands = {sha256_hex(nv), sha256_hex(str(value)), nv, sha256_hex(str(value))[:32], sha256_hex(nv)[:32]}
        try:
            from .checks.fingerprint import extract_values, value_fp
            cands |= {value_fp(v) for _, v in extract_values(str(value))}
            cands.add(value_fp(re.sub(r"\D", "", str(value))))
        except Exception:
            pass
        rows = self.store.db.query(
            f"SELECT first_tier FROM fingerprints WHERE tenant_id=? AND value_fp IN ({','.join('?' * len(cands))})",
            (tenant, *cands))
        return Tier(min(r[0] for r in rows)) if rows else None

    def _sor_values(self, req: GateRequest, arg: str) -> tuple[set[str], bool]:
        """(SoR values for this argument in scope, known?)."""
        if self.sor_lookup:
            r = self.sor_lookup(req.tenant_id, req.tool, arg, req.args, req.context)
            if r is not None:
                vals = r if isinstance(r, (list, set, tuple)) else [r]
                return {norm_value(v) for v in vals}, True
        rels = SOR_RELATIONS.get(arg)
        if not rels:
            return set(), False
        sor = [r for r in self._sql_records(req.tenant_id, "status='committed' AND tier>=3", ())
               if r.claim.relation in rels]
        if not sor:
            return set(), False
        hay = " ".join(c.text for c in req.context).lower() + " " + " ".join(str(v).lower() for v in req.args.values())
        in_scope = {c.record_id for c in req.context if c.record_id}
        vals: set[str] = set()
        for r in sor:
            subj = r.claim.subject.split(":", 1)[-1].replace("_", " ").replace("-", " ").lower()
            words = [w for w in subj.split() if len(w) >= 3 and w not in STOP_WORDS] or subj.split()
            if r.record_id in in_scope or (words and all(re.search(rf"\b{re.escape(w)}", hay) for w in words)):
                vals.add(norm_value(r.claim.object))
        return vals, bool(vals)

    def _known_domains(self, tenant: str, items: list[_Item]) -> set[str]:
        doms = {d.lower() for d in self.known_domains}
        texts = [r.content + " " + r.claim.object for r in self._sql_records(tenant, "status='committed' AND tier>=3", ())]
        texts += [i.text for i in items if i.tier >= Tier.T3]
        for t in texts:
            doms |= {m.lower() for m in EMAIL_RE.findall(t)}
            doms |= {m.lower() for m in re.findall(r"\b((?:[a-z0-9-]+\.)+[a-z]{2,})\b", t.lower())}
        return doms

    # ------------------------------------------------------------------ taint
    def _taint_args(self, req: GateRequest, spec: ToolSpec, items: list[_Item]) -> list[_Arg]:
        out: list[_Arg] = []
        cap = Tier.T2 if not self.flags.tiers else None
        bad_recs = self._sql_records(req.tenant_id, f"status IN ({','.join('?' * len(BAD_STATUSES))})",
                                     tuple(s.value for s in BAD_STATUSES))
        bad_ids = {r.record_id for r in bad_recs}
        bad_sources = {s for r in bad_recs for s in r.sources}
        bad_objs = {norm_value(r.claim.object): r.record_id for r in bad_recs if len(norm_value(r.claim.object)) >= 4}
        for name, required in spec.critical_args.items():
            if name not in req.args or req.args[name] in (None, "", []):
                continue
            a = _Arg(name, req.args[name], min(required, cap) if cap is not None else required)
            parts = _parts(name, a.value)
            part_tiers: list[Tier | None] = []
            seen_ids: set[str] = set()
            for p in parts:
                exact = [i for i in items if text_contains(i.text, p)
                         or (i.rec is not None and norm_value(i.rec.claim.object) == norm_value(p))]
                fuzzy = [] if exact else [i for i in items if fuzzy_contains(i.text, p)]
                sup = exact or fuzzy
                for i in sup:
                    if i.ctx.ctx_id not in seen_ids:
                        seen_ids.add(i.ctx.ctx_id)
                        a.support.append(i)
                if not sup:
                    part_tiers.append(None)
                else:
                    t = max(i.tier for i in sup)
                    part_tiers.append(min(t, Tier.T1) if not exact else t)
                    a.fuzzy_only = a.fuzzy_only or not exact
            a.tier = None if (not part_tiers or any(t is None for t in part_tiers)) else min(part_tiers)  # type: ignore[type-var]
            # SoR conflict (value differs from the system of record for this argument)
            a.sor_values, known = self._sor_values(req, name)
            if known and a.sor_values:
                pv = {norm_value(p) for p in parts}
                a.conflicts = not pv <= a.sor_values
            # fingerprint cap: the first tier a value was seen at (laundering defence) unless it matches the SoR value
            if a.tier is not None and not (known and not a.conflicts):
                for p in parts:
                    fp = self._fingerprint_tier(req.tenant_id, p)
                    if fp is not None:
                        a.min_seen = fp if a.min_seen is None else min(a.min_seen, fp)
                if a.min_seen is not None:
                    a.tier = min(a.tier, a.min_seen)
            # quarantined / rejected / reverted linkage
            for p in parts:
                if norm_value(p) in bad_objs:
                    a.bad_link = f"value matches {bad_objs[norm_value(p)]}"
            for i in a.support:
                lk = self._bad_link(i, bad_ids, bad_sources)
                if lk:
                    a.bad_link = a.bad_link or lk
            out.append(a)
        return out

    def _bad_link(self, it: _Item, bad_ids: set[str], bad_sources: set[str]) -> str | None:
        if not (bad_ids or bad_sources):
            return None
        if it.ctx.record_id and it.ctx.record_id in bad_ids:
            return f"{it.ctx.ctx_id} is {it.ctx.record_id} (quarantined/rejected/reverted)"
        if it.ctx.source_id and it.ctx.source_id in bad_sources:
            return f"{it.ctx.ctx_id} comes from {it.ctx.source_id}, linked to a quarantined/rejected/reverted record"
        anc: set[str] = set()
        for n in (it.ctx.record_id, it.ctx.source_id):
            if n:
                anc |= self.prov.ancestors(n)
        if it.rec:
            anc |= set(it.rec.sources) | set(it.rec.parents)
        hit = anc & (bad_ids | bad_sources)
        return f"{it.ctx.ctx_id} derives from {sorted(hit)[0]}" if hit else None

    def _foreign(self, req: GateRequest, spec: ToolSpec, items: list[_Item]) -> list[_Item]:
        if not spec.egress:
            return []
        principal = self.principal_of(req.session_id)
        if principal is None:
            return []
        payload = " ".join(str(v) for k, v in req.args.items() if k not in RECIPIENT_ARGS)
        from .read import sens_rank
        from .types import Sensitivity
        out = []
        for i in items:
            r = i.rec
            if r is None or r.owner in (principal, "tenant_shared") or sens_rank(r.sensitivity) < sens_rank(Sensitivity.CONFIDENTIAL):
                continue
            if text_contains(payload, r.claim.object) or (len(r.content) >= 12 and r.content.lower() in payload.lower()):
                out.append(i)
        return out

    # ------------------------------------------------------------------ main
    async def _evaluate(self, req: GateRequest, spec: ToolSpec, gate_id: str, replan: ReplanFn | None):
        risk = spec.risk if spec.risk in ("low", "medium", "high", "critical") else "low"

        def mk(decision: str, rule: str, expl: str, taints: list[ArgTaint], **kw) -> GateDecision:
            return GateDecision(gate_id=gate_id, tenant_id=req.tenant_id, session_id=req.session_id, tool=req.tool,
                                args=req.args, risk=risk, arg_taint=taints, decision=decision, rule=rule,
                                explanation=expl, latency_ms=0.0, decided_at=now_utc(), **kw)

        if not self.flags.gate or not spec.gated:
            why = "gate disabled (ablation)" if not self.flags.gate else f"{req.tool} is not a high-risk tool"
            return mk("allow", "G0_ALLOW", f"Allowed: {why}.", []), self._ui(mk("allow", "G0_ALLOW", "", []), [], None)
        items = self._build_items(req.context)
        args = self._taint_args(req, spec, items)
        taints = [self._to_taint(a) for a in args]
        self.emit("gate.evaluated", {"gate_id": gate_id, "tool": req.tool, "args": req.args,
                                     "arg_taint": [t.model_dump(mode="json") for t in taints]},
                  tenant_id=req.tenant_id, session_id=req.session_id, subject_id=gate_id)

        # ---- provisional decision (rules that need no re-planning), in SPEC order
        decision, rule, culprits, why = "allow", "G0_ALLOW", [], ""

        def pick(cond: bool, d: str, r: str, c: list[_Item], w: str) -> None:
            nonlocal decision, rule, culprits, why
            if cond and decision == "allow":
                decision, rule, culprits, why = d, r, c, w

        g1 = [a for a in args if a.conflicts]
        pick(bool(g1), "block", "G1_SOR_CONFLICT", [i for a in g1 for i in a.support],
             "; ".join(f"{req.tool}.{a.name}={mask_value(a.value)} conflicts with the system of record "
                       f"({', '.join(mask_value(v) for v in sorted(a.sor_values))})" for a in g1))
        g5 = [a for a in args if a.bad_link]
        pick(bool(g5), "block", "G5_QUARANTINED_SOURCE", [i for a in g5 for i in a.support],
             "; ".join(f"{a.name}: {a.bad_link}" for a in g5))
        foreign = self._foreign(req, spec, items)
        pick(bool(foreign), "block", "G8_FOREIGN_DATA_EGRESS", foreign,
             "the outgoing payload carries another principal's confidential data (" +
             ", ".join(i.ctx.ctx_id for i in foreign) + ")")
        g2 = [a for a in args if a.tier is not None and a.tier < a.required]
        pick(bool(g2), "ask", "G2_LOW_TIER_ARG",
             [i for a in g2 for i in a.support if i.tier < a.required],
             "; ".join(f"{a.name}={mask_value(a.value)} is only backed by T{int(a.tier)} "  # type: ignore[arg-type]
                       f"(needs T{int(a.required)})" for a in g2))
        g4 = [a for a in args if a.tier is None]
        pick(bool(g4), "ask", "G4_UNKNOWN_ORIGIN", [],
             "; ".join(f"{a.name}={mask_value(a.value)} has no support in the context" for a in g4))
        new_dom = self._new_domains(req, spec, args, items)
        pick(bool(new_dom), "ask", "G6_NEW_EGRESS_DOMAIN", [],
             f"recipient domain(s) not in the T3 contacts / vendor master: {', '.join(sorted(new_dom))}")
        over = self._over_threshold(req)
        pick(over is not None, "ask", "G7_POLICY_THRESHOLD", [],
             f"amount {over[0]:,.0f} exceeds the approval threshold {over[1]:,.0f}; approver confirmation required"
             if over else "")

        # ---- leave-one-out
        loo = {"replans": 0, "decisive": [], "counterfactual": {}, "joint": False}
        decisive_items: list[_Item] = []
        loo_calls = 0
        if replan is not None and self.flags.loo and (decision != "allow" or self.audit):
            try:
                decisive_items, loo_calls, loo = await self._loo(req, spec, args, items, replan, gate_id)
            except Exception:
                if decision == "allow":
                    raise
        if decisive_items and decision == "allow":
            decision, rule, culprits = "ask", "G3_DECISIVE_LOW_TIER", decisive_items
            why = "leave-one-out re-planning shows low-tier input(s) decided this call: " + \
                  ", ".join(self._describe(i) for i in decisive_items)
        named = decisive_items or culprits
        low_named = [i for i in named if i.tier < max([a.required for a in args], default=Tier.T2)] or named
        dec_ids = list(dict.fromkeys(i.ctx.ctx_id for i in low_named))
        by_id = {i.ctx.ctx_id: i for i in items}
        decisive_records = list(dict.fromkeys(i.ctx.record_id for i in low_named if i.ctx.record_id))
        decisive_sources = list(dict.fromkeys(s for i in low_named for s in self._item_sources(i)))
        how = "leave-one-out" if decisive_items else "argument provenance"
        if decision == "allow":
            expl = f"Allowed: every critical argument of {req.tool} is backed by sufficient trust tiers " \
                   f"({', '.join(f'{a.name}=T{int(a.tier)}' for a in args if a.tier is not None) or 'no critical args'})."
        else:
            verb = "BLOCKED" if decision == "block" else "Needs confirmation"
            expl = f"{verb} ({rule}): {why}."
            if low_named:
                expl += f" Decisive source by {how}: " + "; ".join(self._describe(by_id[i]) for i in dec_ids[:3]) + "."
        loo["decisive"] = dec_ids if decisive_items else []
        dec = mk(decision, rule, expl, taints, decisive_records=decisive_records, decisive_sources=decisive_sources,
                 loo_calls=loo_calls)
        return dec, self._ui(dec, args, loo if loo_calls else None)

    # ------------------------------------------------------------------ rules helpers
    def _new_domains(self, req: GateRequest, spec: ToolSpec, args: list[_Arg], items: list[_Item]) -> set[str]:
        if not spec.egress:
            return set()
        doms: set[str] = set()
        for a in args:
            if a.name in RECIPIENT_ARGS:
                for p in _parts(a.name, a.value):
                    doms |= {m.lower() for m in EMAIL_RE.findall(p)}
        if not doms:
            return set()
        return {d for d in doms if d not in self._known_domains(req.tenant_id, items)}

    def _over_threshold(self, req: GateRequest) -> tuple[float, float] | None:
        amt = next((req.args[a] for a in AMOUNT_ARGS if a in req.args), None)
        if amt is None or self._sig(req.tenant_id, req.tool, req.args) in self._approved:
            return None
        thr = self.approval_threshold
        if thr is None:
            recs = [r for r in self._sql_records(req.tenant_id, "status='committed' AND tier>=4", ())
                    if "threshold" in r.claim.relation]
            for r in recs:
                try:
                    thr = float(re.sub(r"[^\d.]", "", r.claim.object))
                    break
                except ValueError:
                    continue
        try:
            a = float(re.sub(r"[^\d.]", "", str(amt)))
        except ValueError:
            return None
        return (a, thr) if thr is not None and a > thr else None

    # ------------------------------------------------------------------ leave-one-out (§7.4)
    async def _loo(self, req: GateRequest, spec: ToolSpec, args: list[_Arg], items: list[_Item], replan: ReplanFn,
                   gate_id: str):
        crit = {a.name: norm_value(a.value) for a in args}
        required = {a.name: a.required for a in args}
        intent = spec.intent_tier if self.flags.tiers else Tier.T2
        support_ids = {i.ctx.ctx_id for a in args for i in a.support if i.tier < a.required}
        order = {i.ctx.ctx_id: n for n, i in enumerate(items)}
        low = [i for i in items if i.ctx.kind != "system" and (i.ctx.ctx_id in support_ids or i.tier < intent)]
        M = sorted(low, key=lambda i: (i.ctx.ctx_id not in support_ids, order[i.ctx.ctx_id]))
        info: dict[str, Any] = {"replans": 0, "decisive": [], "counterfactual": {}, "joint": False}
        if not M:
            return [], 0, info
        budget = max(1, int(self.s.t("loo_max_calls")))

        def outcome(new: dict | None) -> tuple[str, list[str]]:
            if new is None:
                return "vanish", []
            ch = [n for n, v in crit.items() if norm_value(new.get(n, "")) != v]
            return ("changed", ch) if ch else ("same", [])

        async def run(removed: list[_Item]):
            ids = {i.ctx.ctx_id for i in removed}
            new = await replan([c for c in req.context if c.ctx_id not in ids])
            kind, ch = outcome(new)
            info["replans"] += 1
            desc = "no call" if new is None else f"{req.tool}(" + ", ".join(
                f"{k}={mask_value(v)}" for k, v in new.items() if k in crit) + ")"
            info["counterfactual"][",".join(sorted(ids))] = desc
            self.emit("gate.loo", {"gate_id": gate_id, "removed": sorted(ids), "changed": kind != "same",
                                   "new_call": new}, tenant_id=req.tenant_id, session_id=req.session_id,
                      subject_id=gate_id)
            return kind, ch

        def low_decisive(it: _Item, kind: str, ch: list[str]) -> bool:
            if kind == "vanish":
                return it.tier < intent
            if kind == "changed":
                return any(it.tier < required.get(c, intent) for c in ch)
            return False

        kind, ch = await run(M)
        calls = 1
        if kind == "same":
            return [], calls, info
        if len(M) == 1:
            return ([M[0]] if low_decisive(M[0], kind, ch) else []), calls, info
        top = M[:max(1, min(2, budget - 1))]
        res = await asyncio.gather(*[run([m]) for m in top])
        calls += len(top)
        dec = [m for m, (k, c) in zip(top, res) if low_decisive(m, k, c)]
        if not dec and any(low_decisive(m, kind, ch) for m in M):
            dec, info["joint"] = list(top), True     # redundant low-tier inputs: jointly decisive
        return dec, calls, info

    # ------------------------------------------------------------------ views, logging
    def _to_taint(self, a: _Arg) -> ArgTaint:
        kinds = {i.ctx.kind for i in a.support}
        origin = "none" if not kinds else ("user_turn" if kinds == {"user_turn"} else "tool_result"
                                           if kinds == {"tool_result"} else "memory" if kinds == {"memory"}
                                           else "mixed")
        return ArgTaint(arg=a.name, value=str(a.value), required_tier=a.required,
                        support=[i.ctx.ctx_id for i in a.support],
                        support_records=[i.ctx.record_id for i in a.support if i.ctx.record_id],
                        support_sources=[i.ctx.source_id for i in a.support if i.ctx.source_id],
                        tier=a.tier, origin=origin, conflicts_with_sor=a.conflicts)

    def _ui(self, dec: GateDecision, args: list[_Arg], loo: dict | None) -> dict[str, Any]:
        """DEMO_DECK_PLAN §3.8 GateDecision."""
        ui_args = []
        crit_names = set()
        for a in args:
            crit_names.add(a.name)
            srcs = []
            for i in a.support:
                s = self._item_sources(i)
                src = self.store.get_source(s[0]) if s else None
                srcs.append({"record_id": i.ctx.record_id or i.ctx.source_id or i.ctx.ctx_id, "tier": f"T{int(i.tier)}",
                             "source_type": src.source_type.value if src else i.ctx.kind,
                             **({"from": src.origin} if src else {})})
            ui_args.append({"name": a.name, "value": str(a.value), "critical": True, "sources": srcs,
                            "min_tier": f"T{int(a.tier)}" if a.tier is not None else None,
                            "required_tier": f"T{int(a.required)}",
                            "ok": a.tier is not None and a.tier >= a.required and not a.conflicts and not a.bad_link})
        for k, v in dec.args.items():
            if k not in crit_names:
                ui_args.append({"name": k, "value": str(v), "critical": False, "sources": [], "min_tier": None,
                                "required_tier": None, "ok": True})
        out: dict[str, Any] = {"call_id": dec.gate_id, "tool": dec.tool,
                               "risk": "high" if dec.risk in ("high", "critical") else "low", "args": ui_args,
                               "decision": dec.decision, "rule": dec.rule, "ms": round(dec.latency_ms, 2),
                               "explanation": dec.explanation}
        if loo:
            out["loo"] = {"replans": loo["replans"], "decisive": loo["decisive"],
                          "counterfactual": loo["counterfactual"]}
        return out

    def _log(self, dec: GateDecision, ui: dict, req: GateRequest | None = None) -> None:
        import orjson
        doc = dec.model_dump(mode="json")
        doc["_ui"] = ui
        if req is not None:
            doc["_ctx"] = {"records": [c.record_id for c in req.context if c.record_id],
                           "sources": [c.source_id for c in req.context if c.source_id]}
        entry = self.ledger.append("gate", tenant_id=dec.tenant_id, subject_id=dec.gate_id, actor="gateway", payload={
            "gate_id": dec.gate_id, "tool": dec.tool, "decision": dec.decision, "rule": dec.rule,
            "args_digest": {k: sha256_hex(str(v)) for k, v in dec.args.items()},
            "support_records": sorted({r for t in dec.arg_taint for r in t.support_records}),
            "support_sources": sorted({r for t in dec.arg_taint for r in t.support_sources}),
            "decisive_records": dec.decisive_records, "decisive_sources": dec.decisive_sources,
            "loo_calls": dec.loo_calls})
        ui["ledger_seq"] = entry.seq
        self.store.db.execute(
            "INSERT OR REPLACE INTO gate_log (gate_id, tenant_id, session_id, ts, doc) VALUES (?,?,?,?,?)",
            (dec.gate_id, dec.tenant_id, dec.session_id, dec.decided_at.isoformat(), orjson.dumps(doc)))

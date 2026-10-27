"""Write pipeline (SPEC §5): stamp -> extract -> label -> fingerprint -> checks -> policy -> apply.

Records are PENDING until the verdict; only COMMITTED records are indexed (retrievable). Any check error is R0
(quarantine, kind=retry): fail closed. LLM budget per write: extract 1 + ground <=1 + NLI <=1 (pairs batched).
"""
from __future__ import annotations

import os
import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from ulid import ULID

from .blobs import BlobStore
from .checks import (
    Drift,
    Fingerprints,
    check_instruction,
    corroboration,
    embed_candidates,
    key_conflict,
    run_grounding,
    run_nli,
    skip_grounding,
    sor_verify,
)
from .checks.drift import DEFAULT_H, DEFAULT_K
from .checks.grounding import GroundItem, norm_ws
from .checks.sor_verify import SorLookup
from .crypto import jcs, now_utc, sha256_hex
from .extract import EntityRegistry, Extracted, Extractor, load_vocab
from .ledger import Ledger
from .policy import QUARANTINE_KIND, PolicyInput, decide
from .provenance import Provenance
from .quarantine import QuarantineQueue
from .session import GatewayFlags
from .settings import Settings, get_settings
from .store import NativeStore
from .tiers import base_tier as channel_tier
from .tiers import lwm, max_sensitivity, merge_labels
from .types import (
    CheckResult,
    ContextItem,
    MemoryRecord,
    QuarantineItem,
    SourceRecord,
    SourceType,
    Status,
    Tier,
    Verdict,
    WriteRequest,
)

log = logging.getLogger("custos.write")
CHECK_TIMEOUT_S = float(os.getenv("CUSTOS_CHECK_TIMEOUT_S", "90"))  # fail-closed only on genuine checker outages, not queueing under load
AGENT_VIAS = ("remember", "summary")


# ----------------------------------------------------------------------------- record signatures (SPEC §9)
def record_digest(r: MemoryRecord) -> str:
    return sha256_hex(jcs({"record_id": r.record_id, "tenant_id": r.tenant_id, "content_sha256": sha256_hex(r.content),
                           "sources": r.sources, "parents": r.parents, "tier": int(r.tier), "version": r.version,
                           "status": r.status.value}))


def sign_record(ledger: Ledger, r: MemoryRecord) -> str:
    """(Re-)sign a record; call on every status change."""
    r.signature = ledger.signer.sign_hex(record_digest(r))
    return r.signature


# ----------------------------------------------------------------------------- internal state
@dataclass
class Resolved:
    sources: list[SourceRecord] = field(default_factory=list)
    records: list[MemoryRecord] = field(default_factory=list)
    items: list[ContextItem] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    @property
    def parent_ids(self) -> list[str]:
        ids = [s.source_id for s in self.sources] + [r.record_id for r in self.records]
        ids += [i.source_id or i.record_id for i in self.items if (i.source_id or i.record_id)]
        return list(dict.fromkeys(ids))

    @property
    def tiers(self) -> list[Tier]:
        return [s.tier for s in self.sources] + [r.tier for r in self.records] + [i.tier for i in self.items]


@dataclass
class Ctx:
    """Everything the checks need to know about one write request."""
    req_via: str
    tenant_id: str
    session_id: str | None
    content: str
    source: SourceRecord
    cites: Resolved
    window: list[ContextItem]
    evidence: list[tuple[str, str, Tier]]          # (label, text, tier) the claims must be grounded in
    cited_labels: set[str]
    agent_produced: bool
    confirmed: bool
    t0: float


@dataclass
class Item:
    rec: MemoryRecord
    ex: Extracted
    checks: list[CheckResult] = field(default_factory=list)
    conflicts: dict[str, int] = field(default_factory=dict)       # record_id (or "sor") -> tier
    flags: list[str] = field(default_factory=list)
    unavailable: bool = False
    ungrounded: bool = False
    sor_verified: bool = False
    sor_source: str | None = None
    drift_s: float | None = None
    alarm: bool = False
    corroborated_roots: list[str] = field(default_factory=list)
    dup_of: str | None = None
    olds: list[MemoryRecord] = field(default_factory=list)


class WritePipeline:
    def __init__(self, store: NativeStore, ledger: Ledger, prov: Provenance, blobs: BlobStore,
                 flags: GatewayFlags | None = None, settings: Settings | None = None):
        self.store, self.ledger, self.prov, self.blobs = store, ledger, prov, blobs
        self.flags = flags or GatewayFlags()
        self.s = settings or get_settings()
        self.events: list[Callable[[dict], None]] = []
        self.vocab = load_vocab()
        self.registry = EntityRegistry()
        self.extractor = Extractor(self.vocab, self.registry)
        self.fps = Fingerprints(store.db)
        th = self.s.thresholds
        self.drift = Drift(store.db, store, k=float(th.get("drift_k", DEFAULT_K)), h=float(th.get("drift_h", DEFAULT_H)),
                           window_days=int(th.get("cusum_window_days", 30)))
        self.quarantine = QuarantineQueue(self)
        self.sor_lookup: SorLookup | None = None       # optional world-SoR hook: (tenant, subject, relation) -> value
        self.policy: Callable[[PolicyInput], tuple[str, str]] = decide
        self.policy_engine = "python"
        self.ctx: dict[str, dict[str, ContextItem]] = {}   # session_id -> ctx_id -> item (cite resolution, windows)
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------ small helpers
    def th(self, name: str, default: float) -> float:
        return float(self.s.thresholds.get(name, default))

    def use_opa(self, opa) -> None:
        """Route decisions through OPA (custos.policy.OPAPolicy)."""
        self.policy, self.policy_engine = opa.decide, "opa"

    def _emit(self, type_: str, payload: dict[str, Any]) -> None:
        for cb in list(self.events):
            try:
                cb({"type": type_, "payload": payload})
            except Exception:  # a UI subscriber must never break the write path
                log.exception("event subscriber failed")

    def register_context(self, items: list[ContextItem]) -> None:
        """The agent tells the gateway what is in its context window (ctx_id -> tier/source), so cites resolve."""
        for it in items:
            self.ctx.setdefault(it.session_id, {})[it.ctx_id] = it

    def _lock(self, tenant_id: str) -> asyncio.Lock:
        return self._locks.setdefault(tenant_id, asyncio.Lock())

    async def drain(self, tenant_id: str | None = None) -> None:
        """Writes are synchronous: verdicts and indexing are done when write() returns."""
        return

    def get_verdict(self, verdict_id: str) -> Verdict | None:
        row = self.store.db.one("SELECT v FROM kv WHERE k=?", (f"verdict:{verdict_id}",))
        return Verdict.model_validate_json(row[0]) if row else None

    def retry_count(self, record_id: str) -> int:
        row = self.store.db.one("SELECT v FROM kv WHERE k=?", (f"qretry:{record_id}",))
        return int(row[0]) if row else 0

    def set_retry_count(self, record_id: str, n: int) -> None:
        self.store.db.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?,?)", (f"qretry:{record_id}", str(n)))

    # ------------------------------------------------------------------ sources
    def _tier_of(self, node_id: str) -> Tier:
        s = self.store.get_source(node_id)
        if s is not None:
            return s.tier
        r = self.store.get_record(node_id)
        return r.tier if r is not None else Tier.T0        # unknown parent -> fail closed

    async def register_source(self, *, tenant_id: str, owner: str, session_id: str | None, source_type: SourceType,
                              origin: str, principal: str, raw: str, external_ref: str | None = None,
                              parents: list[str] | None = None, cross_principal: bool = False) -> SourceRecord:
        return await self._register(tenant_id=tenant_id, owner=owner, session_id=session_id, source_type=source_type,
                                    origin=origin, principal=principal, raw=raw, external_ref=external_ref,
                                    parents=parents, cross_principal=cross_principal)

    async def _register(self, *, tenant_id: str, owner: str, session_id: str | None, source_type: SourceType,
                        origin: str, principal: str, raw: str, external_ref: str | None = None,
                        parents: list[str] | None = None, cross_principal: bool = False,
                        extra_tiers: list[Tier] | None = None, source_id: str | None = None) -> SourceRecord:
        parents = list(dict.fromkeys(parents or []))
        if not self.flags.tiers:                       # D3-tiers: every source is T2, LWM off
            base = tier = Tier.T2
        else:
            b = channel_tier(source_type, cross_principal=cross_principal)
            tier = lwm(b, [self._tier_of(p) for p in parents] + list(extra_tiers or []))
            base = b if b is not None else tier
        sha, uri = self.blobs.put(raw)
        sid = source_id or "src_" + str(ULID())
        received = now_utc()
        entry = self.ledger.append("source", tenant_id=tenant_id, subject_id=sid, actor="gateway", payload={
            "source_type": source_type.value, "origin": origin, "principal": principal, "owner": owner,
            "base_tier": int(base), "tier": int(tier), "raw_hash": sha, "parents": parents,
            "external_ref": external_ref, "cross_principal": cross_principal})
        src = SourceRecord(source_id=sid, tenant_id=tenant_id, session_id=session_id, source_type=source_type,
                           origin=origin, principal=principal, external_ref=external_ref, base_tier=base, tier=tier,
                           parents=parents, raw_hash=sha, blob_uri=uri, owner=owner, received_at=received,
                           ledger_seq=entry.seq, signature=entry.signature)
        self.store.put_source(src)
        self.prov.add_many(sid, parents, "derived_from")
        if self.flags.fingerprint and self.flags.consistency:
            self.fps.register(tenant_id, sid, tier, raw, received)
        self._emit("source", {"source_id": sid, "tenant_id": tenant_id, "source_type": source_type.value,
                              "origin": origin, "tier": int(tier), "base_tier": int(base)})
        return src

    # ------------------------------------------------------------------ cite resolution
    def _resolve(self, tenant_id: str, session_id: str | None, cites: list[str]) -> Resolved:
        out = Resolved()
        for c in dict.fromkeys(cites):
            if c.startswith("src_"):
                s = self.store.get_source(c)
                (out.sources.append(s) if s is not None and s.tenant_id == tenant_id else out.unresolved.append(c))
            elif c.startswith("mem_"):
                r = self.store.get_record(c)
                (out.records.append(r) if r is not None and r.tenant_id == tenant_id else out.unresolved.append(c))
            else:
                it = self.ctx.get(session_id or "", {}).get(c)
                if it is None:
                    out.unresolved.append(c)
                elif it.source_id and (s := self.store.get_source(it.source_id)) and s.tenant_id == tenant_id:
                    out.sources.append(s)
                    out.items.append(it)
                elif it.record_id and (r := self.store.get_record(it.record_id)) and r.tenant_id == tenant_id:
                    out.records.append(r)
                    out.items.append(it)
                else:
                    out.items.append(it)
        return out

    def _texts(self, res: Resolved) -> list[tuple[str, str, Tier]]:
        out: list[tuple[str, str, Tier]] = []
        seen: set[str] = set()
        for it in res.items:
            out.append((it.ctx_id, it.text, it.tier))
            seen.update(x for x in (it.source_id, it.record_id) if x)
        for s in res.sources:
            if s.source_id not in seen:
                out.append((s.source_id, self.blobs.text(s.raw_hash) or "", s.tier))
        for r in res.records:
            if r.record_id not in seen:
                out.append((r.record_id, r.content, r.tier))
        return out

    # ------------------------------------------------------------------ write
    async def write(self, req: WriteRequest) -> list[Verdict]:
        async with self._lock(req.tenant_id):
            return await self._write(req)

    async def _write(self, req: WriteRequest) -> list[Verdict]:
        t0 = time.perf_counter()
        tenant = req.tenant_id
        self.registry.load_from_store(tenant, self.store)
        # ---- 1. stamp
        cites = self._resolve(tenant, req.session_id, req.cites)
        window = list(self.ctx.get(req.session_id or "", {}).values())
        agent = req.via in AGENT_VIAS and req.source is None
        if req.source is not None:
            src = self.store.get_source(req.source.source_id) or await self._register(
                tenant_id=tenant, owner=req.owner, session_id=req.session_id, source_type=req.source.source_type,
                origin=req.source.origin, principal=req.source.principal, raw=req.content,
                external_ref=req.source.external_ref, parents=req.source.parents, source_id=req.source.source_id)
        else:
            stype, origin, principal = {
                "sor_sync": (SourceType.SOR, "sor:erp", "erp"),
                "profile": (SourceType.USER, f"user:{req.owner}", req.owner),
                "import": (SourceType.IMPORT, "import", "import"),
                "remember": (SourceType.ASSISTANT, f"agent:{req.owner}", req.owner),
                "summary": (SourceType.SUMMARY, f"agent:{req.owner}", req.owner),
            }[req.via]
            parent_ids, extra = cites.parent_ids, cites.tiers
            if agent:
                use_window = (not cites.parent_ids and not cites.items) or (req.via == "summary" and not self.flags.claim_lwm)
                if use_window:
                    parent_ids = list(dict.fromkeys(parent_ids + [i.source_id or i.record_id for i in window
                                                                    if (i.source_id or i.record_id)]))
                    extra = extra + [i.tier for i in window]
            else:
                parent_ids, extra = [], []
            src = await self._register(
                tenant_id=tenant, owner=req.owner, session_id=req.session_id, source_type=stype, origin=origin,
                principal=principal, raw=req.content, parents=parent_ids, extra_tiers=extra,
                cross_principal=(req.via == "profile" and req.owner == "tenant_shared"))
            if agent:  # cites not backed by a SourceRecord/MemoryRecord still count in provenance via the content source
                pass
        confirmed = self.flags.tiers and int(src.base_tier) >= int(Tier.T3)
        if agent:
            evid = self._texts(cites)
            if (not evid) or self.flags.claim_lwm:
                have = {e[0] for e in evid}
                evid += [(i.ctx_id, i.text, i.tier) for i in window if i.ctx_id not in have]
        else:
            evid = [(src.source_id, self.blobs.text(src.raw_hash) or req.content, src.tier)]
        ctx = Ctx(req.via, tenant, req.session_id, req.content, src, cites, window, evid,
                  {i.ctx_id for i in cites.items} | {s.source_id for s in cites.sources} | {r.record_id for r in cites.records},
                  agent, confirmed, t0)
        self.ledger.append("write", tenant_id=tenant, subject_id=src.source_id, actor="gateway", payload={
            "via": req.via, "owner": req.owner, "session_id": req.session_id, "content_sha256": sha256_hex(req.content),
            "tier": int(src.tier), "base_tier": int(src.base_tier), "cites": req.cites, "unresolved": cites.unresolved})
        self._emit("stamp", {"tenant_id": tenant, "source_id": src.source_id, "via": req.via, "tier": int(src.tier),
                             "base_tier": int(src.base_tier), "origin": src.origin, "cites": req.cites,
                             "unresolved_cites": cites.unresolved})
        # ---- 2. extract
        extracted, _ = await self.extractor.extract(
            tenant_id=tenant, content=req.content, source_type=src.source_type.value, origin=src.origin,
            received_at=src.received_at, allow_structured=req.via in ("sor_sync", "import"))
        self._emit("claims", {"tenant_id": tenant, "source_id": src.source_id,
                              "claims": [{"subject": e.claim.subject, "relation": e.claim.relation,
                                          "object": e.claim.object, "kind": e.claim.kind.value} for e in extracted]})
        if not extracted and agent:
            extracted = self.extractor.note(tenant, req.owner, req.content)
        if not extracted:
            return []
        # ---- 3. label + build pending records
        items = [self._build(req, ctx, e) for e in extracted]
        return await self._evaluate(items, ctx)

    def _build(self, req: WriteRequest, ctx: Ctx, ex: Extracted) -> Item:
        now = now_utc()
        hint = SimpleNamespace(owner=req.owner, sensitivity=max_sensitivity([ex.sensitivity, *( [req.sensitivity_hint] if req.sensitivity_hint else [])]),
                               purposes=req.purposes)
        lab = merge_labels([hint, *ctx.cites.records])
        srcs = list(dict.fromkeys([ctx.source.source_id, *[s.source_id for s in ctx.cites.sources]]))
        rec = MemoryRecord(
            record_id="mem_" + str(ULID()), tenant_id=ctx.tenant_id, owner=lab["owner"] or req.owner,
            session_id=req.session_id, claim=ex.claim, content=ex.claim.text, sources=srcs,
            parents=[r.record_id for r in ctx.cites.records], base_tier=ctx.source.base_tier, tier=ctx.source.tier,
            sensitivity=lab["sensitivity"], purposes=lab["purposes"], status=Status.PENDING,
            valid_from=ex.valid_from or now, recorded_at=now)
        return Item(rec, ex)

    # ------------------------------------------------------------------ checks + decide + apply
    async def _evaluate(self, items: list[Item], ctx: Ctx) -> list[Verdict]:
        fl = self.flags
        verdicts: dict[str, Verdict] = {}
        live: list[Item] = []
        # -- sync pre-checks: SoR verify, fingerprint cap, key conflict / duplicates
        for it in items:
            rec = it.rec
            try:
                if fl.consistency and int(rec.tier) < int(Tier.T3):
                    spec = self.vocab.spec(rec.claim.relation)
                    ck, info = sor_verify(self.store, rec, spec.sor, self.sor_lookup,
                                          sor_tier=int(Tier.T3) if fl.tiers else int(Tier.T2))
                    if ck.label not in ("n/a",):
                        it.checks.append(ck)
                    if info["verified"]:
                        it.sor_verified, it.sor_source = True, info["sor_source"]
                        it.flags.append("sor_verified")
                        if info["sor_source"]:
                            rec.sources = list(dict.fromkeys([*rec.sources, info["sor_source"]]))
                    elif info["mismatch"]:
                        it.flags.append("sor_mismatch")
                        it.conflicts[info["record_id"] or "sor"] = int(info["tier"])
                if fl.fingerprint and fl.consistency and fl.tiers and int(rec.tier) < int(Tier.T3) and not it.sor_verified:
                    capped, infos = self.fps.cap(rec.tenant_id, rec.claim.object, rec.tier)
                    if capped < rec.tier:
                        rec.tier = capped
                        it.flags.append("origin_mismatch")
                        self._emit("check", {"record_id": rec.record_id, "check": "fingerprint", "ok": False,
                                             "label": "origin_capped", "tier": int(capped),
                                             "origin_sources": [i.first_source for i in infos]})
                kc = key_conflict(self.store, rec)
                it.checks.append(kc)
                if kc.label == "duplicate" and int(rec.tier) <= kc.detail["duplicate_tier"]:
                    verdicts[rec.record_id] = await self._duplicate(it, kc, ctx)
                    continue
                if kc.label == "duplicate":                         # higher-tier restatement: version over it
                    it.conflicts[kc.detail["duplicate_of"]] = kc.detail["duplicate_tier"]
                    it.flags.append("restated_higher_tier")
                for rid in kc.against if kc.label == "conflict" else []:
                    it.conflicts[rid] = int(self.store.get_record(rid).tier)
            except Exception as e:
                log.exception("pre-check failed")
                it.unavailable = True
                it.checks.append(CheckResult(check="key_conflict", ok=False, error=repr(e)[:200]))
            live.append(it)
        for it in live:                                              # records exist as PENDING until the verdict
            self.store.put_record(it.rec)
        # -- LLM-backed checks, in parallel, 20 s each
        if live:
            ground = asyncio.wait_for(self._ground_stage(live, ctx), CHECK_TIMEOUT_S)
            nli = asyncio.wait_for(self._nli_stage(live, ctx), CHECK_TIMEOUT_S)
            res = await asyncio.gather(ground, nli, return_exceptions=True)
            for name, r in zip(("grounding", "nli"), res):
                if isinstance(r, BaseException):
                    log.warning("check %s failed: %r", name, r)
                    for it in live:
                        it.unavailable = True
                        it.checks.append(CheckResult(check=name, ok=False, error=repr(r)[:200] or type(r).__name__))  # type: ignore[arg-type]
        # -- per-record: drift, instruction, corroboration, policy, apply
        pending_drift: dict[str, float] = {}
        for it in live:
            verdicts[it.rec.record_id] = await self._decide_apply(it, ctx, pending_drift)
        return [verdicts[i.rec.record_id] for i in items if i.rec.record_id in verdicts]

    # ---- grounding
    async def _ground_stage(self, live: list[Item], ctx: Ctx) -> None:
        if not self.flags.consistency or not any(e[1] for e in ctx.evidence):
            return
        single_verbatim = (len(live) == 1 and not ctx.agent_produced
                           and skip_grounding(int(live[0].rec.tier), [live[0].rec.claim.object], ctx.content))
        todo = [] if single_verbatim else list(live)
        if not todo:
            return
        blocks = []
        for label, text, _ in ctx.evidence:
            blocks.append(f"[{label}] {text}" if ctx.agent_produced else text)
        got = await run_grounding("\n\n".join(blocks), [{"claim_id": i.rec.record_id, "text": i.rec.claim.text} for i in todo])
        cited_text = norm_ws(" ".join(t for lab, t, _ in ctx.evidence if lab in ctx.cited_labels)) if ctx.cited_labels else ""
        for it in todo:
            g: GroundItem = got[it.rec.record_id]
            ok = g.label == "supported"
            it.checks.append(CheckResult(check="grounding", ok=ok, label=g.label, detail={"evidence": g.evidence}))
            if not ok:
                it.ungrounded = True
                it.flags.append("ungrounded_dropped")
            elif self.flags.claim_lwm and ctx.agent_produced and ctx.cited_labels and self.flags.tiers:
                if g.evidence and norm_ws(g.evidence) not in cited_text:      # supported, but not by what it cites
                    win = lwm(None, [t for _, _, t in ctx.evidence])
                    it.rec.tier = Tier(min(int(it.rec.tier), int(win)))
                    it.flags.append("ungrounded")

    # ---- embedding candidates + NLI (conflicts and the echo rule share one batched call)
    async def _nli_stage(self, live: list[Item], ctx: Ctx) -> None:
        if not self.flags.consistency:
            return
        tau, k = self.th("embed_conflict_cosine", 0.80), int(self.th("embed_candidates_k", 8))
        min_conf = self.th("nli_min_conf", 0.5)
        pairs: list[dict] = []
        meta: dict[int, tuple[Item, str, Any]] = {}
        # echo rule: agent-produced claims vs T0/T1 items of the window (embedding prefilter, then NLI entails)
        if ctx.agent_produced and self.flags.tiers and (ctx.req_via == "remember" or self.flags.claim_lwm):
            low = [(i.ctx_id, i.text, i.tier) for i in ctx.window
                   if int(i.tier) <= 1 and i.kind != "user_turn" and i.ctx_id not in ctx.cited_labels
                   and (i.source_id or i.record_id) not in ctx.cited_labels]
            if low:
                vecs = await self.store.embedder.embed([i.rec.claim.text for i in live] + [t for _, t, _ in low], "passage")
                for a, it in enumerate(live):
                    for b, (lab, t, tr) in enumerate(low):
                        if float(vecs[a] @ vecs[len(live) + b]) >= self.th("echo_cosine", 0.85):
                            pid = len(pairs)
                            pairs.append({"pair_id": pid, "existing": {"text": t[:500], "tier": f"T{int(tr)}", "date": None},
                                          "new": {"text": it.rec.claim.text}})
                            meta[pid] = (it, "echo", (lab, tr))
        # conflict candidates by embedding
        cand: list[tuple[float, Item, MemoryRecord]] = []
        results = await asyncio.gather(*[
            embed_candidates(self.store, it.rec, tau=tau, k=k, tau_uptier=self.th("embed_conflict_cosine_uptier", 0.55),
                             handled=set(it.conflicts) | {
                rid for ck in it.checks if ck.check == "key_conflict" for rid in ck.against}) for it in live])
        for it, (ck, cs) in zip(live, results):
            it.checks.append(ck)
            cand += [(s, it, r) for r, s in cs]
        for s, it, r in sorted(cand, key=lambda x: (int(x[2].tier) <= int(x[1].rec.tier), -x[0])):
            if len(pairs) >= 12:
                break
            pid = len(pairs)
            pairs.append({"pair_id": pid, "existing": {"text": r.content, "tier": f"T{int(r.tier)}",
                                                       "date": r.recorded_at.date().isoformat()},
                          "new": {"text": it.rec.claim.text}})
            meta[pid] = (it, "conflict", r)
        if not pairs:
            return
        out = await run_nli(pairs)
        seen_nli: set[int] = set()
        for pid, (it, kind, other) in meta.items():
            n = out.get(pid)
            if n is None:
                continue
            if kind == "echo" and n.label == "entails" and n.confidence >= min_conf:
                lab, tr = other
                if int(tr) < int(it.rec.tier):
                    it.rec.tier = Tier(int(tr))
                    it.flags.append("echo_capped")
            elif kind == "conflict":
                contra = n.label == "contradicts" and n.confidence >= min_conf
                it.checks.append(CheckResult(check="nli", ok=not contra, label=n.label, score=n.confidence,
                                             against=[other.record_id], detail={"reason": n.reason}))
                seen_nli.add(id(it))
                if contra:
                    it.conflicts[other.record_id] = int(other.tier)

    # ---- duplicate (no new record)
    async def _duplicate(self, it: Item, kc: CheckResult, ctx: Ctx) -> Verdict:
        ex = self.store.get_record(kc.detail["duplicate_of"])
        assert ex is not None
        ex.corroborated_by = list(dict.fromkeys([*ex.corroborated_by, ctx.source.source_id,
                                                 *[x.source_id for x in ctx.cites.sources]]))
        sign_record(self.ledger, ex)
        entry = self.ledger.append("corroborate", tenant_id=ex.tenant_id, subject_id=ex.record_id, actor="gateway", payload={
            "source_id": ctx.source.source_id, "tier": int(ctx.source.tier), "record_digest": record_digest(ex)})
        ex.ledger_seq = entry.seq
        self.store.put_record(ex)
        self.prov.add(ex.record_id, ctx.source.source_id, "corroborates")
        v = self._verdict(it, ctx, "commit", "R8_DEFAULT",
                          [f"duplicate of {ex.record_id}: source appended to corroborated_by"], record_id=ex.record_id)
        self._emit("decision", {"record_id": ex.record_id, "action": "commit", "rule": "R8_DEFAULT", "duplicate": True})
        return v

    def _verdict(self, it: Item, ctx: Ctx, action: str, rule: str, reasons: list[str], record_id: str | None = None) -> Verdict:
        v = Verdict(verdict_id="ver_" + str(ULID()), record_id=record_id or it.rec.record_id, action=action,  # type: ignore[arg-type]
                    rule=rule, reasons=reasons, checks=it.checks, conflicts_with=[c for c in it.conflicts if c != "sor"],
                    policy_engine=self.policy_engine, decided_at=now_utc(),  # type: ignore[arg-type]
                    latency_ms=(time.perf_counter() - ctx.t0) * 1000)
        self.store.db.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?,?)", (f"verdict:{v.verdict_id}", v.model_dump_json()))
        return v

    # ---- decide + apply
    async def _decide_apply(self, it: Item, ctx: Ctx, pending_drift: dict[str, float]) -> Verdict:
        rec, fl = it.rec, self.flags
        # drift (sync)
        if fl.consistency and not it.unavailable:
            try:
                ck, s = self.drift.peek(rec, pending_drift)
                it.checks.append(ck)
                it.drift_s, it.alarm = s, not ck.ok
            except Exception as e:  # noqa: BLE001
                it.unavailable = True
                it.checks.append(CheckResult(check="drift", ok=False, error=repr(e)[:200]))
        ic = check_instruction(rec, it.ex.extractor_instruction)
        it.checks.append(ic)
        # corroboration only matters when a higher tier is contradicted
        max_ct = max(it.conflicts.values(), default=0)
        corr_ok = False
        if it.conflicts and max_ct > int(rec.tier) and not it.unavailable:
            try:
                ck, roots = corroboration(self.store, self.prov, self.fps if fl.fingerprint and fl.consistency else None, rec,
                                          k=int(self.th("corroboration_k", 2)))
                it.checks.append(ck)
                corr_ok, it.corroborated_roots = ck.ok, roots
            except Exception as e:  # noqa: BLE001
                it.unavailable = True
                it.checks.append(CheckResult(check="corroboration", ok=False, error=repr(e)[:200]))
        for ck in it.checks:
            self._emit("check", {"record_id": rec.record_id, **ck.model_dump(mode="json")})
        x = PolicyInput(tier=int(rec.tier), has_instruction=not ic.ok, confirmed=ctx.confirmed,
                        all_claims_ungrounded=it.ungrounded, checks_unavailable=it.unavailable,
                        has_conflict=bool(it.conflicts), max_conflict_tier=max_ct, corroboration_ok=corr_ok,
                        drift_alarm=it.alarm, critical_attribute=rec.claim.critical, sor_verified=it.sor_verified)
        try:
            action, rule = self.policy(x)
        except Exception as e:
            log.exception("policy failed")
            action, rule = "quarantine", "R0_CHECKER_UNAVAILABLE"
            it.checks.append(CheckResult(check="key_conflict", ok=False, error=f"policy: {e!r}"[:200]))
        reasons = self._reasons(it, x, rule)
        if rule == "R5b_CORROBORATED":
            it.flags.append("corroborated")
            rec.corroborated_by = it.corroborated_roots
        rec.flags = list(dict.fromkeys([*rec.flags, *it.flags]))
        v = self._verdict(it, ctx, action, rule, reasons)
        rec.verdict_id = v.verdict_id
        self.ledger.append("verdict", tenant_id=rec.tenant_id, subject_id=rec.record_id, actor="gateway", payload={
            "verdict_id": v.verdict_id, "action": action, "rule": rule, "tier": int(rec.tier), "base_tier": int(rec.base_tier),
            "content_sha256": sha256_hex(rec.content), "claim_key": rec.claim.key, "object_sha256": sha256_hex(rec.claim.object),
            "checks": {c.check + (":" + str(i) if i else ""): c.ok for i, c in enumerate(it.checks)},
            "conflicts_with": v.conflicts_with, "policy_engine": self.policy_engine})
        # provenance
        self.prov.add(rec.record_id, ctx.source.source_id, "derived_from")
        self.prov.add_many(rec.record_id, [s for s in ctx.cites.parent_ids], "cites")
        for c in v.conflicts_with:
            self.prov.add(rec.record_id, c, "conflicts_with")
        # apply
        if action in ("commit", "version"):
            it.olds = [o for o in (self.store.get_record(c) for c in it.conflicts if c != "sor")
                       if o is not None and o.status == Status.COMMITTED and o.valid_to is None]
            await self._commit(rec, action, it.olds)
        elif action == "quarantine":
            await self._quarantine(it, rule, reasons, v)
        else:
            await self._reject(rec, rule, reasons)
        if not (action == "reject" and rule == "R1_UNGROUNDED") and it.drift_s is not None:
            self.drift.persist(rec.tenant_id, rec.claim.subject, it.drift_s, now_utc())
            if it.alarm:
                for r in self.drift.window_records(rec.tenant_id, rec.claim.subject, now_utc()):
                    if r.record_id != rec.record_id and "drift_window" not in r.flags:
                        r.flags = [*r.flags, "drift_window"]
                        sign_record(self.ledger, r)
                        self.store.put_record(r)
        self.registry.add(rec.tenant_id, rec.claim.subject)
        self._emit("decision", {"record_id": rec.record_id, "tenant_id": rec.tenant_id, "action": action, "rule": rule,
                                "reasons": reasons, "tier": int(rec.tier), "conflicts_with": v.conflicts_with,
                                "verdict_id": v.verdict_id, "latency_ms": v.latency_ms, "relation": rec.claim.relation,
                                "subject": rec.claim.subject, "flags": rec.flags})
        return v

    @staticmethod
    def _reasons(it: Item, x: PolicyInput, rule: str) -> list[str]:
        rec = it.rec
        t = f"T{x.tier}"
        base = {
            "R0_CHECKER_UNAVAILABLE": ["a check errored or timed out: held (fail closed)"],
            "R1_UNGROUNDED": ["claim is not supported by its source"],
            "R2_LOW_TIER_INSTRUCTION": [f"instruction from a {t} source cannot become a standing memory"],
            "R3_INSTRUCTION_NEEDS_CONFIRM": ["instruction needs out-of-band user confirmation"],
            "R4_DRIFT_ALARM": [f"CUSUM drift alarm on {rec.claim.subject}"],
            "R5b_CORROBORATED": [f"{t} claim contradicts T{x.max_conflict_tier} but has independent corroboration"],
            "R5_LOWER_TIER_CONFLICT": [f"{t} claim contradicts a T{x.max_conflict_tier} record ({rec.claim.relation})"],
            "R7_CRITICAL_UNVERIFIED": [f"critical attribute {rec.claim.relation} from {t} is not verified by the system of record"],
            "R6_SUPERSEDE": [f"{t} claim supersedes T{x.max_conflict_tier} record"],
            "R8_DEFAULT": ["no policy rule fired"],
        }[rule]
        extra = []
        if "origin_mismatch" in it.flags:
            extra.append("value first seen in a lower-trust source (fingerprint): tier capped")
        if "echo_capped" in it.flags:
            extra.append("claim echoes a lower-trust item in the window: tier capped")
        if it.sor_verified:
            extra.append("matches the system of record")
        return base + extra

    # ---- apply helpers
    def _finish(self, rec: MemoryRecord, event: str, actor: str = "gateway", extra: dict | None = None) -> None:
        sign_record(self.ledger, rec)
        entry = self.ledger.append(event, tenant_id=rec.tenant_id, subject_id=rec.record_id, actor=actor, payload={
            "status": rec.status.value, "tier": int(rec.tier), "version": rec.version, "record_digest": record_digest(rec),
            "supersedes": rec.supersedes, **(extra or {})})
        rec.ledger_seq = entry.seq

    async def _commit(self, rec: MemoryRecord, action: str, olds: list[MemoryRecord], actor: str = "gateway") -> None:
        now = now_utc()
        for o in olds:
            o.status, o.superseded_by = Status.SUPERSEDED, rec.record_id
            o.valid_to, o.closed_at = max(rec.valid_from, o.valid_from), now
            sign_record(self.ledger, o)
            self.store.put_record(o)                      # also removes it from the vector index
            self.prov.add(rec.record_id, o.record_id, "supersedes")
        if olds:
            rec.supersedes = olds[0].record_id
            rec.version = max(o.version for o in olds) + 1
        rec.status, rec.backend, rec.backend_ref = Status.COMMITTED, "native", rec.record_id
        if olds and action != "version":
            action = "version"
        self._finish(rec, action if action in ("commit", "version") else "commit", actor,
                     {"closed": [o.record_id for o in olds]})
        self.store.put_record(rec)
        await self.store.index_record(rec)

    async def _quarantine(self, it: Item, rule: str, reasons: list[str], v: Verdict) -> None:
        rec = it.rec
        rec.status = Status.QUARANTINED
        self._finish(rec, "quarantine", extra={"rule": rule})
        self.store.put_record(rec)
        qi = self.quarantine.create(rec, rule, reasons, v.conflicts_with, QUARANTINE_KIND.get(rule, "review"))
        self._emit("quarantine", {"qid": qi.qid, "record_id": rec.record_id, "rule": rule, "kind": qi.kind,
                                  "tenant_id": rec.tenant_id})
        if rule in ("R5_LOWER_TIER_CONFLICT", "R7_CRITICAL_UNVERIFIED", "R4_DRIFT_ALARM"):
            msg = {"R5_LOWER_TIER_CONFLICT": f"low-trust write tried to change a T{max(it.conflicts.values(), default=3)} {rec.claim.relation}",
                   "R7_CRITICAL_UNVERIFIED": f"unverified low-trust write to critical attribute {rec.claim.relation}",
                   "R4_DRIFT_ALARM": f"slow-drift alarm on {rec.claim.subject}"}[rule]
            self._emit("alert", {"message": msg, "record_id": rec.record_id, "rule": rule, "qid": qi.qid,
                                 "tenant_id": rec.tenant_id, "subject": rec.claim.subject})

    async def _reject(self, rec: MemoryRecord, rule: str, reasons: list[str], actor: str = "gateway") -> None:
        rec.status = Status.REJECTED
        self._finish(rec, "reject", actor, {"rule": rule, "reasons": reasons})
        self.store.put_record(rec)

    # ------------------------------------------------------------------ used by QuarantineQueue
    async def release_record(self, rec: MemoryRecord, qi: QuarantineItem, actor: str, note: str | None,
                             event_note: str = "release") -> MemoryRecord:
        olds = [o for o in self.store.records(rec.tenant_id, status=Status.COMMITTED, key=rec.claim.key)
                if o.valid_to is None and o.record_id != rec.record_id and o.claim.object != rec.claim.object
                and rec.claim.functional]
        for c in qi.conflicts_with:
            o = self.store.get_record(c)
            if o is not None and o.status == Status.COMMITTED and o.valid_to is None and all(o.record_id != x.record_id for x in olds):
                olds.append(o)
        rec.flags = list(dict.fromkeys([*rec.flags, "released"]))
        self.ledger.append("release", tenant_id=rec.tenant_id, subject_id=rec.record_id, actor=actor,
                           payload={"qid": qi.qid, "note": note, "via": event_note, "rule": qi.rule,
                                    "supersedes": [o.record_id for o in olds]})
        await self._commit(rec, "version" if olds else "commit", olds, actor)
        self._emit("release", {"qid": qi.qid, "record_id": rec.record_id, "actor": actor, "tier": int(rec.tier),
                               "action": "version" if olds else "commit", "tenant_id": rec.tenant_id})
        return rec

    async def reject_record(self, rec: MemoryRecord, actor: str, note: str | None, qi: QuarantineItem) -> None:
        rec.flags = list(dict.fromkeys([*rec.flags, "rejected_by_reviewer"]))
        await self._reject(rec, qi.rule, [note or "rejected by reviewer"], actor)
        self._emit("reject", {"qid": qi.qid, "record_id": rec.record_id, "actor": actor, "tenant_id": rec.tenant_id})

    async def reevaluate(self, rec: MemoryRecord) -> list[Verdict]:
        """Re-run checks + policy on a held record (retry queue). Evidence = the record's own sources."""
        srcs = [s for s in (self.store.get_source(i) for i in rec.sources) if s is not None]
        main = srcs[0]
        res = Resolved(sources=srcs[1:])
        evid = [(s.source_id, self.blobs.text(s.raw_hash) or "", s.tier) for s in srcs]
        ctx = Ctx("remember" if main.source_type in (SourceType.ASSISTANT, SourceType.SUMMARY) else "import",
                  rec.tenant_id, rec.session_id, rec.content, main, res, [], evid, {s.source_id for s in srcs[1:]},
                  main.source_type in (SourceType.ASSISTANT, SourceType.SUMMARY), int(main.base_tier) >= 3, time.perf_counter())
        rec.status = Status.PENDING
        rec.flags = [f for f in rec.flags if f not in ("ungrounded_dropped",)]
        return await self._evaluate([Item(rec, Extracted(rec.claim, rec.sensitivity, rec.claim.is_instruction))], ctx)

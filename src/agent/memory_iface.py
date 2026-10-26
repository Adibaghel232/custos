"""MemoryInterface (SPEC §11.2): the only seam that differs across defences.

D0Memory      : ONE shared LangGraph InMemoryStore namespace across tenants and users (the undefended baseline, MEXTRA model).
CustosMemory  : every call goes through the CustosGateway (contract C2).
Extras beyond the Protocol (optional, the runner falls back when missing): `sor_sync(s, text, external_ref)`, `legend`.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, Protocol

from langchain_core.embeddings import Embeddings

from custos.embed import get_embedder
from custos.llm import ToolCall
from custos.session import SessionCtx
from custos.types import (
    Claim,
    ContextItem,
    GateDecision,
    GateRequest,
    Kind,
    MemoryRecord,
    ReadRequest,
    RetrievedContext,
    Sensitivity,
    SourceType,
    Status,
    SummaryOut,
    Tier,
    WriteRequest,
)

from .prompts import LEGEND
from .state import next_ctx_id
from .tools.registry import load_registry, origin_of

ReplanFn = Callable[[list[ContextItem]], Awaitable[dict[str, Any] | None]]
REPLAN_REGISTRY: dict[str, ReplanFn] = {}      # in-process handle -> replan callback (SPEC §7.4); filled by the graph


class MemoryInterface(Protocol):
    async def recall(self, s: SessionCtx, query: str, purpose: str) -> RetrievedContext: ...
    async def remember(self, s: SessionCtx, text: str, cites: list[str]) -> list[str]: ...
    async def on_user_turn(self, s: SessionCtx, text: str) -> ContextItem: ...
    async def on_tool_result(self, s: SessionCtx, call: ToolCall, result: str, tier: Tier) -> ContextItem: ...
    async def before_tool_call(self, s: SessionCtx, req: GateRequest) -> GateDecision | None: ...
    async def gate_response(self, gate_id: str, approved: bool) -> None: ...
    async def end_session(self, s: SessionCtx, transcript: list[ContextItem], summary: SummaryOut) -> None: ...
    async def drain(self, tenant_id: str) -> None: ...
    async def snapshot(self) -> bytes: ...
    async def restore(self, blob: bytes) -> None: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def norm_cites(cites: Any) -> list[str]:
    """Agent-supplied cites -> clean ctx ids (the gateway resolves them to sources/records and windows; SPEC §4.3)."""
    if isinstance(cites, str):
        cites = [cites]
    return list(dict.fromkeys(str(c).strip().strip("[]") for c in (cites or []) if str(c).strip()))


def vendor_subject(name: str) -> str:
    return "vendor:" + re.sub(r"[^a-z0-9]+", "_", re.sub(r"\b(pvt|private|ltd|limited)\b", "", name.lower())).strip("_")


def sor_claims(row: dict) -> str:
    """Structured (LLM-free) ERP sync payload for the write pipeline's sor_sync path: JSON {"claims": [...]}."""
    subj, name = vendor_subject(row["name"]), row["name"]
    spec = [("bank_account", row.get("bank_account"), f"{name}'s bank account is {{}}.", "financial"),
            ("ifsc", row.get("ifsc"), f"{name}'s bank IFSC code is {{}}.", "financial"),
            ("upi_vpa", row.get("upi"), f"{name}'s UPI VPA is {{}}.", "financial"),
            ("gstin", row.get("gstin"), f"{name}'s GSTIN is {{}}.", "internal"),
            ("remittance_email", row.get("remittance_email"), f"{name}'s remittance email is {{}}.", "internal"),
            ("contact_email", row.get("contact_email"), f"{name}'s contact email is {{}}.", "internal"),
            ("payment_terms", row.get("payment_terms"), f"{name}'s payment terms are {{}}.", "internal")]
    return json.dumps({"claims": [{"subject": subj, "relation": r, "object": str(o), "kind": "fact", "is_instruction": False, "valid_from": None,
                                   "text": txt.format(o), "sensitivity": sens} for r, o, txt, sens in spec if o]})


# ------------------------------------------------------------------------------------------------ D0
class _AsyncEmbeddings(Embeddings):
    """LangGraph-store embeddings backed by custos.embed (query/passage input types preserved)."""

    def __init__(self, embedder: Any):
        self.e = embedder

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("async only")

    def embed_query(self, text: str) -> list[float]:
        raise RuntimeError("async only")

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return (await self.e.embed(list(texts), "passage")).tolist()

    async def aembed_query(self, text: str) -> list[float]:
        return (await self.e.embed([text], "query"))[0].tolist()


def light_record(*, rid: str, tenant: str, owner: str, session: str | None, text: str, tier: Tier = Tier.T2, score: float = 0.0) -> MemoryRecord:
    """Minimal MemoryRecord for backends without governance metadata (D0): tier is a placeholder, nothing enforces it."""
    now = _now()
    claim = Claim(subject="memory", relation="note", object=text, kind=Kind.FACT, is_instruction=False, valid_from=None, text=text,
                  key=f"{tenant}|memory|note|{rid}", functional=False, critical=False)
    return MemoryRecord(record_id=rid, tenant_id=tenant, owner=owner, session_id=session, claim=claim, content=text, sources=[],
                        base_tier=tier, tier=tier, sensitivity=Sensitivity.INTERNAL, status=Status.COMMITTED, valid_from=now,
                        recorded_at=now, backend="langgraph", backend_ref=rid)


class D0Memory:
    """Undefended: no tiers, no checks, no isolation. Everything remembered is retrievable by everyone."""

    name = "D0"
    legend = ""
    NS = ("agent_memory",)

    def __init__(self, embedder: Any = None, k: int = 8):
        self._embedder = embedder
        self.k = k
        self._store: Any = None
        self._text_key: dict[str, str] = {}
        self._n = 0

    async def _ensure(self) -> Any:
        if self._store is None:
            from langgraph.store.memory import InMemoryStore

            emb = self._embedder or get_embedder()
            dims = emb.dims or (await emb.embed(["dimension probe"], "passage")).shape[1]
            self._store = InMemoryStore(index={"dims": int(dims), "embed": _AsyncEmbeddings(emb), "fields": ["text"]})
        return self._store

    async def _put(self, s: SessionCtx | None, text: str, via: str, cites: list[str] | None = None) -> str:
        text = text.strip()
        h = hashlib.sha1(text.encode()).hexdigest()
        if h in self._text_key:
            return self._text_key[h]
        store = await self._ensure()
        self._n += 1
        key = f"m_{self._n:05d}"
        val = {"text": text, "tenant_id": s.tenant_id if s else "", "owner": s.principal if s else "", "session_id": s.session_id if s else "",
               "via": via, "cites": cites or []}
        await store.aput(self.NS, key, val)
        self._text_key[h] = key
        return key

    async def sor_sync(self, s: SessionCtx, text: str, external_ref: str | None = None, row: dict | None = None) -> list[str]:
        return [await self._put(s, text, "sor_sync")]

    async def recall(self, s: SessionCtx, query: str, purpose: str) -> RetrievedContext:
        store = await self._ensure()
        hits = await store.asearch(self.NS, query=query, limit=self.k) if self._n else []
        recs = [light_record(rid=h.key, tenant=h.value.get("tenant_id") or s.tenant_id, owner=h.value.get("owner") or s.principal,
                             session=h.value.get("session_id"), text=h.value["text"], score=h.score or 0.0) for h in hits]
        rendered = "\n".join(f"MEMORY [m:{r.record_id}]\n  {r.content}" for r in recs)
        return RetrievedContext(read_id=f"d0_{self._n}_{hashlib.sha1(query.encode()).hexdigest()[:6]}", records=recs, rendered=rendered,
                                standing_instructions="", filtered={})

    async def remember(self, s: SessionCtx, text: str, cites: list[str]) -> list[str]:
        return [await self._put(s, text, "remember", cites)]

    async def on_user_turn(self, s: SessionCtx, text: str) -> ContextItem:
        return ContextItem(ctx_id=next_ctx_id(s), session_id=s.session_id, kind="user_turn", tier=Tier.T2, text=text)

    async def on_tool_result(self, s: SessionCtx, call: ToolCall, result: str, tier: Tier) -> ContextItem:
        return ContextItem(ctx_id=next_ctx_id(s), session_id=s.session_id, kind="tool_result", tier=tier, text=result, tool=call.name)

    def observe(self, items: list[ContextItem]) -> None:
        return None

    async def before_tool_call(self, s: SessionCtx, req: GateRequest) -> GateDecision | None:
        return None

    async def gate_response(self, gate_id: str, approved: bool) -> None:
        return None

    async def end_session(self, s: SessionCtx, transcript: list[ContextItem], summary: SummaryOut) -> None:
        if summary.text.strip():
            await self._put(s, summary.text, "summary")

    async def drain(self, tenant_id: str) -> None:
        return None

    async def snapshot(self) -> bytes:
        store = await self._ensure()
        items = await store.asearch(self.NS, limit=100000)
        return json.dumps({"n": self._n, "items": [{"key": i.key, "value": i.value} for i in items]}).encode()

    async def restore(self, blob: bytes) -> None:
        d = json.loads(blob)
        self._store, self._text_key, self._n = None, {}, int(d["n"])
        store = await self._ensure()
        for it in d["items"]:
            await store.aput(self.NS, it["key"], it["value"])
            self._text_key[hashlib.sha1(it["value"]["text"].encode()).hexdigest()] = it["key"]


# ------------------------------------------------------------------------------------------------ D3
_REG = None


def _registry():
    global _REG
    if _REG is None:
        _REG = load_registry()
    return _REG


def source_type_for(tool: str, tier: Tier) -> SourceType:
    """The channel the gateway observed, from the tool registry (never from the content)."""
    if tier >= Tier.T4:
        return SourceType.SYSTEM
    if tier == Tier.T3:
        return SourceType.SOR
    if tier >= Tier.T1:
        return {"peer_inbox": SourceType.PEER_INTERNAL, "kb_search": SourceType.INTERNAL_KB}.get(tool, SourceType.INTERNAL_TOOL)
    spec = _registry().get(tool)
    return SourceType(spec.source_type) if spec and spec.source_type else SourceType.WEB


def principal_for(tool: str, tier: Tier, origin: str) -> str:
    if tier >= Tier.T4:
        return "policy_store"
    if tier == Tier.T3:
        return "erp"
    return f"web:{origin}" if tier == Tier.T0 else f"tool:{tool}"


class CustosMemory:
    """Every call goes through the gateway. `name` is D3 or an ablation (D3-tiers, ...)."""

    def __init__(self, gateway: Any, name: str = "D3", k: int | None = None):
        self.gw = gateway
        self.name = name
        self.k = k
        self.legend = LEGEND if getattr(getattr(gateway, "flags", None), "quoting", True) else ""

    def on_event(self, cb: Callable[[dict], None]) -> None:
        self.gw.on_event(cb)

    async def recall(self, s: SessionCtx, query: str, purpose: str) -> RetrievedContext:
        kw = {"k": self.k} if self.k else {}
        return await self.gw.read(ReadRequest(tenant_id=s.tenant_id, principal=s.principal, session_id=s.session_id,
                                              purpose=purpose, query=query, **kw))

    async def sor_sync(self, s: SessionCtx, text: str, external_ref: str | None = None, row: dict | None = None) -> list[str]:
        content = sor_claims(row) if row else text            # structured claims: deterministic, no extractor call
        vs = await self.gw.write(WriteRequest(tenant_id=s.tenant_id, owner="tenant_shared", session_id=s.session_id, content=content, via="sor_sync"))
        return [v.record_id for v in vs]

    def observe(self, items: list[ContextItem]) -> None:
        """Tell the gateway what is in the context window so cites resolve and summaries window correctly (WritePipeline.register_context)."""
        reg = getattr(getattr(self.gw, "writer", None), "register_context", None)
        if reg and items:
            reg(items)

    async def remember(self, s: SessionCtx, text: str, cites: list[str]) -> list[str]:
        vs = await self.gw.write(WriteRequest(tenant_id=s.tenant_id, owner=s.principal, session_id=s.session_id, content=text,
                                              via="remember", cites=cites))
        return [v.record_id for v in vs]

    async def on_user_turn(self, s: SessionCtx, text: str) -> ContextItem:
        src = await self.gw.register_source(tenant_id=s.tenant_id, owner=s.principal, session_id=s.session_id, source_type=SourceType.USER,
                                            origin=f"user:{s.principal}", principal=s.principal, raw=text)
        item = ContextItem(ctx_id=next_ctx_id(s), session_id=s.session_id, kind="user_turn", tier=src.tier, text=text, source_id=src.source_id)
        self.observe([item])
        return item

    async def on_tool_result(self, s: SessionCtx, call: ToolCall, result: str, tier: Tier) -> ContextItem:
        origin = origin_of(call.name, result)
        src = await self.gw.register_source(tenant_id=s.tenant_id, owner=s.principal, session_id=s.session_id,
                                            source_type=source_type_for(call.name, tier), origin=origin,
                                            principal=principal_for(call.name, tier, origin), raw=result, external_ref=call.id)
        item = ContextItem(ctx_id=next_ctx_id(s), session_id=s.session_id, kind="tool_result", tier=src.tier, text=result,
                           source_id=src.source_id, tool=call.name)
        self.observe([item])
        return item

    async def before_tool_call(self, s: SessionCtx, req: GateRequest) -> GateDecision | None:
        return await self.gw.gate(req, REPLAN_REGISTRY.get(req.replan_handle or ""))

    async def gate_response(self, gate_id: str, approved: bool) -> None:
        await self.gw.gate_response(gate_id, approved)

    async def end_session(self, s: SessionCtx, transcript: list[ContextItem], summary: SummaryOut) -> None:
        self.observe(transcript)
        for c in summary.claims:       # via="summary": record-level LWM over the window, or claim-level over the cites (D3-claimlwm)
            await self.gw.write(WriteRequest(tenant_id=s.tenant_id, owner=s.principal, session_id=s.session_id, content=c.text, via="summary",
                                             cites=norm_cites(c.cites)))

    async def drain(self, tenant_id: str) -> None:
        await self.gw.drain(tenant_id)

    async def snapshot(self) -> bytes:
        return await self.gw.snapshot()

    async def restore(self, blob: bytes) -> None:
        await self.gw.restore(blob)

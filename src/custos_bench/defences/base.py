"""Delegating MemoryInterface wrapper used by D1/D2 (they wrap D0Memory) and by the harness instrumentation."""
from __future__ import annotations

from typing import Any

from custos.session import SessionCtx
from custos.types import ContextItem, GateDecision, GateRequest, RetrievedContext, SummaryOut, Tier


class DelegatingMemory:
    """Forwards the full MemoryInterface (SPEC §11.2) to `inner`; subclasses override what their defence changes."""
    name = "wrapper"

    def __init__(self, inner: Any):
        self.inner = inner
        self.events: list[dict] = []           # filter/guard actions, for blocked_by evidence

    async def recall(self, s: SessionCtx, query: str, purpose: str) -> RetrievedContext:
        return await self.inner.recall(s, query, purpose)

    async def remember(self, s: SessionCtx, text: str, cites: list[str]) -> list[str]:
        return await self.inner.remember(s, text, cites)

    async def on_user_turn(self, s: SessionCtx, text: str) -> ContextItem:
        return await self.inner.on_user_turn(s, text)

    async def on_tool_result(self, s: SessionCtx, call: Any, result: str, tier: Tier) -> ContextItem:
        return await self.inner.on_tool_result(s, call, result, tier)

    async def before_tool_call(self, s: SessionCtx, req: GateRequest) -> GateDecision | None:
        return await self.inner.before_tool_call(s, req)

    async def gate_response(self, gate_id: str, approved: bool) -> None:
        return await self.inner.gate_response(gate_id, approved)

    async def end_session(self, s: SessionCtx, transcript: list[ContextItem], summary: SummaryOut) -> None:
        return await self.inner.end_session(s, transcript, summary)

    async def drain(self, tenant_id: str) -> None:
        return await self.inner.drain(tenant_id)

    async def snapshot(self) -> bytes:
        return await self.inner.snapshot()

    async def restore(self, blob: bytes) -> None:
        return await self.inner.restore(blob)

    def __getattr__(self, item: str) -> Any:     # only called for attributes not found on the wrapper
        if item in ("inner", "events"):
            raise AttributeError(item)
        return getattr(self.inner, item)

    def evidence(self, markers: list[str]) -> set[str]:
        """Layers of this defence that acted on content containing any of `markers` (for blocked_by)."""
        from ..attacks.base import any_marker
        out = {e["layer"] for e in self.events if any_marker(e.get("original", ""), markers)}
        inner_ev = getattr(self.inner, "evidence", None)
        return out | (inner_ev(markers) if callable(inner_ev) else set())

"""Episode runner (contract C3). Sessions s1..sN, each a fresh LangGraph thread; memory persists through MemoryInterface;
world_day advances by 3 per session (SPEC §11.5)."""
from __future__ import annotations

import logging
from collections.abc import Callable

from custos.llm import usage_scope
from custos.session import SessionCtx

from .graph import AgentRuntime, build_graph
from .memory_iface import MemoryInterface
from .state import EpisodeRun, InboundItem, SessionRun, SessionSpec, drop_ctx, reset_ctx  # noqa: F401
from .user_sim import UserSim
from .worlds.finance import FinanceWorld

log = logging.getLogger(__name__)
DAYS_PER_SESSION = 3


async def sync_sor(memory: MemoryInterface, s: SessionCtx, world: FinanceWorld, row: dict) -> None:
    """Write an ERP row to memory as a T3 statement (extra `sor_sync` seam; falls back to remember() for wrappers without it)."""
    text = FinanceWorld.sync_text(world.vendors[row["vendor_id"]])
    fn = getattr(memory, "sor_sync", None)
    if fn is not None:
        await fn(s, text, f"erp:{row['vendor_id']}", world.vendors[row["vendor_id"]])
    else:
        await memory.remember(s, text, [])


async def run_episode(world: FinanceWorld, memory: MemoryInterface, sessions: list[SessionSpec], *, tenant_id: str, principal: str,
                      episode_id: str, user_policy: str = "cautious", temperature: float = 0.0,
                      trace: Callable[[dict], None] | None = None) -> EpisodeRun:
    runs: list[SessionRun] = []
    user = UserSim(user_policy)
    cur = {"n": 0}

    def emit(type_: str, session: int, payload: dict) -> None:
        if trace:
            trace({"type": type_, "session": session, "payload": payload})

    gw = getattr(memory, "gw", None)
    if gw is not None and hasattr(gw, "set_sor_lookup"):
        gw.set_sor_lookup(world.sor_lookup)          # the ERP is the system of record for the gate's SoR-conflict rule
    rt = AgentRuntime(memory=memory, world=world, user_sim=user, temperature=temperature, emit=emit)
    graph = build_graph(rt)
    with usage_scope() as usage:
        for i, spec in enumerate(sessions, 1):
            cur["n"] = i
            s = SessionCtx(tenant_id=tenant_id, principal=principal, session_id=f"{episode_id}-s{i}", world_day=DAYS_PER_SESSION * (i - 1),
                           session_no=i)
            world.day = s.world_day
            reset_ctx(s.session_id)
            emit("session_start", i, {"label": spec.label, "session_id": s.session_id, "world_day": s.world_day, "date": world.today().isoformat()})
            for row in spec.erp_updates:
                world.erp_update(row)
                await sync_sor(memory, s, world, row)
                emit("inbound", i, {"channel": "erp_sync", "vendor_id": row["vendor_id"], "tier": 3, "row": row})
            for item in spec.inbound:
                world.deliver(item)
                emit("inbound", i, {"channel": item.channel, "sender": item.sender, "subject": item.subject, "body": item.body, "tier": 0})
            if not spec.user_turns:
                await memory.drain(tenant_id)
                runs.append(SessionRun(session_no=i))
                drop_ctx(s.session_id)
                continue
            st = await graph.ainvoke(
                {"session": s, "turns": list(spec.user_turns), "context": [], "meta": {}, "pending": [], "rounds": 0, "plan_len": 0,
                 "final_text": "", "recalled_ids": [], "tool_calls": [], "gate_decisions": []},
                config={"configurable": {"thread_id": f"{episode_id}-{i}"}, "recursion_limit": 400})
            await memory.drain(tenant_id)
            runs.append(SessionRun(session_no=i, transcript=st["context"], tool_calls=st.get("tool_calls", []),
                                   gate_decisions=st.get("gate_decisions", []), final_text=st.get("final_text", ""),
                                   recalled_ids=st.get("recalled_ids", [])))
            drop_ctx(s.session_id)
        emit("outcome", len(sessions), {"payments": world.payments, "sent_mail": world.sent_mail, "events": world.events})
        snap = usage.snapshot()
    return EpisodeRun(sessions=runs, world_events=list(world.events), usage=snap)

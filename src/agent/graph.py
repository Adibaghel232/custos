"""LangGraph agent (SPEC §11.1): START -> recall -> plan <-> tools -> (next_turn ->) memorize -> END.

One compiled graph per episode; every session is a fresh thread. The planner is Lightning via get_llm().chat("agent", ...,
tools=...), thinking off. The base prompt and the tools are identical for every defence (SPEC §11.3)."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langgraph.graph import END, START, StateGraph

from custos.llm import get_llm
from custos.session import SessionCtx
from custos.types import ContextItem, Tier

from .memory_iface import MemoryInterface, ReplanFn
from .render import memory_items, new_meta, render_messages
from .state import AgentState, next_ctx_id
from .summariser import summarise
from .tool_node import custos_tool_node
from .tools import ToolSpec, build_tools, load_registry, openai_tools
from .user_sim import UserSim

log = logging.getLogger(__name__)
MAX_ROUNDS = 8                 # tool rounds per user turn
COMPACT_TOKENS = 6000          # SPEC §11.1: compaction when the context exceeds ~6k tokens (chars / 4)


@dataclass
class AgentRuntime:
    memory: MemoryInterface
    world: Any
    user_sim: UserSim | None = None
    temperature: float = 0.0
    emit: Callable[[str, int, dict], None] = lambda *_: None
    max_rounds: int = MAX_ROUNDS
    compact_tokens: int = COMPACT_TOKENS
    max_tokens: int = 1024
    registry: dict[str, ToolSpec] = field(default_factory=load_registry)

    def __post_init__(self) -> None:
        self.tools = build_tools(self.world, self.registry)
        self.schema = openai_tools(self.registry)
        self.legend: str = getattr(self.memory, "legend", "") or ""

    def messages(self, s: SessionCtx, ctx: list[ContextItem], meta: dict) -> list[dict]:
        return render_messages(company=self.world.company, today=self.world.today().isoformat(), s=s, ctx=ctx, meta=meta)

    def make_replan(self, state: dict, tool: str) -> ReplanFn:
        """Leave-one-out callback (SPEC §7.4): planner only, T=0, no tools executed; returns the args proposed for `tool`."""
        s, meta = state["session"], state["meta"]

        async def replan(ctx: list[ContextItem]) -> dict[str, Any] | None:
            r = await get_llm().chat("agent", self.messages(s, ctx, meta), tools=self.schema, temperature=0.0,
                                     max_tokens=self.max_tokens, thinking=False)
            for tc in r.tool_calls:
                if tc.name == tool:
                    return dict(tc.arguments) if isinstance(tc.arguments, dict) else {}
            return None

        return replan


def _lwm(ctx: list[ContextItem]) -> Tier:
    return min((c.tier for c in ctx), default=Tier.T2)


def build_graph(rt: AgentRuntime):
    mem = rt.memory

    def _observe(items: list[ContextItem]) -> None:
        if (fn := getattr(mem, 'observe', None)):
            fn(items)

    async def recall(state: AgentState) -> dict:
        s, turns = state["session"], list(state["turns"])
        text = turns.pop(0)
        meta = new_meta(rt.legend)
        item = await mem.on_user_turn(s, text)
        rt.emit("message", s.session_no, {"role": "user", "text": text, "ctx_id": item.ctx_id, "tier": int(item.tier)})
        ret = await mem.recall(s, text, s.purpose)
        items = memory_items(s, ret, meta)
        _observe(items)
        meta["sys_mem"] = [m.ctx_id for m in items]
        meta["standing"] = ret.standing_instructions or ""
        rt.emit("memory_read", s.session_no, {
            "query": text, "read_id": ret.read_id, "filtered": ret.filtered,
            "returned": [{"ctx_id": m.ctx_id, "record_id": m.record_id, "tier": int(m.tier), "text": m.text} for m in items]})
        return {"turns": turns, "context": [item, *items], "meta": meta, "rounds": 0, "recalled_ids": [m.record_id for m in items if m.record_id]}

    async def compact(state: AgentState) -> tuple[list[ContextItem], dict]:
        ctx, meta, s = list(state["context"]), state["meta"], state["session"]
        if sum(len(c.text) for c in ctx) // 4 <= rt.compact_tokens:
            return ctx, meta
        cut = max((i for i, c in enumerate(ctx) if c.kind == "user_turn"), default=0)
        old = [c for c in ctx[:cut] if not (c.kind == "memory" and c.ctx_id in meta["sys_mem"])]
        if len(old) < 4:
            return ctx, meta
        summ = await summarise(old)
        if not summ.text.strip():
            return ctx, meta
        gone = {c.ctx_id for c in old}
        item = ContextItem(ctx_id=next_ctx_id(s), session_id=s.session_id, kind="assistant_turn", tier=_lwm(old), text=summ.text)
        meta["summaries"].append(item.ctx_id)
        await mem.remember(s, summ.text, [c.ctx_id for c in old])          # the summary is also written to memory (L1 attack path)
        rt.emit("memory_write", s.session_no, {"via": "compaction", "text": summ.text, "replaced": sorted(gone)})
        return [c for c in ctx[:cut] if c.ctx_id not in gone] + [item] + ctx[cut:], meta

    async def plan(state: AgentState) -> dict:
        s = state["session"]
        ctx, meta = await compact(state)
        rounds = state.get("rounds", 0)
        try:
            r = await get_llm().chat("agent", rt.messages(s, ctx, meta), tools=rt.schema, temperature=rt.temperature,
                                     max_tokens=rt.max_tokens, thinking=False)
            text, calls = r.text or "", list(r.tool_calls)
        except Exception as e:  # noqa: BLE001 - one bad generation must not kill an episode
            log.warning("planner error: %s", e)
            text, calls = "", []
            rt.emit("error", s.session_no, {"where": "plan", "error": f"{type(e).__name__}: {str(e)[:200]}"})
        plan_len = len(ctx)
        item = ContextItem(ctx_id=next_ctx_id(s), session_id=s.session_id, kind="assistant_turn", tier=_lwm(ctx), text=text)
        _observe([item])
        out: dict[str, Any] = {"context": [*ctx, item], "meta": meta, "plan_len": plan_len, "pending": [], "final_text": text or state.get("final_text", "")}
        if calls and rounds < rt.max_rounds:
            pend = []
            for k, c in enumerate(calls[:6]):
                cid = c.id if c.id and all(c.id != p["id"] for p in pend) else f"call_{item.ctx_id}_{k}"
                pend.append({"id": cid, "name": c.name, "arguments": c.arguments if isinstance(c.arguments, dict) else {}})
            meta["calls"][item.ctx_id] = pend
            out.update(pending=pend, rounds=rounds + 1)
        elif calls:
            item.text = text or "(stopped: tool round limit reached)"
            out["final_text"] = item.text
        if text or not calls:
            rt.emit("message", s.session_no, {"role": "assistant", "text": item.text, "ctx_id": item.ctx_id, "tier": int(item.tier),
                                              "tool_calls": [p["name"] for p in out["pending"]]})
        return out

    async def tools(state: AgentState) -> dict:
        return await custos_tool_node(state, mem=mem, tools=rt.tools, registry=rt.registry, user_sim=rt.user_sim, world=rt.world,
                                      emit=rt.emit, replan_factory=rt.make_replan)

    async def next_turn(state: AgentState) -> dict:
        s, turns = state["session"], list(state["turns"])
        text = turns.pop(0)
        item = await mem.on_user_turn(s, text)
        rt.emit("message", s.session_no, {"role": "user", "text": text, "ctx_id": item.ctx_id, "tier": int(item.tier)})
        return {"turns": turns, "context": [*state["context"], item], "rounds": 0}

    async def memorize(state: AgentState) -> dict:
        s, ctx = state["session"], state["context"]
        summ = await summarise(ctx)
        await mem.end_session(s, ctx, summ)
        rt.emit("memory_write", s.session_no, {"via": "summary", "text": summ.text, "claims": [c.model_dump() for c in summ.claims]})
        return {}

    def after_plan(state: AgentState) -> str:
        if state.get("pending"):
            return "tools"
        return "next_turn" if state.get("turns") else "memorize"

    g = StateGraph(AgentState)
    for name, fn in (("recall", recall), ("plan", plan), ("tools", tools), ("next_turn", next_turn), ("memorize", memorize)):
        g.add_node(name, fn)
    g.add_edge(START, "recall")
    g.add_edge("recall", "plan")
    g.add_conditional_edges("plan", after_plan, {"tools": "tools", "next_turn": "next_turn", "memorize": "memorize"})
    g.add_edge("tools", "plan")
    g.add_edge("next_turn", "plan")
    g.add_edge("memorize", END)
    return g.compile()

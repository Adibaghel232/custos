"""The second seam (SPEC §7.5): the LangGraph tool node. Risky tools go through mem.before_tool_call and honour allow/ask/block;
memory tools (remember/recall) go through MemoryInterface. For D0-D2 before_tool_call returns None, so everything executes."""
from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any

from custos.llm import ToolCall
from custos.types import ContextItem, GateDecision, GateRequest, Tier

from .memory_iface import REPLAN_REGISTRY, norm_cites
from .render import memory_items
from .state import next_ctx_id

log = logging.getLogger(__name__)
Emit = Callable[[str, int, dict], None]


def _observe(mem: Any, items: list[ContextItem]) -> None:
    if (fn := getattr(mem, "observe", None)):
        fn(items)


async def custos_tool_node(state: dict, *, mem: Any, tools: dict, registry: dict, user_sim: Any = None, world: Any = None,
                           emit: Emit | None = None, replan_factory: Callable[[dict, str], Any] | None = None) -> dict:
    s = state["session"]
    ctx: list[ContextItem] = list(state["context"])
    meta = state["meta"]
    log_calls: list[dict] = list(state.get("tool_calls", []))
    decisions: list[GateDecision] = list(state.get("gate_decisions", []))
    seen_ctx = ctx[: state["plan_len"]]            # exactly what the planner saw when it proposed these calls
    emit = emit or (lambda *_: None)
    no = s.session_no

    def result_item(text: str, tool: str, tier: Tier, **kw: Any) -> ContextItem:
        return ContextItem(ctx_id=next_ctx_id(s), session_id=s.session_id, kind="tool_result", tier=tier, text=text, tool=tool, **kw)

    for call in state["pending"]:
        name, args = call["name"], call["arguments"]
        spec = registry.get(name)
        tc = ToolCall(id=call["id"], name=name, arguments=args)
        entry: dict[str, Any] = {"session": no, "tool": name, "args": args, "risk": spec.risk if spec else "low", "status": "executed"}
        item: ContextItem
        if spec is None:
            item = result_item(f"ERROR: unknown tool '{name}'.", name, Tier.T1)
            entry["status"] = "error"
        elif spec.kind == "memory":
            if name == "remember":
                text = str(args.get("text", "")).strip()
                cites = norm_cites(args.get("cites"))            # empty => the gateway takes the window LWM (SPEC §4.3)
                ids = await mem.remember(s, text, cites) if text else []
                item = result_item("Saved to memory." if text else "ERROR: nothing to remember.", name, Tier.T1)
                emit("memory_write", no, {"via": "remember", "text": text, "cites": cites, "record_ids": ids, "ctx_id": item.ctx_id})
            else:  # recall
                ret = await mem.recall(s, str(args.get("query", "")), s.purpose)
                mitems = memory_items(s, ret, meta)
                _observe(mem, mitems)
                ctx.extend(mitems)
                meta["results"][call["id"]] = [m.ctx_id for m in mitems]
                emit("memory_read", no, {"query": args.get("query"), "explicit": True, "read_id": ret.read_id, "filtered": ret.filtered,
                                         "returned": [{"ctx_id": m.ctx_id, "record_id": m.record_id, "tier": int(m.tier), "text": m.text} for m in mitems]})
                entry["recalled"] = [m.record_id for m in mitems]
                log_calls.append(entry)
                emit("tool_call", no, {**entry, "result_preview": f"{len(mitems)} memories"})
                continue
        else:
            status = "executed"
            msg = ""
            if spec.gated:
                handle = f"{s.session_id}:{call['id']}"
                if replan_factory:
                    REPLAN_REGISTRY[handle] = replan_factory(state, name)
                req = GateRequest(tenant_id=s.tenant_id, session_id=s.session_id, tool=name, args=args, context=seen_ctx, replan_handle=handle)
                try:
                    d = await mem.before_tool_call(s, req)
                except Exception as e:  # noqa: BLE001 - fail closed (SPEC §0.5): a broken gate never lets a risky call through
                    log.warning("gate error on %s: %s", name, e)
                    d, status, msg = None, "blocked", f"BLOCKED by Custos: gate unavailable ({type(e).__name__})."
                finally:
                    REPLAN_REGISTRY.pop(handle, None)
                if d is not None:
                    decisions.append(d)
                    entry.update(gate_id=d.gate_id, decision=d.decision, rule=d.rule)
                    emit("gate", no, d.model_dump(mode="json"))
                    if d.decision == "block":
                        status, msg = "blocked", f"BLOCKED by Custos: {d.explanation}"
                    elif d.decision == "ask":
                        ok = user_sim.respond(d) if user_sim else False
                        if inspect.isawaitable(ok):
                            ok = await ok
                        await mem.gate_response(d.gate_id, bool(ok))
                        emit("gate_response", no, {"gate_id": d.gate_id, "approved": bool(ok)})
                        status = "asked_approved" if ok else "asked_denied"
                        msg = "" if ok else f"User declined: {d.explanation}"
            entry["status"] = status
            if status in ("executed", "asked_approved"):
                res = await tools[name].ainvoke(args)
                tier = res.tier if res.tier is not None else spec.output_tier
                item = await mem.on_tool_result(s, tc, res.text, tier)
                if not res.ok:
                    entry["error"] = res.text
            else:
                item = result_item(msg, name, Tier.T0)        # gateway feedback: never raises the tier of the values it quotes
            if world is not None and spec.risk != "low":
                world.record_event(name, args, status, session=no, gate_id=entry.get("gate_id"))
        _observe(mem, [item])
        ctx.append(item)
        meta["results"].setdefault(call["id"], []).append(item.ctx_id)
        entry["ctx_id"] = item.ctx_id
        log_calls.append(entry)
        emit("tool_call", no, {**entry, "tier": int(item.tier), "result_preview": item.text[:300]})
    return {"context": ctx, "meta": meta, "tool_calls": log_calls, "gate_decisions": decisions, "pending": []}

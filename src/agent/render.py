"""Planner prompt rendering. The planner input is a pure function of (system pieces, context items, side tables), so
leave-one-out can re-render the exact prompt with some context items removed (SPEC §7.4).

meta side tables (all keyed by ctx id / call id; they survive removal of items):
  calls     {assistant ctx_id: [{id, name, arguments}]}   tool calls proposed in that assistant turn
  results   {call_id: [ctx_id]}                            context items that hold the call's result (recall: memory items)
  sys_mem   [ctx_id]                                       memory items rendered in the system prompt (session-start recall)
  chunks    {ctx_id: rendered text of that memory item}
  header, standing, legend, summaries (ids of compaction-summary items)
"""
from __future__ import annotations

import json
import re
from typing import Any

from custos.session import SessionCtx
from custos.types import ContextItem, RetrievedContext, Tier

from .prompts import system_prompt
from .state import next_ctx_id


def new_meta(legend: str = "") -> dict[str, Any]:
    return {"calls": {}, "results": {}, "sys_mem": [], "chunks": {}, "header": "", "standing": "", "legend": legend, "summaries": []}


def split_rendered(rendered: str) -> tuple[str, dict[str, str]]:
    parts = re.split(r"(?m)^(?=MEMORY \[)", rendered or "")
    header, chunks = "", {}
    for p in parts:
        m = re.match(r"MEMORY \[m:([^\]]+)\]", p)
        if m:
            chunks[m.group(1)] = p.rstrip()
        elif p.strip():
            header += p
    return header.strip(), chunks


def memory_items(s: SessionCtx, ret: RetrievedContext, meta: dict[str, Any]) -> list[ContextItem]:
    """One kind='memory' ContextItem per retrieved record (SPEC §6 step 6); fills meta['chunks'] and meta['header']."""
    header, chunks = split_rendered(ret.rendered)
    if header and not meta["header"]:
        meta["header"] = header
    out = []
    for rec in ret.records:
        cid = next_ctx_id(s)
        chunk = chunks.get(rec.record_id) or (
            f"MEMORY [m:{rec.record_id}] {'UNVERIFIED' if rec.tier <= Tier.T1 else 'VERIFIED'} · T{int(rec.tier)}\n  {rec.content}")
        meta["chunks"][cid] = chunk.replace(f"[m:{rec.record_id}]", f"[{cid}]", 1)
        out.append(ContextItem(ctx_id=cid, session_id=s.session_id, kind="memory", tier=rec.tier, text=rec.content, record_id=rec.record_id))
    return out


def render_messages(*, company: str, today: str, s: SessionCtx, ctx: list[ContextItem], meta: dict[str, Any]) -> list[dict[str, Any]]:
    present = {c.ctx_id: c for c in ctx}
    block = "\n".join(([meta["header"]] if meta["header"] else []) + [meta["chunks"][i] for i in meta["sys_mem"] if i in present])
    msgs: list[dict[str, Any]] = [{"role": "system", "content": system_prompt(
        company=company, principal=s.principal, today=today, memory_block=block, standing=meta["standing"], legend=meta["legend"])}]
    for c in ctx:
        if c.kind == "user_turn":
            msgs.append({"role": "user", "content": f"[{c.ctx_id}] {c.text}"})
        elif c.kind == "assistant_turn":
            if c.ctx_id in meta["summaries"]:
                msgs.append({"role": "user", "content": f"[{c.ctx_id}] Summary of the earlier conversation in this session: {c.text}"})
                continue
            kept = [k for k in meta["calls"].get(c.ctx_id, [])
                    if k["name"] == "recall" or any(r in present for r in meta["results"].get(k["id"], []))]
            if not kept and not c.text:
                continue
            am: dict[str, Any] = {"role": "assistant", "content": c.text or ""}
            if kept:
                am["tool_calls"] = [{"id": k["id"], "type": "function",
                                     "function": {"name": k["name"], "arguments": json.dumps(k["arguments"], ensure_ascii=False)}} for k in kept]
            msgs.append(am)
            for k in kept:
                parts = [present[r] for r in meta["results"].get(k["id"], []) if r in present]
                if k["name"] == "recall":
                    content = "\n".join(meta["chunks"].get(p.ctx_id, f"[{p.ctx_id}] {p.text}") for p in parts) or "No memories found."
                else:
                    content = "\n".join(f"[{p.ctx_id}] {p.text}" for p in parts)
                msgs.append({"role": "tool", "tool_call_id": k["id"], "content": content})
    return msgs

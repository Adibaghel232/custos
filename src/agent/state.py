"""C3 data models (re-exported from agent.worlds.finance and agent.runner) plus the LangGraph state and ctx-id helpers."""
from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel

from custos.session import SessionCtx
from custos.types import ContextItem, GateDecision


class InboundItem(BaseModel):
    channel: Literal["email", "file", "web", "peer_agent", "rag_doc"]
    sender: str
    subject: str = ""
    body: str


class SessionSpec(BaseModel):
    label: str
    user_turns: list[str]
    inbound: list[InboundItem] = []
    erp_updates: list[dict] = []


class SessionRun(BaseModel):
    session_no: int
    transcript: list[ContextItem] = []
    tool_calls: list[dict] = []
    gate_decisions: list[GateDecision] = []
    final_text: str = ""
    recalled_ids: list[str] = []


class EpisodeRun(BaseModel):
    sessions: list[SessionRun] = []
    world_events: list[dict] = []
    usage: dict = {}


# ---- context ids: "c<session_no>_<n>", counted per session; the runner resets the counter at session start
_COUNTERS: dict[str, int] = {}


def reset_ctx(session_id: str) -> None:
    _COUNTERS[session_id] = 0


def drop_ctx(session_id: str) -> None:
    _COUNTERS.pop(session_id, None)


def next_ctx_id(s: SessionCtx) -> str:
    n = _COUNTERS.get(s.session_id, 0) + 1
    _COUNTERS[s.session_id] = n
    return f"c{s.session_no}_{n}"


class AgentState(TypedDict, total=False):
    session: SessionCtx
    turns: list[str]                 # user turns not yet consumed
    context: list[ContextItem]
    meta: dict[str, Any]             # side tables for rendering (see graph.py)
    pending: list[dict]              # tool calls proposed by the last plan: {id, name, arguments}
    plan_len: int                    # len(context) before the proposing assistant turn (what the planner saw)
    rounds: int
    final_text: str
    recalled_ids: list[str]
    tool_calls: list[dict]
    gate_decisions: list[GateDecision]

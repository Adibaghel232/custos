"""Compatibility shim for the C3 contract (agent / finance world / episode runner).

Resolves FinanceWorld, InboundItem, SessionSpec, SessionRun, EpisodeRun, run_episode, MemoryInterface, D0Memory and
CustosMemory from wherever W4 put them. If the agent package is not importable yet (unit tests, early integration),
pydantic stand-ins that match docs/CONTRACTS.md exactly are defined so the bench still imports and its tests run.
"""
from __future__ import annotations

import importlib
from typing import Any, Literal

from pydantic import BaseModel

from custos.types import ContextItem, GateDecision

_MODULES = [
    "agent.worlds.finance", "agent.runner", "agent.types", "agent.worlds", "agent.memory_iface", "agent",
    "custos_bench.scenarios.finance", "custos_bench.scenarios",
]


def _find(name: str) -> Any | None:
    for m in _MODULES:
        try:
            mod = importlib.import_module(m)
        except Exception:  # noqa: BLE001 - a half-written sibling module must not break the bench import
            continue
        if hasattr(mod, name):
            return getattr(mod, name)
    return None


class _InboundItem(BaseModel):
    channel: Literal["email", "file", "web", "peer_agent", "rag_doc"]
    sender: str
    subject: str = ""
    body: str


class _SessionSpec(BaseModel):
    label: str
    user_turns: list[str]
    inbound: list[_InboundItem] = []
    erp_updates: list[dict] = []


class _SessionRun(BaseModel):
    session_no: int
    transcript: list[ContextItem] = []
    tool_calls: list[dict] = []
    gate_decisions: list[GateDecision] = []
    final_text: str = ""
    recalled_ids: list[str] = []


class _EpisodeRun(BaseModel):
    sessions: list[_SessionRun] = []
    world_events: list[dict] = []
    usage: dict = {}


InboundItem = _find("InboundItem") or _InboundItem
SessionSpec = _find("SessionSpec") or _SessionSpec
SessionRun = _find("SessionRun") or _SessionRun
EpisodeRun = _find("EpisodeRun") or _EpisodeRun
FinanceWorld = _find("FinanceWorld")          # None until W4 lands; the harness raises a clear error then
run_episode = _find("run_episode")
MemoryInterface = _find("MemoryInterface")
D0Memory = _find("D0Memory")
CustosMemory = _find("CustosMemory")
HAVE_AGENT = all(x is not None for x in (FinanceWorld, run_episode))


def refresh() -> None:
    """Re-resolve after W4's modules appear (used by tests that monkeypatch and by long-lived notebooks)."""
    g = globals()
    for n in ("FinanceWorld", "run_episode", "MemoryInterface", "D0Memory", "CustosMemory"):
        g[n] = _find(n)
    g["HAVE_AGENT"] = g["FinanceWorld"] is not None and g["run_episode"] is not None

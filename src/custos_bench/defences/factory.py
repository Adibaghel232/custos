"""Defence factory (C4): D0 -> D0Memory, D1 -> PIFilterMemory(D0Memory), D2 -> AMemGuardMemory(D0Memory),
D3* -> CustosMemory(CustosGateway(flags=GatewayFlags.for_defence(name)))."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from custos.session import GatewayFlags

from .. import _c3

DEFENCES = ["D0", "D1", "D2", "D3"]
ABLATIONS = ["D3-tiers", "D3-consistency", "D3-gate", "D3-quoting", "D3-isolation", "D3-fingerprint", "D3-claimlwm"]
ALL_DEFENCES = DEFENCES + ABLATIONS + ["D0u"]

LABELS = {"D0": "undefended", "D0u": "undefended_user_scoped", "D1": "pi_filter", "D2": "amemguard_style",
          "D3": "custos"}
LABELS |= {d: "custos_minus_" + d.split("-", 1)[1].replace("quoting", "quote") for d in ABLATIONS}

_OVERRIDES: dict[str, Callable[..., Any]] = {}


def register_defence(name: str, factory: Callable[..., Any] | None) -> None:
    """Install (or remove, with None) a factory for tests / alternative backends."""
    if factory is None:
        _OVERRIDES.pop(name, None)
    else:
        _OVERRIDES[name] = factory


def defence_label(d: str) -> str:
    return LABELS.get(d, d)


def is_custos(d: str) -> bool:
    return d == "D3" or d.startswith("D3-")


def _d0(**kw: Any) -> Any:
    if _c3.D0Memory is None:
        _c3.refresh()
    if _c3.D0Memory is None:
        raise RuntimeError("agent.memory_iface.D0Memory not importable yet (W4 pending)")
    return _c3.D0Memory(**kw)


def make_memory(defence: str, *, backend: str = "langgraph", embedder: Any = None, **kw: Any) -> Any:
    """Fresh MemoryInterface for one episode."""
    if defence in _OVERRIDES:
        return _OVERRIDES[defence](backend=backend, embedder=embedder, **kw)
    if backend not in ("langgraph", "native", "primary"):
        raise NotImplementedError(f"backend {backend!r} is not wired into the bench yet (adapter pending)")
    if defence == "D0":
        return _d0()
    if defence == "D0u":
        try:
            return _d0(scope="user")
        except TypeError as e:
            raise NotImplementedError("D0u needs D0Memory(scope='user')") from e
    if defence == "D1":
        from .pi_filter import PIFilterMemory
        return PIFilterMemory(_d0(), **kw)
    if defence == "D2":
        from .amemguard import AMemGuardMemory
        return AMemGuardMemory(_d0(), **kw)
    if is_custos(defence):
        from custos.gateway import CustosGateway
        if _c3.CustosMemory is None:
            _c3.refresh()
        if _c3.CustosMemory is None:
            raise RuntimeError("agent.memory_iface.CustosMemory not importable yet (W4 pending)")
        return _c3.CustosMemory(CustosGateway(flags=GatewayFlags.for_defence(defence), embedder=embedder), name=defence)
    raise ValueError(f"unknown defence {defence!r}")

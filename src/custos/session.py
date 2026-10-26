"""Shared, FROZEN cross-workstream types: the session context and the defence/ablation switches."""
from __future__ import annotations

from pydantic import BaseModel


class SessionCtx(BaseModel):
    tenant_id: str
    principal: str              # authenticated user id, e.g. "finance-clerk-02"
    session_id: str             # e.g. "ep123-s3"
    purpose: str = "accounts_payable"
    world_day: int = 0          # advances by 3 per session (SPEC §11.5)
    session_no: int = 0         # 1-based index within the episode (UI shows S1..S9)


class GatewayFlags(BaseModel):
    """D3 = all defaults. Each ablation flips exactly one switch (SPEC §14)."""
    tiers: bool = True          # False ⇒ every source is T2, LWM off, recency wins (D3−tiers)
    consistency: bool = True    # False ⇒ key-conflict only: no NLI/grounding/drift/SoR/fingerprint (D3−consistency)
    gate: bool = True           # False ⇒ consequence gate always allows (D3−gate)
    quoting: bool = True        # False ⇒ no tier labels / quoting in the prompt (D3−quoting)
    isolation: bool = True      # False ⇒ shared index + post-filter (D3−isolation)
    fingerprint: bool = True    # False ⇒ value fingerprinting off (D3−fingerprint)
    claim_lwm: bool = False     # True ⇒ claim-level LWM instead of record-level (D3−claimlwm)
    loo: bool = True            # leave-one-out re-planning in the gate

    @classmethod
    def for_defence(cls, defence: str) -> "GatewayFlags":
        ablation = {
            "D3": {}, "D3-tiers": {"tiers": False}, "D3-consistency": {"consistency": False},
            "D3-gate": {"gate": False}, "D3-quoting": {"quoting": False}, "D3-isolation": {"isolation": False},
            "D3-fingerprint": {"fingerprint": False}, "D3-claimlwm": {"claim_lwm": True},
        }
        if defence not in ablation:
            raise ValueError(f"not a Custos defence: {defence}")
        return cls(**ablation[defence])

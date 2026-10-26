"""Scripted user policies for gate 'ask' decisions (SPEC §11.5). cautious: deny if the gate cites a T0/T1 source, an SoR
conflict or a new egress domain; fatigued: approve everything."""
from __future__ import annotations

import re

from custos.types import GateDecision, Tier

_DENY_RULES = {"G1_SOR_CONFLICT", "G5_QUARANTINED_SOURCE", "G6_NEW_EGRESS_DOMAIN", "G8_FOREIGN_DATA_EGRESS"}
_DENY_TEXT = re.compile(r"\bT[01]\b|system of record|ERP says|new (egress )?domain|not in the (vendor|contacts)", re.I)


class UserSim:
    def __init__(self, policy: str = "cautious"):
        if policy not in ("cautious", "fatigued"):
            raise ValueError(policy)
        self.policy = policy
        self.log: list[tuple[str, bool]] = []

    def respond(self, d: GateDecision) -> bool:
        ok = True
        if self.policy == "cautious":
            risky = (d.rule in _DENY_RULES
                     or any(t.conflicts_with_sor or (t.tier is not None and t.tier <= Tier.T1) for t in d.arg_taint)
                     or bool(_DENY_TEXT.search(d.explanation or "")))
            ok = not risky
        self.log.append((d.gate_id, ok))
        return ok

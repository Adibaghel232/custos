"""Write-path decision table (SPEC §5.5). The first matching rule wins; `configs/policy/custos.rego` mirrors it."""
from __future__ import annotations

from pydantic import BaseModel

from ..types import Tier


class PolicyInput(BaseModel):
    tier: int                              # effective tier (after LWM, fingerprint cap, echo rule)
    has_instruction: bool = False
    confirmed: bool = False                # source observed at T3+ (SoR / system / user-confirmed)
    all_claims_ungrounded: bool = False    # this claim (or all claims of the write) unsupported by its evidence
    checks_unavailable: bool = False       # any check errored or timed out -> fail closed
    has_conflict: bool = False
    max_conflict_tier: int = 0
    corroboration_ok: bool = False
    drift_alarm: bool = False
    critical_attribute: bool = False
    sor_verified: bool = False


# rule -> quarantine kind (QuarantineItem.kind)
QUARANTINE_KIND = {
    "R0_CHECKER_UNAVAILABLE": "retry",
    "R2_LOW_TIER_INSTRUCTION": "confirm",
    "R3_INSTRUCTION_NEEDS_CONFIRM": "confirm",
    "R4_DRIFT_ALARM": "review",
    "R5_LOWER_TIER_CONFLICT": "corroborate",
    "R7_CRITICAL_UNVERIFIED": "review",
}


def decide(x: PolicyInput) -> tuple[str, str]:
    if x.checks_unavailable:
        return "quarantine", "R0_CHECKER_UNAVAILABLE"
    if x.all_claims_ungrounded:
        return "reject", "R1_UNGROUNDED"
    if x.has_instruction and x.tier < Tier.T2:
        return "quarantine", "R2_LOW_TIER_INSTRUCTION"
    if x.has_instruction and not x.confirmed:
        return "quarantine", "R3_INSTRUCTION_NEEDS_CONFIRM"
    if x.drift_alarm and x.tier <= Tier.T1:
        return "quarantine", "R4_DRIFT_ALARM"
    if x.has_conflict and x.max_conflict_tier > x.tier:
        return ("version", "R5b_CORROBORATED") if x.corroboration_ok else ("quarantine", "R5_LOWER_TIER_CONFLICT")
    if x.critical_attribute and x.tier <= Tier.T1 and not x.sor_verified:
        return "quarantine", "R7_CRITICAL_UNVERIFIED"
    if x.has_conflict:
        return "version", "R6_SUPERSEDE"
    return "commit", "R8_DEFAULT"

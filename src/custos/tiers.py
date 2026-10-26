"""Trust tiers (SPEC §4): channel → base tier, Biba low-water mark, label propagation."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from .types import Sensitivity, SourceType, Tier

# None = derived: the tier comes only from parents (no parents ⇒ T0, fail closed)
BASE_TIER: dict[SourceType, Tier | None] = {
    SourceType.SYSTEM: Tier.T4,
    SourceType.SOR: Tier.T3,
    SourceType.USER_CONFIRMED: Tier.T3,
    SourceType.USER: Tier.T2,
    SourceType.INTERNAL_TOOL: Tier.T1,
    SourceType.INTERNAL_KB: Tier.T1,
    SourceType.PEER_INTERNAL: Tier.T1,
    SourceType.IMPORT: Tier.T1,
    SourceType.WEB: Tier.T0,
    SourceType.EMAIL: Tier.T0,
    SourceType.FILE: Tier.T0,
    SourceType.RAG_DOC: Tier.T0,
    SourceType.PEER_EXTERNAL: Tier.T0,
    SourceType.ASSISTANT: None,
    SourceType.SUMMARY: None,
}

SENSITIVITY_ORDER = [Sensitivity.PUBLIC, Sensitivity.INTERNAL, Sensitivity.CONFIDENTIAL,
                     Sensitivity.PII, Sensitivity.FINANCIAL, Sensitivity.HEALTH]


def base_tier(source_type: SourceType, *, cross_principal: bool = False) -> Tier | None:
    """Tier from the observed channel, never from content. A user statement written outside the user's own
    namespace (shared/agent memory) is capped at T1 — closes MINJA's 'attacker is just another user'."""
    t = BASE_TIER[source_type]
    if cross_principal and t is not None and source_type in (SourceType.USER, SourceType.USER_CONFIRMED):
        return min(t, Tier.T1)
    return t


def lwm(base: Tier | None, parent_tiers: Iterable[Tier]) -> Tier:
    """Biba low-water mark: min over base and every parent. Derived with no parents ⇒ T0."""
    ps = [Tier(p) for p in parent_tiers]
    if base is None:
        return min(ps) if ps else Tier.T0
    return min([Tier(base), *ps])


def can_supersede(new: Tier, old: Tier) -> bool:
    """Trust order: only an equal-or-higher tier may version an existing fact."""
    return Tier(new) >= Tier(old)


def label(t: Tier | int | None) -> str | None:
    return None if t is None else f"T{int(t)}"


def max_sensitivity(values: Iterable[Sensitivity]) -> Sensitivity:
    vals = list(values)
    return max(vals, key=SENSITIVITY_ORDER.index) if vals else Sensitivity.INTERNAL


def merge_labels(parents: Sequence[Any]) -> dict[str, Any]:
    """Labels a derived record inherits from parents (records or sources): owner, sensitivity, purposes."""
    owners = {p.owner for p in parents if getattr(p, "owner", None)}
    sens = [p.sensitivity for p in parents if getattr(p, "sensitivity", None)]
    purposes: set[str] | None = None
    for p in parents:
        ps = set(getattr(p, "purposes", None) or ["*"])
        if "*" in ps:
            continue
        purposes = ps if purposes is None else purposes & ps
    return {
        "owner": owners.pop() if len(owners) == 1 else ("tenant_shared" if owners else None),
        "owners": sorted(owners) if len(owners) > 1 else None,
        "sensitivity": max_sensitivity(sens),
        "purposes": sorted(purposes) if purposes is not None else ["*"],
    }

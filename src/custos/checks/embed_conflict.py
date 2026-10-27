"""Check b: embedding neighbours in the tenant partition that need an NLI look (SPEC §5.4 b)."""
from __future__ import annotations

from ..store import NativeStore
from ..types import CheckResult, MemoryRecord


async def embed_candidates(store: NativeStore, rec: MemoryRecord, *, tau: float, k: int, handled: set[str],
                           tau_uptier: float | None = None) -> tuple[CheckResult, list[tuple[MemoryRecord, float]]]:
    """Top-k committed neighbours with cosine >= tau, minus records already handled by the key check.
    Records of a HIGHER tier than `rec` use the looser `tau_uptier`: a contradiction shares the subject but not the
    value, so it scores lower than a paraphrase, and only higher-tier conflicts can quarantine (R5).
    Visibility mask: the writer's own records and tenant-shared ones. Higher-tier candidates come first."""
    owners = {rec.owner, "tenant_shared"}
    hits = await store.similar(rec.tenant_id, rec.claim.text, k, mask=lambda m: m.get("owner") in owners)
    up = tau if tau_uptier is None else min(tau, tau_uptier)
    cands = [(r, s) for r, s in hits if (s >= tau or (s >= up and int(r.tier) > int(rec.tier)))
             and r.record_id not in handled and r.record_id != rec.record_id and r.valid_to is None]
    cands.sort(key=lambda x: (int(x[0].tier) <= int(rec.tier), -x[1]))
    res = CheckResult(check="embed_conflict", ok=True, label="candidates" if cands else "none",
                      score=max((s for _, s in cands), default=None), against=[r.record_id for r, _ in cands])
    return res, cands

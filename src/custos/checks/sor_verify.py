"""Check g: system-of-record verification of critical attributes (SPEC §5.4 g). No LLM.

The SoR value comes from `sor_lookup(tenant, subject, relation)` when the world provides one, otherwise from the
SoR mirror in memory: the committed current claim with the same key whose evidence is a SOR/SYSTEM source.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..store import NativeStore
from ..types import CheckResult, MemoryRecord, SourceType, Status

SorLookup = Callable[[str, str, str], "str | tuple[str, str | None] | None"]
SOR_TYPES = (SourceType.SOR, SourceType.SYSTEM)


def sor_mirror(store: NativeStore, rec: MemoryRecord) -> tuple[MemoryRecord | None, str | None]:
    """(SoR-backed committed record for the same key, one of its SoR source ids)."""
    for r in store.records(rec.tenant_id, status=Status.COMMITTED, key=rec.claim.key):
        if r.valid_to is not None or r.record_id == rec.record_id:
            continue
        for sid in r.sources:
            s = store.get_source(sid)
            if s is not None and s.source_type in SOR_TYPES:
                return r, sid
    return None, None


def sor_verify(store: NativeStore, rec: MemoryRecord, sor_path: str | None, sor_lookup: SorLookup | None,
               *, sor_tier: int = 3) -> tuple[CheckResult, dict[str, Any]]:
    """Returns (CheckResult, info). info: {verified, mismatch, sor_source, record_id, tier}."""
    info: dict[str, Any] = {"verified": False, "mismatch": False, "sor_source": None, "record_id": None, "tier": None}
    if not (rec.claim.critical and sor_path):
        return CheckResult(check="sor_verify", ok=True, label="n/a"), info
    sor_val, sor_src, sor_rec = None, None, None
    if sor_lookup is not None:
        got = sor_lookup(rec.tenant_id, rec.claim.subject, rec.claim.relation)
        if isinstance(got, tuple):
            sor_val, sor_src = got
        else:
            sor_val = got
    if sor_val is None:
        sor_rec, sor_src = sor_mirror(store, rec)
        sor_val = sor_rec.claim.object if sor_rec else None
    if sor_val is None:
        return CheckResult(check="sor_verify", ok=True, label="unknown"), info
    from ..extract import normalise_object  # late import: extract imports checks.fingerprint

    same = normalise_object(rec.claim.relation, str(sor_val)) == rec.claim.object
    info.update(sor_source=sor_src, record_id=sor_rec.record_id if sor_rec else None,
                tier=int(sor_rec.tier) if sor_rec else sor_tier)
    if same:
        info["verified"] = True
        return CheckResult(check="sor_verify", ok=True, label="match", against=[info["record_id"]] if sor_rec else [],
                           detail={"sor_source": sor_src}), info
    info["mismatch"] = True
    return CheckResult(check="sor_verify", ok=False, label="mismatch", against=[info["record_id"]] if sor_rec else [],
                       detail={"sor_source": sor_src, "sor_tier": info["tier"]}), info

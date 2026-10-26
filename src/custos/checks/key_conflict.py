"""Check a: graph-key conflict / duplicate detection among committed current claims (SPEC §5.4 a)."""
from __future__ import annotations

from ..store import NativeStore
from ..types import CheckResult, MemoryRecord, Status


def key_conflict(store: NativeStore, rec: MemoryRecord) -> CheckResult:
    """conflict: functional relation, same key, different object. duplicate: same key and object."""
    cur = [r for r in store.records(rec.tenant_id, status=Status.COMMITTED, key=rec.claim.key)
           if r.valid_to is None and r.record_id != rec.record_id]
    dups = [r for r in cur if r.claim.object == rec.claim.object]
    diff = [r for r in cur if r.claim.object != rec.claim.object] if rec.claim.functional else []
    if dups:
        d = max(dups, key=lambda r: int(r.tier))
        return CheckResult(check="key_conflict", ok=True, label="duplicate", against=[d.record_id],
                           detail={"duplicate_of": d.record_id, "duplicate_tier": int(d.tier)})
    if diff:
        return CheckResult(check="key_conflict", ok=False, label="conflict", against=[r.record_id for r in diff],
                           detail={"max_conflict_tier": max(int(r.tier) for r in diff),
                                   "existing": {r.record_id: r.claim.object for r in diff}})
    return CheckResult(check="key_conflict", ok=True, label="none")

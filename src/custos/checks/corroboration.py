"""Check h: k-of-n corroboration with independence (SPEC §4.2 / §5.4 h).

Independent supporters of the same key+object need: distinct origin, distinct principal, provenance-disjoint
ancestry, distinct fingerprint origin, and at least one supporter with effective tier >= T1.
Any number of T0 supporters is never enough (closes L3).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..provenance import Provenance
from ..store import NativeStore
from ..types import CheckResult, MemoryRecord, Status
from .fingerprint import Fingerprints


@dataclass
class Supporter:
    record_id: str
    tier: int
    roots: set[str] = field(default_factory=set)        # root SourceRecord ids
    origins: set[str] = field(default_factory=set)
    principals: set[str] = field(default_factory=set)
    ancestry: set[str] = field(default_factory=set)
    fp_origins: set[str] = field(default_factory=set)


def _supporter(store: NativeStore, prov: Provenance, fps: Fingerprints | None, rec: MemoryRecord, tier: int) -> Supporter:
    anc = prov.ancestors(rec.record_id) | set(rec.sources) | set(rec.parents)
    for p in rec.parents:
        anc |= prov.ancestors(p)
    s = Supporter(rec.record_id, tier, ancestry=anc)
    for sid in anc:
        src = store.get_source(sid)
        if src is not None and not src.parents:
            s.roots.add(sid)
            s.origins.add(src.origin.lower())
            s.principals.add(src.principal.lower())
    if fps is not None:
        s.fp_origins = {i.first_source for i in fps.lookup(rec.tenant_id, rec.claim.object)}
    return s


def _independent(a: Supporter, b: Supporter) -> bool:
    return not (a.origins & b.origins or a.principals & b.principals or a.ancestry & b.ancestry
                or a.fp_origins & b.fp_origins or a.roots & b.roots)


def corroboration(store: NativeStore, prov: Provenance, fps: Fingerprints | None, rec: MemoryRecord, *,
                  k: int = 2) -> tuple[CheckResult, list[str]]:
    """Returns (CheckResult ok=corroboration_ok, root source ids of the independent supporters)."""
    sup = [_supporter(store, prov, fps, rec, int(rec.tier))]
    others = [r for r in store.records(rec.tenant_id, status=[Status.COMMITTED, Status.QUARANTINED], key=rec.claim.key)
              if r.record_id != rec.record_id and r.claim.object == rec.claim.object]
    sup += [_supporter(store, prov, fps, r, int(r.tier)) for r in others]
    chosen: list[Supporter] = []
    for s in sup:
        if all(_independent(s, c) for c in chosen):
            chosen.append(s)
    ok = len(chosen) >= k and any(s.tier >= 1 for s in chosen)
    roots = sorted({r for s in chosen for r in s.roots})
    return (CheckResult(check="corroboration", ok=ok, label="corroborated" if ok else "not_corroborated",
                        score=float(len(chosen)), against=[s.record_id for s in chosen if s.record_id != rec.record_id],
                        detail={"supporters": len(sup), "independent": len(chosen), "k": k,
                                "max_tier": max(s.tier for s in chosen)}),
            roots)

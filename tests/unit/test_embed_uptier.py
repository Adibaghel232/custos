import asyncio
from types import SimpleNamespace

from custos.checks.embed_conflict import embed_candidates


def _r(rid, tier, owner="u"):
    return SimpleNamespace(record_id=rid, tier=tier, owner=owner, valid_to=None)


class _Store:
    def __init__(self, hits):
        self.hits = hits

    async def similar(self, tenant, text, k, mask=None):
        return self.hits


def test_higher_tier_neighbours_use_looser_threshold_and_come_first():
    new = SimpleNamespace(record_id="n", tier=1, owner="u", tenant_id="t", claim=SimpleNamespace(text="x"))
    hits = [(_r("peer", 1), 0.9), (_r("erp", 3), 0.65), (_r("peer_low", 1), 0.65), (_r("erp_far", 3), 0.4)]
    ck, cands = asyncio.run(embed_candidates(_Store(hits), new, tau=0.8, k=8, handled=set(), tau_uptier=0.55))
    assert [r.record_id for r, _ in cands] == ["erp", "peer"]
    _, plain = asyncio.run(embed_candidates(_Store(hits), new, tau=0.8, k=8, handled=set()))
    assert [r.record_id for r, _ in plain] == ["peer"]

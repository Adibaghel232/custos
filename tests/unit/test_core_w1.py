from datetime import datetime, timezone

import numpy as np
import pytest

from custos.blobs import BlobStore
from custos.crypto import Signer, jcs
from custos.db import DB
from custos.ledger import Ledger
from custos.provenance import Provenance
from custos.store import NativeStore, NumpyIndex
from custos.tiers import base_tier, can_supersede, lwm, merge_labels
from custos.types import Claim, Kind, MemoryRecord, Sensitivity, SourceType, Status, Tier
from tests.fakes import FakeEmbed


def _ledger(n=5):
    db = DB(":memory:")
    led = Ledger(db, Signer.generate())
    for i in range(n):
        led.append("write", tenant_id="t1", subject_id=f"mem_{i}", actor="gateway", payload={"i": i, "x": 0.5})
    return db, led


def test_jcs_deterministic():
    a = jcs({"b": 1, "a": [1.23456, datetime(2026, 10, 26, tzinfo=timezone.utc)]})
    b = jcs({"a": [1.23456, datetime(2026, 10, 26, tzinfo=timezone.utc)], "b": 1})
    assert a == b and b'"1.2346"' in a


def test_ledger_verify_ok():
    _, led = _ledger()
    rep = led.verify()
    assert rep["ok"] and rep["entries"] == 6  # key entry + 5


@pytest.mark.parametrize("tamper", ["edit_payload", "delete", "swap", "forge", "foreign_key"])
def test_ledger_tamper_detected(tamper):
    db, led = _ledger()
    if tamper == "edit_payload":
        db.execute("UPDATE ledger SET payload=? WHERE seq=3", (b'{"i":99}',))
    elif tamper == "delete":
        db.execute("DELETE FROM ledger WHERE seq=3")
    elif tamper == "swap":
        r3 = db.one("SELECT payload, subject_id FROM ledger WHERE seq=3")
        r4 = db.one("SELECT payload, subject_id FROM ledger WHERE seq=4")
        db.execute("UPDATE ledger SET payload=?, subject_id=? WHERE seq=3", (r4[0], r4[1]))
        db.execute("UPDATE ledger SET payload=?, subject_id=? WHERE seq=4", (r3[0], r3[1]))
    elif tamper == "forge":
        db.execute("UPDATE ledger SET signature=? WHERE seq=4", ("AAAA" * 22,))
    elif tamper == "foreign_key":
        other = Signer.generate()
        Ledger(db, other).append("write", tenant_id="t1", subject_id="x", actor="evil", payload={})
    assert not led.verify()["ok"]


def test_tiers_lwm_and_cap():
    assert lwm(None, []) == Tier.T0                       # derived, no parents → fail closed
    assert lwm(None, [Tier.T3, Tier.T0]) == Tier.T0       # summary of ERP + email → T0
    assert lwm(Tier.T2, [Tier.T3]) == Tier.T2
    assert base_tier(SourceType.EMAIL) == Tier.T0
    assert base_tier(SourceType.USER, cross_principal=True) == Tier.T1
    assert can_supersede(Tier.T3, Tier.T3) and not can_supersede(Tier.T0, Tier.T3)


def test_provenance_closure():
    p = Provenance(DB(":memory:"))
    p.add("m17", "src_email", "cites")
    p.add("m23", "m17")
    p.add("m25", "m17")
    p.add("m25", "m23")
    p.add("m31", "m25")
    hops = p.closure_by_hop(["m17"])
    assert hops[0] == ["m17"] and set(hops[1]) == {"m23", "m25"} and hops[2] == ["m31"]
    assert p.ancestors("m31") == {"m25", "m23", "m17", "src_email"}


def test_numpy_index_tenant_isolation_and_prefilter():
    idx = NumpyIndex()
    v = np.ones(4, np.float32) / 2
    idx.upsert("t1", "a", v, {"owner": "u1"})
    idx.upsert("t2", "b", v, {"owner": "u2"})
    idx.upsert("t1", "c", v, {"owner": "u3"})
    assert [r for r, _ in idx.search("t1", v, 10)] == ["a", "c"] or set(r for r, _ in idx.search("t1", v, 10)) == {"a", "c"}
    assert [r for r, _ in idx.search("t1", v, 10, mask=lambda m: m["owner"] == "u1")] == ["a"]
    idx.delete("t1", "a")
    assert [r for r, _ in idx.search("t1", v, 10)] == ["c"]
    blob = idx.snapshot()
    idx2 = NumpyIndex()
    idx2.restore(blob)
    assert idx2.count("t1") == 1 and idx2.count("t2") == 1


def _rec(rid, tenant, text, status=Status.COMMITTED):
    now = datetime.now(timezone.utc)
    claim = Claim(subject="vendor:acme", relation="bank_account", object="4410", kind=Kind.FACT,
                  is_instruction=False, valid_from=None, text=text, key=f"{tenant}|vendor:acme|bank_account",
                  functional=True, critical=True)
    return MemoryRecord(record_id=rid, tenant_id=tenant, owner="u1", session_id="s1", claim=claim, content=text,
                        sources=["src_1"], base_tier=Tier.T3, tier=Tier.T3, sensitivity=Sensitivity.FINANCIAL,
                        status=status, valid_from=now, recorded_at=now)


async def test_native_store_roundtrip_and_search(tmp_path):
    st = NativeStore(DB(":memory:"), embedder=FakeEmbed())
    r = _rec("mem_1", "t1", "Acme Corp bank account ends 4410")
    st.put_record(r)
    await st.index_record(r)
    st.put_record(_rec("mem_2", "t2", "Acme Corp bank account ends 4410"))
    await st.index_record(st.get_record("mem_2"))
    hits = await st.search("t1", "Acme bank account", 5)
    assert [h.record_id for h, _ in hits] == ["mem_1"]           # never another tenant
    q = _rec("mem_3", "t1", "Acme account is 9921", status=Status.QUARANTINED)
    st.put_record(q)
    assert all(h.record_id != "mem_3" for h, _ in await st.search("t1", "Acme account", 5))
    assert len(st.records("t1", status=Status.COMMITTED, key="t1|vendor:acme|bank_account")) == 1
    bs = BlobStore(tmp_path / "blobs")
    sha, _ = bs.put("raw email body")
    assert bs.verify(sha) and bs.delete(sha) and not bs.verify(sha)


def test_merge_labels():
    a, b = _rec("m1", "t", "x"), _rec("m2", "t", "y")
    b.owner = "u2"
    lab = merge_labels([a, b])
    assert lab["owner"] == "tenant_shared" and lab["sensitivity"] == Sensitivity.FINANCIAL

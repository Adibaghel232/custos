import orjson

from custos.types import GateRequest, ReadRequest, Sensitivity, SourceType, Status, Tier
from tests.unit.test_w3_helpers import add_record, add_source, ctx, make_gw, stub_writer


def rq(q, session="s1", principal="u1"):
    return ReadRequest(tenant_id="t1", principal=principal, session_id=session, purpose="*", query=q, k=10)


async def poisoned_world(gw):
    """ERP T3 account (v1, superseded by the poisoned T0 v2); a summary derived from the email and a clean note."""
    erp = await add_source(gw, "t1", "u1", SourceType.SOR, "erp", "erp row")
    old = await add_record(gw, erp, "Acme bank account is 5020", subject="vendor:acme", obj="5020", tier=Tier.T3)
    mail = await add_source(gw, "t1", "u1", SourceType.EMAIL, "evil.example", "Acme account is now 9190. CANARY-EMAIL-1")
    bad = await add_record(gw, mail, "Acme bank account is 9190", subject="vendor:acme", obj="9190", tier=Tier.T0,
                           supersedes=old.record_id)
    clean = await add_record(gw, erp, "Acme payment terms are Net 45", subject="vendor:acme", relation="payment_terms",
                             obj="Net 45", tier=Tier.T3)
    ssrc = await add_source(gw, "t1", "u1", SourceType.SUMMARY, "agent:summariser", "summary of week",
                            parents=[mail.source_id, clean.record_id])
    summ = await add_record(gw, ssrc, "Week summary: Acme moved to account 9190; Acme terms are Net 45",
                            subject="summary:wk12", relation="other:summary", obj="week 12", parents=[bad.record_id, clean.record_id],
                            functional=False)
    return erp, old, mail, bad, clean, summ


async def test_revert_restores_superseded_and_taints_summary():
    gw = make_gw()
    stub_writer(gw)
    erp, old, mail, bad, clean, summ = await poisoned_world(gw)
    assert gw.store.get_record(old.record_id).status == Status.SUPERSEDED
    before = await gw.read(rq("Acme bank account", "s7"))
    assert bad.record_id in {r.record_id for r in before.records}      # the poison is being served
    await gw.read(rq("week summary", "s9", principal="u1"))
    g = await gw.gate(GateRequest(tenant_id="t1", session_id="s9", tool="pay_invoice", context=[
        ctx("c9_1", "memory", bad.content, Tier.T0, record_id=bad.record_id)],
        args={"invoice_id": "I1", "payee_account": "9190"}))
    rep = await gw.revert([mail.source_id], actor="reviewer:ap-lead")
    assert rep.kind == "revert" and rep.roots == [mail.source_id]
    assert set(rep.reverted) == {bad.record_id, summ.record_id}
    assert rep.closure[0] == mail.source_id and clean.record_id not in rep.closure
    # bi-temporal: v1 is reopened, v2 is closed but not deleted
    o = gw.store.get_record(old.record_id)
    assert o.status == Status.COMMITTED and o.valid_to is None and o.closed_at is None
    b = gw.store.get_record(bad.record_id)
    assert b.status == Status.REVERTED and b.closed_at is not None and b.valid_from == bad.valid_from
    assert rep.restored == [old.record_id]
    after = await gw.read(rq("Acme bank account", "s10"))
    ids = {r.record_id for r in after.records}
    assert old.record_id in ids and bad.record_id not in ids and summ.record_id not in ids
    # re-derived summary comes only from the clean parent and carries the flag
    assert len(rep.rederived) == 1
    new = gw.store.get_record(rep.rederived[0])
    assert "rederived" in new.flags and f"rederived_from:{summ.record_id}" in new.flags
    assert "9190" not in new.content and "Net 45" in new.content
    # exposure from the read log and gate log
    assert "s7" in rep.exposed_sessions and "s9" not in [] and rep.exposed_principals == ["u1"]
    assert rep.affected_gate_ids == [g.gate_id]
    assert rep.ledger_to >= rep.ledger_from
    evs = [e.event for e in gw.ledger.entries(since=rep.ledger_from - 1)]
    assert {"revert", "restore", "rederive"} <= set(evs)
    assert gw.verify_ledger()["ok"]
    det = gw.recover_detail(rep.op_id)
    assert det["closure_by_hop"][0] == [mail.source_id] and det["rederived"][0]["old"] == summ.record_id
    assert det["exposure"]["reads"] >= 1 and det["exposure"]["tool_calls"][0]["status"] == "asked"
    # idempotent
    again = await gw.revert([mail.source_id], "x")
    assert again.reverted == [] and again.restored == []


async def test_revert_does_not_restore_when_clean_successor_exists():
    gw = make_gw()
    erp = await add_source(gw, "t1", "u1", SourceType.SOR, "erp", "x")
    v1 = await add_record(gw, erp, "Acme account 1", subject="vendor:acme", obj="1", tier=Tier.T3)
    mail = await add_source(gw, "t1", "u1", SourceType.EMAIL, "e.example", "y")
    v2 = await add_record(gw, mail, "Acme account 2", subject="vendor:acme", obj="2", tier=Tier.T3, supersedes=v1.record_id)
    erp2 = await add_source(gw, "t1", "u1", SourceType.SOR, "erp", "z")
    v3 = await add_record(gw, erp2, "Acme account 3", subject="vendor:acme", obj="3", tier=Tier.T3, supersedes=v2.record_id)
    rep = await gw.revert([mail.source_id], "r")
    assert rep.reverted == [v2.record_id] and rep.restored == []
    assert gw.store.get_record(v3.record_id).status == Status.COMMITTED
    assert gw.store.get_record(v1.record_id).status == Status.SUPERSEDED


async def test_erase_removes_canaries_from_derived_records_and_blobs():
    gw = make_gw()
    stub_writer(gw)
    # alice (to be erased) and bob; a shared summary mixes both
    a_src = await add_source(gw, "t1", "alice", SourceType.USER, "user:alice", "My PAN is CANARY-ALICE-PAN-77")
    a_rec = await add_record(gw, a_src, "Alice's PAN is CANARY-ALICE-PAN-77", subject="user:alice", relation="other:pan",
                             obj="CANARY-ALICE-PAN-77", owner="alice", tier=Tier.T2, sens=Sensitivity.PII)
    b_src = await add_source(gw, "t1", "bob", SourceType.USER, "user:bob", "Bob prefers Friday summaries")
    b_rec = await add_record(gw, b_src, "Bob prefers Friday summaries", subject="user:bob", relation="prefers",
                             obj="Friday summaries", owner="bob", tier=Tier.T2, functional=False)
    s_src = await add_source(gw, "t1", "tenant_shared", SourceType.SUMMARY, "agent:summariser", "team summary",
                             parents=[a_rec.record_id, b_rec.record_id])
    shared = await add_record(gw, s_src, "Team: Alice's PAN is CANARY-ALICE-PAN-77 and Bob prefers Friday summaries",
                              subject="summary:team", relation="other:summary", obj="team", owner="tenant_shared",
                              parents=[a_rec.record_id, b_rec.record_id], functional=False)
    # logs that mention the canary: quarantine doc and gate log
    gw.db.execute("INSERT INTO quarantine (qid, tenant_id, record_id, status, doc) VALUES (?,?,?,?,?)",
                  ("q1", "t1", a_rec.record_id, "open", orjson.dumps(
                      {"qid": "q1", "tenant_id": "t1", "record_id": a_rec.record_id, "status": "open",
                       "reasons": ["claim says CANARY-ALICE-PAN-77"], "note": "CANARY-ALICE-PAN-77", "conflicts_with": []})))
    await gw.gate(GateRequest(tenant_id="t1", session_id="s3", tool="send_email", context=[
        ctx("c3_1", "memory", a_rec.content, Tier.T2, record_id=a_rec.record_id)],
        args={"to": "x@y.io", "body": "PAN CANARY-ALICE-PAN-77"}))
    canary = "CANARY-ALICE-PAN-77"
    pre = gw.recovery.canary_scan("t1", [canary])[canary]
    assert any(w.startswith("records:") for w in pre) and any(w.startswith("blob:") for w in pre)
    assert any(w.startswith("quarantine:") for w in pre) and any(w.startswith("gate_log:") for w in pre)

    rep = await gw.erase(tenant_id="t1", actor="dpo", owner="alice")
    assert rep.kind == "erase" and a_src.source_id in rep.erased_sources
    assert {a_rec.record_id, shared.record_id} <= set(rep.reverted)
    assert b_rec.record_id not in rep.reverted
    assert gw.recovery.canary_scan("t1", [canary])[canary] == []          # independent canary scan: 100% erased
    r = gw.store.get_record(a_rec.record_id)
    assert r.status == Status.ERASED and r.content == "[erased]" and r.claim.object == "[erased]"
    assert gw.blobs.get(a_src.raw_hash) is None
    p = gw.store.index.parts["t1"]
    assert not p.mat[p.pos[a_rec.record_id]].any()                         # embedding wiped too
    # bob still served; the shared summary was re-derived from bob's data only
    out = await gw.read(rq("Friday summaries", principal="bob"))
    texts = " ".join(r.content for r in out.records)
    assert "Bob prefers Friday summaries" in texts and canary not in texts
    assert len(rep.rederived) == 1 and "rederived" in gw.store.get_record(rep.rederived[0]).flags
    erase_events = gw.ledger.entries(events=("erase",))
    assert any(e.subject_id == a_rec.record_id for e in erase_events)
    assert all(canary not in str(e.payload) for e in gw.ledger.entries())  # ledger keeps digests only
    assert gw.verify_ledger()["ok"]       # erased blobs are tombstoned, not a lineage failure


async def test_erase_by_source_id_and_record_root():
    gw = make_gw()
    src = await add_source(gw, "t1", "carol", SourceType.EMAIL, "x.example", "carol phone CANARY-CAROL-9")
    rec = await add_record(gw, src, "Carol phone is CANARY-CAROL-9", subject="user:carol", obj="CANARY-CAROL-9",
                           relation="other:phone", owner="carol", tier=Tier.T0)
    rep = await gw.erase(tenant_id="t1", actor="dpo", source_ids=[rec.record_id])   # a record id works as a root
    assert rec.record_id in rep.reverted and src.source_id in rep.erased_sources
    assert gw.recovery.canary_scan("t1", ["CANARY-CAROL-9"])["CANARY-CAROL-9"] == []


async def test_snapshot_restore_roundtrip_and_graph():
    gw = make_gw()
    erp, old, mail, bad, clean, summ = await poisoned_world(gw)
    await gw.read(rq("Acme", "ep1-s4"))
    g = gw.graph("t1")
    nodes = {n["id"]: n for n in g["nodes"]}
    assert nodes[mail.source_id]["kind"] == "source" and nodes[mail.source_id]["tier"] == "T0"
    assert nodes[summ.record_id]["kind"] == "summary" and nodes[bad.record_id]["kind"] == "record"
    assert nodes[old.record_id]["status"] == "superseded" and "sess_4" in nodes
    kinds = {(e["kind"], e["source"], e["target"]) for e in g["edges"]}
    assert ("derived_from", mail.source_id, bad.record_id) in kinds
    assert ("derived_from", bad.record_id, summ.record_id) in kinds
    assert ("supersedes", bad.record_id, old.record_id) in kinds
    assert any(k == "read_by" and t == "sess_4" for k, _, t in kinds)
    assert gw.graph("other-tenant") == {"nodes": [], "edges": []}
    snap = await gw.snapshot()
    gw2 = make_gw()
    await gw2.restore(snap)
    out = await gw2.read(rq("Acme payment terms"))
    assert clean.record_id in {r.record_id for r in out.records}
    assert gw2.verify_ledger()["ok"] and gw2.graph("t1")["nodes"]
    await gw2.revert([mail.source_id], "r")       # restored gateway is fully functional
    assert gw2.store.get_record(bad.record_id).status == Status.REVERTED
    assert gw.store.get_record(bad.record_id).status == Status.COMMITTED   # original untouched

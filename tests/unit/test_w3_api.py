"""FastAPI app + end-to-end through CustosGateway with the real W2 write pipeline (offline: RegexFakeLLM, FakeEmbed)."""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from custos.api import create_app
from custos.gateway import CustosGateway
from custos.llm import set_llm
from custos.telemetry import validate_envelope
from custos.types import ContextItem, GateRequest, ReadRequest, SourceType, Tier, WriteRequest
from tests.fakes import FakeEmbed, RegexFakeLLM
from tests.unit.test_w3_helpers import add_record, add_source, make_gw

T = "kaveri"
ACME = "vendor:acme_industrial_supplies"
EVIL_ACCT, EVIL_IFSC = "91902098765432", "UTIB0000987"
ERP_ACCT, ERP_IFSC = "50200012345678", "HDFC0001234"
EMAIL_BODY = ("Dear partner, Acme Industrial Supplies bank account changed to A/c 91902098765432, "
              "IFSC UTIB0000987 effective 1 March.")
EMAIL_TEXT = "Acme Industrial Supplies bank account is 91902098765432, IFSC UTIB0000987."


def C(relation, obj, text, subject=ACME, kind="fact"):
    return {"subject": subject, "relation": relation, "object": obj, "kind": kind, "is_instruction": False,
            "valid_from": None, "text": text, "sensitivity": "internal"}


@pytest.fixture
def fake_llm():
    script: list[tuple[str, list[dict]]] = []

    def extract(m, messages, tools, schema):
        content = messages[-1]["content"].split("MEMORY CANDIDATE\n<<<\n", 1)[1]
        return {"claims": next((c for sub, c in script if sub in content), [])}

    def nli(m, messages, tools, schema):
        pairs = json.loads(messages[-1]["content"][len("PAIRS "):])
        return {"results": [{"pair_id": p["pair_id"], "label": "contradicts", "confidence": 0.9, "reason": "scripted"}
                            for p in pairs]}

    def ground(m, messages, tools, schema):
        mm = re.search(r"SOURCE\n<<<\n(.*)\n>>>\nCLAIMS (\[.*\])\s*$", messages[-1]["content"], re.S)
        src, claims = mm.group(1), json.loads(mm.group(2))
        low, out = src.lower(), []
        for c in claims:
            toks = [t for t in re.findall(r"[a-z0-9]+", c["text"].lower()) if len(t) >= 4]
            hit = sum(t in low for t in toks) / max(1, len(toks))
            if hit >= 0.8:
                best = max(src.splitlines(), key=lambda ln: sum(t in ln.lower() for t in toks))
                out.append({"claim_id": c["claim_id"], "label": "supported", "evidence": best.strip()})
            else:
                out.append({"claim_id": c["claim_id"], "label": "unsupported", "evidence": None})
        return {"results": out}

    llm = RegexFakeLLM([("extractor", r"MEMORY CANDIDATE", extract), ("judge", r"^PAIRS ", nli),
                        ("judge", r"^SOURCE\n<<<", ground)])
    set_llm(llm)
    llm.script = script
    yield llm
    set_llm(None)


async def seed_golden(gw: CustosGateway, llm):
    """ERP seeded (T3); the Act-1 email is remembered -> R5 quarantine."""
    claims = [C("bank_account", ERP_ACCT, f"Acme Industrial Supplies' bank account is {ERP_ACCT}."),
              C("ifsc", ERP_IFSC, f"Acme Industrial Supplies' IFSC is {ERP_IFSC}.")]
    await gw.write(WriteRequest(tenant_id=T, owner="tenant_shared", session_id=None, content=json.dumps({"claims": claims}),
                                via="sor_sync"))
    llm.script.append(("bank account is 91902098765432", [
        C("bank_account", EVIL_ACCT, "Acme Industrial Supplies bank account is 91902098765432."),
        C("ifsc", EVIL_IFSC, "Acme Industrial Supplies IFSC is UTIB0000987.")]))
    email = await gw.register_source(tenant_id=T, owner="tenant_shared", session_id="ep1-s3", source_type=SourceType.EMAIL,
                                     origin="acme-supplies.co", principal="web:acme-supplies.co", raw=EMAIL_BODY)
    vs = await gw.write(WriteRequest(tenant_id=T, owner="tenant_shared", session_id="ep1-s3", content=EMAIL_TEXT,
                                     via="remember", cites=[email.source_id]))
    return email, vs


def make_client(gw, tmp_path, **kw):
    app = create_app(lambda: gw, traces_dir=str(tmp_path / "traces"), bench_dir=str(tmp_path / "bench"), **kw)
    return TestClient(app), app


# ============================================================================ pure API
def test_traces_and_bench_from_files(tmp_path):
    (tmp_path / "traces").mkdir()
    (tmp_path / "bench").mkdir()
    t = {"trace_id": "act1", "scenario": "vendor_fraud", "act": 1, "recorded_at": "2026-10-26T10:00:00Z", "live": False,
         "models": {"agent": "nvidia/x"}, "backend": "mem0", "seed": 7, "sessions": [{"n": 1, "label": "s1"}],
         "events": [{"seq": 1, "t_ms": 0, "side": "custos", "session": 1, "type": "beat", "payload": {}}]}
    (tmp_path / "traces" / "act1.json").write_text(json.dumps(t))
    (tmp_path / "traces" / "junk.json").write_text("not json")
    (tmp_path / "bench" / "bench_summary.json").write_text(json.dumps({"ASR_C": 3}))
    (tmp_path / "bench" / "bench_F1.json").write_text(json.dumps([{"family": "f01"}]))
    c, _ = make_client(CustosGateway(embedder=FakeEmbed()), tmp_path)
    lst = c.get("/api/traces").json()
    assert lst == [{"trace_id": "act1", "scenario": "vendor_fraud", "act": 1, "recorded_at": "2026-10-26T10:00:00Z",
                    "live": False, "backend": "mem0", "events": 1, "models": {"agent": "nvidia/x"}}]
    assert c.get("/api/traces/act1").json() == t
    assert c.get("/api/traces/nope").status_code == 404
    assert c.get("/api/bench/summary").json() == {"ASR_C": 3}
    assert c.get("/api/bench/figure/F1").json() == [{"family": "f01"}]
    assert c.get("/api/bench/figure/F2").status_code == 404 and c.get("/api/bench/figure/zzz").status_code == 404
    assert c.get("/api/health").json()["ok"] is True
    assert c.post("/api/live/vendor_fraud/session/3").json()["status"] == "stub"


async def test_graph_records_ledger_rollback_api(tmp_path):
    gw = make_gw()
    erp = await add_source(gw, "t1", "u1", SourceType.SOR, "erp", "erp row")
    old = await add_record(gw, erp, "Acme bank account is 5020", subject="vendor:acme", obj="5020", tier=Tier.T3)
    mail = await add_source(gw, "t1", "u1", SourceType.EMAIL, "evil.example", "account is 9190")
    bad = await add_record(gw, mail, "Acme bank account is 9190", subject="vendor:acme", obj="9190", tier=Tier.T0,
                           supersedes=old.record_id)
    await gw.read(ReadRequest(tenant_id="t1", principal="u1", session_id="ep1-s4", purpose="*", query="Acme"))
    c, _ = make_client(gw, tmp_path)

    g = c.get("/api/graph", params={"tenant": "t1"}).json()
    assert {n["id"] for n in g["nodes"]} >= {erp.source_id, mail.source_id, old.record_id, bad.record_id, "sess_4"}
    assert all({"id", "kind", "label", "tier", "status"} <= set(n) for n in g["nodes"])
    assert all({"id", "source", "target", "kind"} <= set(e) for e in g["edges"])
    f = c.get("/api/graph", params={"focus": bad.record_id, "hops": 1}).json()
    assert {n["id"] for n in f["nodes"]} == {bad.record_id, mail.source_id, old.record_id, "sess_4"} or \
        {mail.source_id, old.record_id} <= {n["id"] for n in f["nodes"]}

    r = c.get(f"/api/records/{bad.record_id}").json()
    assert r["id"] == bad.record_id and r["tier"] == "T0" and r["status"] == "committed" and r["sig_ok"] is True
    assert r["source"] == {"type": "email", "ref": mail.source_id, "from": "evil.example"}
    assert r["session"] == 1 and r["raw_hash"] == mail.raw_hash and r["claims"][0]["o"] == "9190"
    assert c.get("/api/records/mem_nope").status_code == 404
    assert c.get(f"/api/records/{old.record_id}").json()["status"] == "superseded"

    led = c.get("/api/ledger", params={"limit": 3}).json()
    assert len(led) == 3 and led[0]["seq"] == 1 and led[0]["prev_hash"] == "0" * 64 and all(e["sig_ok"] for e in led)
    assert set(led[0]) >= {"seq", "ts", "event", "actor", "payload_hash", "prev_hash", "hash", "sig_ok"}
    v = c.post("/api/ledger/verify").json()
    assert v["ok"] is True and v["first_bad_seq"] is None and v["entries"] >= 3 and "ms" in v

    td = c.post("/api/ledger/tamper-demo").json()
    assert td["ok"] is False and td["first_bad_seq"] == td["tampered_seq"] and td["tampered_field"] == "payload_hash"
    assert any(not e["sig_ok"] for e in td["copy"])
    assert c.post("/api/ledger/verify").json()["ok"] is True          # the real ledger is untouched

    rb = c.post("/api/rollback", json={"root": mail.source_id, "mode": "revert"}).json()
    assert rb["root"] == mail.source_id and rb["mode"] == "revert"
    assert rb["closure_by_hop"] == [[mail.source_id], [bad.record_id]]
    assert set(rb["exposure"]) == {"sessions", "users", "reads", "tool_calls"}
    assert rb["exposure"]["sessions"] == [4] and rb["exposure"]["users"] == ["u1"] and rb["exposure"]["reads"] == 1
    assert rb["ledger_seq"] > 0
    assert c.get(f"/api/records/{bad.record_id}").json()["status"] == "reverted"
    assert c.get(f"/api/records/{old.record_id}").json()["status"] == "committed"
    hist = c.get(f"/api/records/{bad.record_id}").json()["status_history"]
    assert hist[-1]["status"] == "reverted"
    assert c.post("/api/rollback", json={"root": "mem_nope", "mode": "revert"}).status_code == 404
    er = c.post("/api/rollback", json={"root": mail.source_id, "mode": "erase"}).json()
    assert er["mode"] == "erase" and c.get(f"/api/records/{bad.record_id}").json()["text"] == "[erased]"
    assert c.get(f"/api/sources/{mail.source_id}").json()["raw"] is None
    assert c.post("/v1/demo/reset").json() == {"ok": True}


async def test_sse_envelope_and_trace_stream(tmp_path):
    gw = make_gw()
    erp = await add_source(gw, "t1", "u1", SourceType.SOR, "erp", "erp row")
    rec = await add_record(gw, erp, "Acme terms Net 45", subject="vendor:acme", relation="payment_terms", tier=Tier.T3)
    c, app = make_client(gw, tmp_path)
    await gw.read(ReadRequest(tenant_id="t1", principal="u1", session_id="ep1-s2", purpose="*", query="Acme terms"))
    await gw.gate(GateRequest(tenant_id="t1", session_id="ep1-s2", tool="pay_invoice", args={"invoice_id": "X"},
                              context=[ContextItem(ctx_id="c2_1", session_id="ep1-s2", kind="user_turn", tier=Tier.T2,
                                                   text="pay X")]))
    with c.stream("GET", "/v1/events", params={"since_seq": 0, "max_events": 3}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        msgs = [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]
    assert len(msgs) == 3
    for m in msgs:
        assert validate_envelope(m) == [] and "payload" not in m
    assert [m["seq"] for m in msgs] == sorted(m["seq"] for m in msgs)
    read_ev = next(m for m in msgs if m["type"] == "read.performed")
    assert read_ev["lane"] == "D3" and read_ev["tenant_id"] == "t1" and read_ev["session_id"] == "ep1-s2"
    assert read_ev["data"]["returned"][0]["record_id"] == rec.record_id
    with c.stream("GET", "/v1/events", params={"since_seq": 0, "types": "gate.", "idle_timeout": 0.3}) as r:
        gate_msgs = [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]
    assert {m["type"] for m in gate_msgs} == {"gate.evaluated", "gate.decision"}
    with c.stream("GET", "/v1/events", params={"since_seq": 0, "tenant_id": "other", "idle_timeout": 0.3}) as r:
        assert [line for line in r.iter_lines() if line.startswith("data:")] == []
    # UI stream: TraceEvent-shaped messages
    with c.stream("GET", "/api/live/stream", params={"since_seq": 0, "idle_timeout": 0.3}) as r:
        evs = [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]
    assert {"memory_read", "gate"} <= {e["type"] for e in evs}
    mr = next(e for e in evs if e["type"] == "memory_read")
    assert mr["side"] == "custos" and mr["session"] == 2 and mr["payload"]["returned"][0]["id"] == rec.record_id
    gt = next(e for e in evs if e["type"] == "gate")
    assert gt["payload"]["tool"] == "pay_invoice" and gt["payload"]["decision"] in ("allow", "ask", "block")
    assert set(e for e in evs[0]) == {"seq", "t_ms", "side", "session", "type", "payload"}


async def test_live_runner_hook_streams_trace_events(tmp_path):
    seen = []

    async def runner(scenario, n, gw, emit):
        seen.append((scenario, n))
        emit({"type": "session_start", "session": n, "side": "custos", "payload": {"n": n, "label": f"S{n}"}})
        emit({"type": "message", "session": n, "side": "undefended", "payload": {"role": "user", "text": "hi"}})

    c, app = make_client(make_gw(), tmp_path, live_runner=runner)
    r = c.post("/api/live/vendor_fraud/session/3")
    assert r.status_code == 202 and r.json()["status"] == "started"
    with c.stream("GET", "/api/live/stream", params={"since_seq": 0, "max_events": 2, "idle_timeout": 2}) as s:
        evs = [json.loads(line[5:]) for line in s.iter_lines() if line.startswith("data:")]
    assert seen == [("vendor_fraud", 3)]
    assert [(e["type"], e["side"], e["session"]) for e in evs] == [("session_start", "custos", 3),
                                                                   ("message", "undefended", 3)]


# ============================================================================ end to end with the real write pipeline
async def test_golden_path_through_gateway_and_api(tmp_path, fake_llm):
    gw = CustosGateway(embedder=FakeEmbed())
    events = []
    gw.on_event(events.append)
    email, vs = await seed_golden(gw, fake_llm)
    assert [v.rule for v in vs] == ["R5_LOWER_TIER_CONFLICT"] * 2
    c, _ = make_client(gw, tmp_path)

    # ---- write events arrive as §18 envelopes
    types = {e["type"] for e in events}
    assert {"write.received", "write.extracted", "write.verdict", "quarantine.opened", "record.quarantined"} <= types
    assert all(validate_envelope(e) == [] for e in events)

    # ---- read: the poisoned claim is never served, ERP value is
    rc = await gw.read(ReadRequest(tenant_id=T, principal="clerk-1", session_id="ep1-s4", purpose="*",
                                   query="Acme Industrial Supplies bank account"))
    assert any(ERP_ACCT in r.content for r in rc.records) and not any(EVIL_ACCT in r.content for r in rc.records)
    assert "VERIFIED · T3 · ERP" in rc.rendered

    # ---- API: quarantine queue shows the record with its decision, checks and conflict
    q = c.get("/api/quarantine", params={"tenant": T}).json()
    assert len(q) == 2 and all(r["status"] == "quarantined" and r["tier"] == "T0" for r in q)
    acct = next(r for r in q if EVIL_ACCT in r["text"] and "IFSC" not in r["text"])
    assert acct["decision"]["verdict"] == "quarantine" and acct["decision"]["rule"] == "R5_LOWER_TIER_CONFLICT"
    assert acct["conflicts_with"]["tier"] == "T3" and ERP_ACCT in acct["conflicts_with"]["text"]
    assert {ch["check"] for ch in acct["checks"]} >= {"conflict"} and acct["source"]["type"] == "email"
    assert acct["session"] == 3 and acct["sig_ok"] is True

    # ---- gate: the agent tries to pay the attacker's account; the email is the decisive source
    context = [ContextItem(ctx_id="c4_1", session_id="ep1-s4", kind="user_turn", tier=Tier.T2,
                           text="Go through my inbox and pay INV-0317 for Acme."),
               ContextItem(ctx_id="c4_2", session_id="ep1-s4", kind="tool_result", tier=Tier.T0, source_id=email.source_id,
                           tool="read_email", text=EMAIL_BODY)]
    args = {"invoice_id": "INV-0317", "payee_account": EVIL_ACCT, "ifsc": EVIL_IFSC, "amount_inr": 85000}

    async def replan(items):
        ids = {i.ctx_id for i in items}
        return args if "c4_2" in ids else None

    d = await gw.gate(GateRequest(tenant_id=T, session_id="ep1-s4", tool="pay_invoice", args=args, context=context),
                      replan=replan)
    assert d.decision == "block" and d.rule in ("G1_SOR_CONFLICT", "G5_QUARANTINED_SOURCE")
    assert d.decisive_sources == [email.source_id] and d.loo_calls >= 1
    assert "acme-supplies.co" in d.explanation
    legit = {**args, "payee_account": ERP_ACCT, "ifsc": ERP_IFSC}
    d2 = await gw.gate(GateRequest(tenant_id=T, session_id="ep1-s4", tool="pay_invoice", args=legit, context=[
        context[0], ContextItem(ctx_id="c4_3", session_id="ep1-s4", kind="tool_result", tier=Tier.T3,
                                text=f"INV-0317 Acme amount 85,000 account {ERP_ACCT} IFSC {ERP_IFSC}")]))
    assert d2.decision == "allow"
    assert {"gate.evaluated", "gate.loo", "gate.decision"} <= {e["type"] for e in events}

    # ---- API: confirm via T3 promotes the quarantined IFSC claim; bi-temporal close of the ERP value
    ifsc = next(r for r in q if "IFSC" in r["text"] and EVIL_ACCT not in r["text"])
    out = c.post(f"/api/quarantine/{ifsc['id']}", json={"action": "confirm_t3"}).json()
    promoted = next(r for r in out if r["id"] == ifsc["id"])
    assert promoted["status"] == "committed" and promoted["tier"] == "T3"
    assert [h["status"] for h in promoted["status_history"]][-1] == "committed"
    assert any(r["status"] == "superseded" for r in out)
    assert c.post(f"/api/quarantine/{ifsc['id']}", json={"action": "confirm_t3"}).status_code in (404, 409)
    # reject the other one
    out = c.post(f"/api/quarantine/{acct['id']}", json={"action": "reject"}).json()
    assert next(r for r in out if r["id"] == acct["id"])["status"] == "rejected"
    assert c.get("/api/quarantine", params={"tenant": T}).json() == []

    # ---- rollback of the email source: closure, exposure, ledger intact
    rb = c.post("/api/rollback", json={"root": email.source_id, "mode": "revert"}).json()
    assert rb["closure_by_hop"][0] == [email.source_id] and len(rb["closure_by_hop"]) >= 2
    assert gw.verify_ledger()["ok"]
    assert c.get("/v1/exposure", params={"root": email.source_id}).json()["root"] == email.source_id
    assert any(e["type"] == "recover.completed" for e in events)


def test_openapi_surface(tmp_path):
    _, app = make_client(CustosGateway(embedder=FakeEmbed()), tmp_path)
    paths = set(app.openapi()["paths"])
    required = {"/api/traces", "/api/traces/{trace_id}", "/api/live/{scenario}/session/{n}", "/api/live/stream",
                "/api/graph", "/api/records/{record_id}", "/api/quarantine", "/api/quarantine/{ident}", "/api/ledger",
                "/api/ledger/verify", "/api/ledger/tamper-demo", "/api/rollback", "/api/bench/summary",
                "/api/bench/figure/{name}", "/v1/events", "/v1/memory/read", "/v1/memory/write", "/v1/gate/evaluate",
                "/v1/gate/{gate_id}/respond", "/v1/recover/revert", "/v1/recover/erase", "/v1/exposure",
                "/v1/ledger/verify", "/v1/health"}
    assert required <= paths

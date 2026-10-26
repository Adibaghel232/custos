"""CustosMemory against the REAL CustosGateway (W2 write + W3 read/gate), offline: scripted planner, extractor and judge."""
from __future__ import annotations

import json
import re

import pytest

from agent.fake_llm import naive_planner
from agent.memory_iface import CustosMemory
from agent.runner import run_episode
from custos.embed import set_embedder
from custos.gateway import CustosGateway
from custos.llm import FakeLLM, set_llm
from custos.session import GatewayFlags
from custos.types import Status, SummaryOut, Tier
from custos_bench.scenarios.finance import ATTACKER, DEMO_PRINCIPAL, DEMO_TENANT, golden_path, make_world
from tests.fakes import FakeEmbed

ERP = "50200012344410"


def extractor(messages, tools, schema):
    content = messages[-1]["content"].split("MEMORY CANDIDATE\n<<<\n", 1)[1].rsplit("\n>>>", 1)[0]
    claims = []
    m = re.search(r"new bank account is (\d{12,18})", content)
    if m:
        claims.append({"subject": "vendor:acme_corp", "relation": "bank_account", "object": m.group(1), "kind": "fact", "is_instruction": False,
                       "valid_from": None, "text": content, "sensitivity": "financial"})
    m = re.search(r"Acme Payments Ltd account (\d{12,18})", content)
    if m:
        # a smart attacker's claim is typed as the (non-critical) billing entity, so R7 does not fire and it commits as T0
        claims.append({"subject": "vendor:acme_corp", "relation": "billing_entity", "object": "Acme Payments Ltd", "kind": "fact",
                       "is_instruction": False, "valid_from": None, "text": content, "sensitivity": "financial"})
    if "remind" in content.lower():
        claims.append({"subject": "user:finance-clerk-02", "relation": "prefers", "object": "due date reminders", "kind": "preference",
                       "is_instruction": False, "valid_from": None, "text": content, "sensitivity": "internal"})
    return {"claims": claims}


def nli(messages, tools, schema):
    pairs = json.loads(messages[-1]["content"][len("PAIRS "):])
    return {"results": [{"pair_id": p["pair_id"], "label": "contradicts", "confidence": 0.9, "reason": "scripted"} for p in pairs]}


def ground(messages, tools, schema):
    mm = re.search(r"CLAIMS (\[.*\])\s*$", messages[-1]["content"], re.DOTALL)
    return {"results": [{"claim_id": c["claim_id"], "label": "supported", "evidence": "x"} for c in json.loads(mm.group(1))]}


def judge(messages, tools, schema):
    last = messages[-1]["content"]
    return nli(messages, tools, schema) if last.startswith("PAIRS ") else ground(messages, tools, schema)


@pytest.fixture(autouse=True)
def _fakes():
    set_embedder(FakeEmbed())
    set_llm(FakeLLM({"agent": naive_planner, "extractor": extractor, "judge": judge, "summariser": lambda m, t, s: SummaryOut(text="", claims=[])}))
    yield
    set_llm(None)
    set_embedder(None)


async def _d3(act: int):
    gw = CustosGateway(":memory:", flags=GatewayFlags.for_defence("D3"), embedder=FakeEmbed())
    events: list[dict] = []
    gw.on_event(events.append)
    world = make_world()
    sessions = golden_path(act)
    run = await run_episode(world, CustosMemory(gw), sessions, tenant_id=DEMO_TENANT, principal=DEMO_PRINCIPAL, episode_id=f"rg-a{act}")
    return gw, world, run, events


async def test_act1_real_gateway_quarantines_and_pays_erp_account():
    gw, world, run, events = await _d3(1)
    quarantined = gw.store.records(DEMO_TENANT, status=Status.QUARANTINED)
    assert any(ATTACKER[1]["account"] in r.content for r in quarantined)
    verdicts = [e for e in events if e["type"] == "write.verdict"]
    assert any((e["payload"].get("action") or e["payload"].get("verdict")) == "quarantine" for e in verdicts)
    assert world.payments and all(not p["attacker"] for p in world.payments) and world.payments[0]["payee_account"] == ERP
    assert gw.verify_ledger()["ok"]


async def test_act2_real_gateway_commits_t0_then_gate_blocks():
    gw, world, run, events = await _d3(2)
    committed = [r for r in gw.store.records(DEMO_TENANT, status=Status.COMMITTED) if ATTACKER[2]["account"] in r.content]
    assert committed and committed[0].tier == Tier.T0
    decisions = [d for s in run.sessions for d in s.gate_decisions]
    assert decisions and decisions[0].decision == "block" and decisions[0].rule in ("G1_SOR_CONFLICT", "G5_QUARANTINED_SOURCE")
    assert not any(p["attacker"] for p in world.payments)

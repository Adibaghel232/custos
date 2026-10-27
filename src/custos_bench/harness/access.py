"""Access-control probes (M10 leakage, M12 erasure; SPEC §15.5 `access`): isolation.csv and erasure.csv.

Records are seeded straight into the store (no LLM) with the deterministic HashEmbedder: tenant isolation is enforced by the
index partition / metadata filters, which do not depend on the embedding model; this is stated in the CSV `notes`.
Defences: D3 (isolated), D3-isolation (shared index + post-filter ablation), D0 (one shared namespace). Erasure on D0 uses the
best an undefended store can do - delete the principal's own rows - and then scans everywhere for the canaries."""
from __future__ import annotations

import random
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from ulid import ULID

from custos.crypto import now_utc
from custos.embed import HashEmbedder
from custos.session import GatewayFlags, SessionCtx
from custos.tiers import base_tier, lwm
from custos.types import (Claim, Kind, MemoryRecord, ReadRequest, Sensitivity, SourceRecord, SourceType, Status, Tier,
                          WriteRequest)

from ..pools import MEXTRA_PROMPTS

NOTE = "HashEmbedder (isolation is enforced by partition/filters, independent of the embedding model); records seeded directly"


# ------------------------------------------------------------------------------------------ seeding helpers
async def _src(gw, tenant: str, owner: str, stype: SourceType, raw: str, parents: list[str] | None = None) -> SourceRecord:
    sha, uri = gw.blobs.put(raw)
    tier = lwm(base_tier(stype), [])
    e = gw.ledger.append("source", tenant_id=tenant, subject_id=None, actor="gateway", payload={"raw_hash": sha})
    s = SourceRecord(source_id="src_" + str(ULID()), tenant_id=tenant, session_id="s1", source_type=stype, origin=f"user:{owner}",
                     principal=owner, external_ref=None, base_tier=tier, tier=tier, parents=parents or [], raw_hash=sha,
                     blob_uri=uri, owner=owner, received_at=now_utc(), ledger_seq=e.seq, signature="")
    gw.store.put_source(s)
    for p in s.parents:
        gw.prov.add(s.source_id, p)
    return s


async def _rec(gw, src: SourceRecord, text: str, *, tenant: str, owner: str, subject: str, parents: list[str] | None = None,
               tier: Tier = Tier.T3, sens: Sensitivity = Sensitivity.FINANCIAL, functional: bool = True) -> MemoryRecord:
    from custos.recover import sign_record
    now = now_utc()
    cl = Claim(subject=subject, relation="note", object=text, kind=Kind.FACT, is_instruction=False, valid_from=None, text=text,
               key=f"{tenant}|{subject}|note", functional=functional, critical=False)
    r = MemoryRecord(record_id="mem_" + str(ULID()), tenant_id=tenant, owner=owner, session_id="s1", claim=cl, content=text,
                     sources=[src.source_id], parents=parents or [], base_tier=tier, tier=tier, sensitivity=sens,
                     status=Status.COMMITTED, valid_from=now, recorded_at=now)
    r.signature = sign_record(gw.signer, r)
    gw.store.put_record(r)
    gw.prov.add(r.record_id, src.source_id)
    for p in parents or []:
        gw.prov.add(r.record_id, p)
    await gw.store.index_record(r)
    return r


def _gateway(defence: str):
    from custos.gateway import CustosGateway
    return CustosGateway(":memory:", flags=GatewayFlags.for_defence(defence), embedder=HashEmbedder())


def _d0():
    from agent.memory_iface import D0Memory
    return D0Memory(embedder=HashEmbedder())


def _fact(t: int, i: int) -> str:
    return f"Vendor {i} of tenant {t} bank account is {t}{i:04d}999"


# ------------------------------------------------------------------------------------------ M10 / M11 probes
async def leakage(defence: str, *, n_probes: int = 10_000, n_tenants: int = 50, per_tenant: int = 200, n_extraction: int = 300,
                  seed: int = 0) -> list[dict]:
    rng = random.Random(f"leak|{defence}|{seed}")
    custos = defence.startswith("D3")
    gw = mem = None
    if custos:
        gw = _gateway(defence)
        for t in range(n_tenants):
            for i in range(per_tenant):
                owner = f"u{t}_{i % 3}" if i % 4 else "tenant_shared"
                s = await _src(gw, f"t{t}", f"u{t}_0", SourceType.SOR, f"row {t} {i}")
                await _rec(gw, s, _fact(t, i), tenant=f"t{t}", owner=owner, subject=f"vendor:v{i}")
    else:
        mem = _d0()
        for t in range(n_tenants):
            for i in range(per_tenant):
                await mem.remember(SessionCtx(tenant_id=f"t{t}", principal=f"u{t}_0", session_id="seed"), _fact(t, i), [])

    async def probe(t: int, q: str) -> int:
        if custos:
            out = await gw.read(ReadRequest(tenant_id=f"t{t}", principal=f"u{t}_0", session_id=f"p{rng.randrange(9)}",
                                            purpose="accounts_payable", query=q, k=8))
        else:
            out = await mem.recall(SessionCtx(tenant_id=f"t{t}", principal=f"u{t}_0", session_id="probe"), q, "accounts_payable")
        return int(any(r.tenant_id != f"t{t}" for r in out.records))        # M10: retrievals returning >= 1 foreign record

    leaks = 0
    for k in range(n_probes):
        t = rng.randrange(n_tenants)
        if k % 2:                                         # adversarial: name another tenant's entity verbatim
            other = (t + 1 + rng.randrange(n_tenants - 1)) % n_tenants
            q = _fact(other, rng.randrange(per_tenant))
        else:
            q = f"What is the bank account of vendor {rng.randrange(per_tenant)}?"
        leaks += await probe(t, q)
    ext = 0
    for k in range(n_extraction):
        t = rng.randrange(n_tenants)
        ext += await probe(t, MEXTRA_PROMPTS[k % len(MEXTRA_PROMPTS)])
    return [{"probe_type": "cross_tenant_retrieval", "defence": defence, "backend": "native" if custos else "langgraph",
             "n_probes": n_probes, "n_leaks": leaks, "notes": NOTE},
            {"probe_type": "extraction_prompt", "defence": defence, "backend": "native" if custos else "langgraph",
             "n_probes": n_extraction, "n_leaks": ext, "notes": NOTE}]


# ------------------------------------------------------------------------------------------ M12 erasure
async def erasure(defence: str, *, n_principals: int = 50, seed: int = 0) -> list[dict]:
    rows = []
    custos = defence.startswith("D3")
    if custos:
        gw = _gateway(defence)

        async def stub_write(req: WriteRequest):               # re-derivation writer: commits the clean summary directly
            s = await _src(gw, req.tenant_id, req.owner, SourceType.SUMMARY, req.content, parents=list(req.cites))
            r = await _rec(gw, s, req.content, tenant=req.tenant_id, owner=req.owner, subject="summary:window", tier=Tier.T2,
                           functional=False)
            return [SimpleNamespace(record_id=r.record_id, action="commit")]
        gw.recovery.write_fn = stub_write
        bystander_src = await _src(gw, "t1", "bystander", SourceType.USER, "Bystander prefers Friday summaries")
        bystander = await _rec(gw, bystander_src, "Bystander prefers Friday summaries", tenant="t1", owner="bystander",
                               subject="user:bystander", tier=Tier.T2, sens=Sensitivity.INTERNAL, functional=False)
    else:
        mem = _d0()
    for p in range(n_principals):
        owner = f"p{p}"
        canaries = [f"CANARY-ERASE-{seed}-{p}-{j}" for j in range(3)]
        if custos:
            recs = []
            for j, c in enumerate(canaries):
                s = await _src(gw, "t1", owner, SourceType.USER, f"My reference is {c}")
                recs.append(await _rec(gw, s, f"{owner}'s reference is {c}", tenant="t1", owner=owner, subject=f"user:{owner}:{j}",
                                       tier=Tier.T2, sens=Sensitivity.PII, functional=False))
            ss = await _src(gw, "t1", "tenant_shared", SourceType.SUMMARY, "team summary",
                            parents=[recs[0].record_id, recs[1].record_id, bystander.record_id])
            await _rec(gw, ss, f"Team notes: {owner}'s references are {canaries[0]} and {canaries[1]}; bystander prefers Friday summaries",
                       tenant="t1", owner="tenant_shared", subject=f"summary:{owner}", tier=Tier.T2, sens=Sensitivity.INTERNAL,
                       functional=False, parents=[recs[0].record_id, recs[1].record_id, bystander.record_id])
            before = gw.recovery.canary_scan("t1", canaries)
            rep = await gw.erase(tenant_id="t1", actor="dpo", owner=owner)
            after = gw.recovery.canary_scan("t1", canaries)
            n_before = sum(len(v) for v in before.values())
            residual = sum(len(v) for v in after.values())
            rows.append({"request_id": f"erase-{defence}-{p}", "defence": defence, "backend": "native", "source_records": 3,
                         "derived_total": 1, "derived_removed": 1 - int(any(after[c] for c in canaries[:2])),
                         "residual_hits": residual, "present_before": n_before, "closure": len(rep.closure), "notes": NOTE})
        else:
            s = SessionCtx(tenant_id="t1", principal=owner, session_id="e")
            for c in canaries:
                await mem.remember(s, f"{owner}'s reference is {c}", [])
            await mem.remember(SessionCtx(tenant_id="t1", principal="tenant_shared", session_id="e"),
                               f"Team notes: {owner}'s references are {canaries[0]} and {canaries[1]}", [])
            store = await mem._ensure()
            items = await store.asearch(mem.NS, limit=100000)
            before = sum(1 for it in items for c in canaries if c in it.value["text"])
            for it in items:                                    # owner-scoped delete: all an undefended store can do
                if it.value.get("owner") == owner:
                    await store.adelete(mem.NS, it.key)
            items = await store.asearch(mem.NS, limit=100000)
            residual = sum(1 for it in items for c in canaries if c in it.value["text"])
            rows.append({"request_id": f"erase-{defence}-{p}", "defence": defence, "backend": "langgraph", "source_records": 3,
                         "derived_total": 1, "derived_removed": 1 - int(residual > 0), "residual_hits": residual,
                         "present_before": before, "closure": 3, "notes": "owner-scoped delete; no provenance to reach summaries"})
    return rows


def write_access_tables(out_dir: Path, iso: list[dict], era: list[dict]) -> None:
    """Append to isolation.csv / erasure.csv (replacing rows of the same defence)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows, key in (("isolation.csv", iso, ["probe_type", "defence"]), ("erasure.csv", era, ["defence"])):
        if not rows:
            continue
        new = pd.DataFrame(rows).assign(run_id=out_dir.name)
        p = out_dir / name
        if p.exists():
            old = pd.read_csv(p)
            drop = old[key].astype(str).agg("|".join, axis=1).isin(new[key].astype(str).agg("|".join, axis=1))
            new = pd.concat([old[~drop], new], ignore_index=True)
        new.to_csv(p, index=False)

"""Seeding helpers for the W3 tests: insert sources/records directly (no write pipeline needed)."""
from __future__ import annotations

from types import SimpleNamespace

from ulid import ULID

from custos.crypto import now_utc
from custos.gateway import CustosGateway
from custos.recover import sign_record
from custos.session import GatewayFlags
from custos.tiers import base_tier, lwm
from custos.types import (
    Claim,
    ContextItem,
    Kind,
    MemoryRecord,
    Sensitivity,
    SourceRecord,
    SourceType,
    Status,
    Tier,
    WriteRequest,
)
from tests.fakes import FakeEmbed


def make_gw(flags: GatewayFlags | None = None, aliases: dict | None = None) -> CustosGateway:
    return CustosGateway(":memory:", flags=flags, embedder=FakeEmbed(aliases or {}))


async def add_source(gw: CustosGateway, tenant: str, owner: str, stype: SourceType, origin: str, raw: str,
                     session: str = "s1", parents: list[str] | None = None) -> SourceRecord:
    sha, uri = gw.blobs.put(raw)
    tier = lwm(base_tier(stype), [])
    e = gw.ledger.append("source", tenant_id=tenant, subject_id=None, actor="gateway", payload={"raw_hash": sha})
    src = SourceRecord(source_id="src_" + str(ULID()), tenant_id=tenant, session_id=session, source_type=stype,
                       origin=origin, principal=owner, external_ref=None, base_tier=tier, tier=tier,
                       parents=parents or [], raw_hash=sha, blob_uri=uri, owner=owner, received_at=now_utc(),
                       ledger_seq=e.seq, signature="")
    gw.store.put_source(src)
    for p in src.parents:
        gw.prov.add(src.source_id, p)
    return src


async def add_record(gw: CustosGateway, src: SourceRecord | None, text: str, *, subject: str = "vendor:acme",
                     relation: str = "bank_account", obj: str | None = None, tenant: str | None = None,
                     owner: str | None = None, tier: Tier | None = None, sens: Sensitivity = Sensitivity.INTERNAL,
                     status: Status = Status.COMMITTED, parents: list[str] | None = None,
                     supersedes: str | None = None, kind: Kind = Kind.FACT, functional: bool = True,
                     purposes: list[str] | None = None, session: str = "s1", flags: list[str] | None = None,
                     is_instruction: bool = False, extra_sources: list[str] | None = None) -> MemoryRecord:
    tenant = tenant or (src.tenant_id if src else "t1")
    owner = owner or (src.owner if src else "u1")
    t = tier if tier is not None else (src.tier if src else Tier.T0)
    if parents:
        t = min([t, *[gw.store.get_record(p).tier for p in parents if p.startswith("mem_")]])
    now = now_utc()
    claim = Claim(subject=subject, relation=relation, object=obj if obj is not None else text, kind=kind,
                  is_instruction=is_instruction, valid_from=None, text=text,
                  key=f"{tenant}|{subject}|{relation}", functional=functional, critical=True)
    rec = MemoryRecord(record_id="mem_" + str(ULID()), tenant_id=tenant, owner=owner, session_id=session, claim=claim,
                       content=text, sources=([src.source_id] if src else []) + (extra_sources or []),
                       parents=parents or [], base_tier=t, tier=t, sensitivity=sens, purposes=purposes or ["*"],
                       status=status, supersedes=supersedes, valid_from=now, recorded_at=now, flags=flags or [])
    rec.signature = sign_record(gw.signer, rec)
    gw.store.put_record(rec)
    if src:
        gw.prov.add(rec.record_id, src.source_id)
    for p in parents or []:
        gw.prov.add(rec.record_id, p)
    if supersedes:
        old = gw.store.get_record(supersedes)
        old.status, old.superseded_by, old.valid_to, old.closed_at = Status.SUPERSEDED, rec.record_id, now, now
        gw.store.put_record(old)
        gw.prov.add(rec.record_id, supersedes, "supersedes")
    if status == Status.COMMITTED:
        await gw.store.index_record(rec)
    return rec


def ctx(cid: str, kind: str, text: str, tier: Tier, **kw) -> ContextItem:
    return ContextItem(ctx_id=cid, session_id="s1", kind=kind, tier=tier, text=text, **kw)


def stub_writer(gw: CustosGateway):
    """A stand-in for WritePipeline.write used by re-derivation tests: commits the content as a summary record."""
    async def write(req: WriteRequest):
        src = await add_source(gw, req.tenant_id, req.owner, SourceType.SUMMARY, "agent:summariser", req.content,
                               session=req.session_id or "s1", parents=list(req.cites))
        rec = await add_record(gw, src, req.content, subject="summary:window", relation="other:summary",
                               obj=req.content, tenant=req.tenant_id, owner=req.owner, parents=None,
                               tier=lwm(None, [gw.store.get_record(c).tier if c.startswith("mem_")
                                               else gw.store.get_source(c).tier for c in req.cites]),
                               functional=False, session=req.session_id or "s1")
        return [SimpleNamespace(record_id=rec.record_id, action="commit")]
    gw.recovery.write_fn = write

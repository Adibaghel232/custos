"""Recover (SPEC §8): taint closure, bi-temporal revert, re-derivation, exposure report, DPDP erasure."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

import orjson
from ulid import ULID

from .blobs import BlobStore
from .crypto import Signer, jcs, now_utc, sha256_hex
from .ledger import Ledger
from .provenance import TAINT_EDGES, Provenance
from .session import GatewayFlags
from .store import NativeStore
from .types import MemoryRecord, ReadLogEntry, RollbackReport, SourceType, Status, WriteRequest

TOMBSTONE = "[erased]"
CLOSURE_EDGES = (*TAINT_EDGES, "corroborates")
Rederiver = Callable[[MemoryRecord, list[str]], Awaitable["str | None"]]
WriteFn = Callable[[WriteRequest], Awaitable[list[Any]]]


def _s(x: Any) -> str:
    return bytes(x).decode("utf-8", "replace") if isinstance(x, (bytes, bytearray, memoryview)) else str(x)


def record_digest(r: MemoryRecord) -> str:
    return sha256_hex(jcs({"record_id": r.record_id, "tenant_id": r.tenant_id, "content_sha256": sha256_hex(r.content),
                           "sources": r.sources, "parents": r.parents, "tier": int(r.tier), "version": r.version,
                           "status": r.status.value}))


def sign_record(signer: Signer, r: MemoryRecord) -> str:
    return signer.sign_hex(record_digest(r))


def record_sig_ok(public_key_hex: str, r: MemoryRecord) -> bool:
    return bool(r.signature) and Signer.verify(public_key_hex, record_digest(r), r.signature or "")


async def default_rederiver(old: MemoryRecord, texts: list[str]) -> str | None:
    """Re-run the summariser on clean parents (LLM if one is installed, else extractive)."""
    from . import llm as llm_mod
    if getattr(llm_mod, "_llm", None) is not None:
        try:
            resp = await llm_mod.get_llm().chat("summariser", [
                {"role": "system", "content": "Summarise the notes into short standalone factual sentences. "
                                              "Add nothing that is not in the notes."},
                {"role": "user", "content": "\n".join(f"- {t}" for t in texts)}], max_tokens=300)
            if resp.text.strip():
                return resp.text.strip()
        except Exception:
            pass
    return " ".join(t.strip() for t in texts)[:1000] or None


class Recover:
    def __init__(self, store: NativeStore, ledger: Ledger, prov: Provenance, blobs: BlobStore,
                 flags: GatewayFlags | None = None, emit: Callable[..., Any] | None = None,
                 write_fn: WriteFn | None = None, rederiver: Rederiver | None = None):
        self.store, self.ledger, self.prov, self.blobs = store, ledger, prov, blobs
        self.flags = flags or GatewayFlags()
        self.emit = emit or (lambda *a, **k: None)
        self.write_fn = write_fn
        self.rederiver = rederiver or default_rederiver

    # ------------------------------------------------------------------ closure
    def _tenants_of(self, roots: list[str]) -> set[str | None]:
        out: set[str | None] = set()
        for x in roots:
            r = self.store.get_record(x) if x.startswith("mem_") else None
            s = self.store.get_source(x) if x.startswith("src_") else None
            out.add((r or s).tenant_id if (r or s) else None)   # type: ignore[union-attr]
        return out

    def _child_map(self, tenants: set[str | None]) -> dict[str, list[str]]:
        """Fallback links straight from the documents, so closure is right even if an edge was never written."""
        cm: dict[str, list[str]] = defaultdict(list)
        for t in (tenants or {None}):
            for r in self.store.records(t):
                for p in (*r.sources, *r.parents, *r.corroborated_by):
                    cm[p].append(r.record_id)
            for s in self.store.sources(t):
                for p in s.parents:
                    cm[p].append(s.source_id)
        return cm

    def closure(self, roots: list[str]) -> list[list[str]]:
        cm = self._child_map(self._tenants_of(roots))
        frontier = list(dict.fromkeys(roots))
        seen = set(frontier)
        hops = [frontier]
        while frontier:
            nxt: list[str] = []
            for n in frontier:
                for c in [*self.prov.children(n, CLOSURE_EDGES), *cm.get(n, [])]:
                    if c not in seen:
                        seen.add(c)
                        nxt.append(c)
            if nxt:
                hops.append(nxt)
            frontier = nxt
        return hops

    # ------------------------------------------------------------------ exposure
    def exposure(self, closure_ids: set[str], tenant_id: str | None = None) -> dict[str, Any]:
        sql, args = "SELECT doc FROM read_log", []
        if tenant_id:
            sql, args = sql + " WHERE tenant_id=?", [tenant_id]
        reads = [ReadLogEntry.model_validate_json(r[0]) for r in self.store.db.query(sql + " ORDER BY ts", args)]
        exposed = [r for r in reads if closure_ids & set(r.returned)]
        gsql, gargs = "SELECT doc FROM gate_log", []
        if tenant_id:
            gsql, gargs = gsql + " WHERE tenant_id=?", [tenant_id]
        gates = []
        for row in self.store.db.query(gsql + " ORDER BY ts", gargs):
            d = orjson.loads(row[0])
            deps = set(d.get("decisive_records", [])) | set(d.get("decisive_sources", []))
            for t in d.get("arg_taint", []):
                deps |= set(t.get("support_records", [])) | set(t.get("support_sources", []))
            if deps & closure_ids:
                resp = d.get("user_response")
                status = ("executed" if d["decision"] == "allow" or resp == "approved"
                          else "blocked" if d["decision"] == "block" else "asked")
                gates.append({"gate_id": d["gate_id"], "tool": d["tool"], "status": status,
                              "session_id": d.get("session_id")})
        return {"reads": exposed, "gates": gates,
                "sessions": list(dict.fromkeys(r.session_id for r in exposed)),
                "principals": list(dict.fromkeys(r.principal for r in exposed))}

    # ------------------------------------------------------------------ re-derivation
    def _clean_texts(self, rec: MemoryRecord, closure: set[str]) -> tuple[list[str], list[str]]:
        """(texts, cite ids) of the record's parents that are outside the closure and still alive."""
        seeds = list(rec.parents)
        for sid in rec.sources:
            s = self.store.get_source(sid)
            if s and s.source_type == SourceType.SUMMARY:
                seeds += s.parents
        texts, cites = [], []
        for p in dict.fromkeys(seeds):
            if p in closure:
                continue
            if p.startswith("mem_"):
                r = self.store.get_record(p)
                if r and r.status == Status.COMMITTED:
                    texts.append(r.content)
                    cites.append(p)
            elif p.startswith("src_"):
                s = self.store.get_source(p)
                row = self.store.db.one("SELECT erased FROM sources WHERE source_id=?", (p,))
                t = self.blobs.text(s.raw_hash) if s and not (row and row[0]) else None
                if t:
                    texts.append(t)
                    cites.append(p)
        return texts, cites

    async def _rederive(self, members: list[MemoryRecord], closure: set[str], op_id: str, actor: str,
                        tenant_id: str | None) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.write_fn is None:
            return out
        for rec in members:
            texts, cites = self._clean_texts(rec, closure)
            if not texts:
                continue
            new_text = await self.rederiver(rec, texts)
            if not new_text:
                continue
            verdicts = await self.write_fn(WriteRequest(
                tenant_id=rec.tenant_id, owner=rec.owner, session_id=rec.session_id, content=new_text,
                via="summary", cites=cites, purposes=rec.purposes, sensitivity_hint=rec.sensitivity, wait=True))
            for v in verdicts:
                nid = getattr(v, "record_id", None)
                nr = self.store.get_record(nid) if nid else None
                if nr is None or nr.status != Status.COMMITTED:
                    continue
                nr.flags = [*nr.flags, "rederived", f"rederived_from:{rec.record_id}"]
                nr.signature = sign_record(self.ledger.signer, nr)
                self.store.put_record(nr)
                self.prov.add_many(nr.record_id, [c for c in cites], "derived_from")
                self.ledger.append("rederive", tenant_id=nr.tenant_id, subject_id=nr.record_id, actor=actor, payload={
                    "op_id": op_id, "old": rec.record_id, "new": nr.record_id, "clean_parents": cites})
                out.setdefault(rec.record_id, nr.record_id)
        return out

    def _is_derived(self, rec: MemoryRecord) -> bool:
        if rec.parents:
            return True
        for sid in rec.sources:
            s = self.store.get_source(sid)
            if s and s.source_type == SourceType.SUMMARY:
                return True
        return False

    def _save_detail(self, op_id: str, detail: dict[str, Any]) -> None:
        self.store.db.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?,?)", (f"recover:{op_id}", orjson.dumps(detail)))

    def detail(self, op_id: str) -> dict[str, Any] | None:
        row = self.store.db.one("SELECT v FROM kv WHERE k=?", (f"recover:{op_id}",))
        return orjson.loads(row[0]) if row else None

    # ------------------------------------------------------------------ revert
    async def revert(self, roots: list[str], actor: str) -> RollbackReport:
        op_id = "op_" + str(ULID())
        tenant = next((t for t in self._tenants_of(roots) if t), None)
        self.emit("recover.started", {"op_id": op_id, "kind": "revert", "roots": roots}, tenant_id=tenant,
                  subject_id=op_id)
        hops = self.closure(roots)
        closure = {n for h in hops for n in h}
        start = self.ledger.append("revert", tenant_id=tenant, subject_id=op_id, actor=actor, payload={
            "op_id": op_id, "kind": "revert", "roots": roots, "closure_size": len(closure)})
        edges = [{"from": a, "to": b, "type": t} for a, b, t in self.prov.edges(closure)]
        self.emit("recover.closure", {"op_id": op_id, "nodes": sorted(closure), "edges": edges}, tenant_id=tenant,
                  subject_id=op_id)
        reverted: list[str] = []
        now = now_utc()
        with self.store.db.tx():
            for hop_no, hop in enumerate(hops):
                for n in hop:
                    rec = self.store.get_record(n) if n.startswith("mem_") else None
                    if rec is None or rec.status in (Status.REVERTED, Status.ERASED):
                        continue
                    prev = rec.status
                    rec.status, rec.closed_at = Status.REVERTED, now
                    rec.signature = sign_record(self.ledger.signer, rec)
                    self.store.put_record(rec)          # also removes it from the vector index
                    reverted.append(n)
                    self.ledger.append("revert", tenant_id=rec.tenant_id, subject_id=n, actor=actor, payload={
                        "op_id": op_id, "prev_status": prev.value, "hop": hop_no, "root": roots})
        self._close_quarantine(reverted, actor, op_id)
        restored = await self._restore_predecessors(reverted, closure, op_id, actor)
        members = [r for r in (self.store.get_record(n) for n in reverted) if r and self._is_derived(r)]
        rederived = await self._rederive(members, closure, op_id, actor, tenant)
        expo = self.exposure(closure, tenant)
        report = RollbackReport(
            op_id=op_id, kind="revert", roots=roots, closure=[n for h in hops for n in h], reverted=reverted,
            restored=restored, rederived=list(rederived.values()), erased_sources=[],
            exposed_sessions=expo["sessions"], exposed_principals=expo["principals"],
            affected_gate_ids=[g["gate_id"] for g in expo["gates"]], ledger_from=start.seq,
            ledger_to=self.ledger.head()[0])
        self._finish(report, hops, rederived, expo, tenant)
        return report

    def _close_quarantine(self, record_ids: list[str], actor: str, op_id: str) -> None:
        """Open quarantine items for reverted records are resolved: the record is gone, nothing left to review."""
        if not record_ids:
            return
        ids = set(record_ids)
        for row in self.store.db.query("SELECT qid, doc FROM quarantine WHERE status='open'"):
            d = orjson.loads(row["doc"])
            if d.get("record_id") in ids:
                d.update({"status": "rejected", "reviewer": actor, "note": f"reverted in {op_id}",
                          "resolved_at": now_utc().isoformat()})
                self.store.db.execute("UPDATE quarantine SET doc=?, status='rejected' WHERE qid=?",
                                      (orjson.dumps(d), row["qid"]))

    async def _restore_predecessors(self, reverted: list[str], closure: set[str], op_id: str, actor: str) -> list[str]:
        restored: list[str] = []
        for n in reverted:
            m = self.store.get_record(n)
            if not m or not m.supersedes or m.supersedes in closure:
                continue
            s = self.store.get_record(m.supersedes)
            if s is None or s.status != Status.SUPERSEDED:
                continue
            if s.claim.functional and any(o.record_id != s.record_id for o in
                                          self.store.records(s.tenant_id, status=Status.COMMITTED, key=s.claim.key)):
                continue   # a clean successor already holds the key
            s.status, s.valid_to, s.closed_at, s.superseded_by = Status.COMMITTED, None, None, None
            s.signature = sign_record(self.ledger.signer, s)
            self.store.put_record(s)
            await self.store.index_record(s)
            restored.append(s.record_id)
            self.ledger.append("restore", tenant_id=s.tenant_id, subject_id=s.record_id, actor=actor, payload={
                "op_id": op_id, "reopened_by_revert_of": n})
        return restored

    def _finish(self, report: RollbackReport, hops: list[list[str]], rederived: dict[str, str],
                expo: dict[str, Any], tenant: str | None) -> None:
        from .context import session_no
        tool_calls = [{"call_id": g["gate_id"], "tool": g["tool"], "status": g["status"]} for g in expo["gates"]]
        detail = {"op_id": report.op_id, "mode": report.kind, "roots": report.roots, "closure_by_hop": hops,
                  "rederived": [{"old": o, "new": n} for o, n in rederived.items()], "restored": report.restored,
                  "exposure": {"sessions": sorted({session_no(s) for s in report.exposed_sessions}),
                               "users": report.exposed_principals, "reads": len(expo["reads"]),
                               "tool_calls": tool_calls},
                  "ledger_seq": report.ledger_from, "ledger_to": report.ledger_to}
        self._save_detail(report.op_id, detail)
        self.emit("recover.completed", {**report.model_dump(mode="json"), "ui": {
            "root": report.roots[0] if report.roots else "", "mode": report.kind, "closure_by_hop": hops,
            "rederived": detail["rederived"], "exposure": detail["exposure"], "ledger_seq": report.ledger_from}},
            tenant_id=tenant, subject_id=report.op_id)

    # ------------------------------------------------------------------ DPDP erasure
    def _scrub_vector(self, rec: MemoryRecord) -> None:
        self.store.unindex(rec)
        p = getattr(self.store.index, "parts", {}).get(rec.tenant_id)
        if p is not None and rec.record_id in p.pos:
            i = p.pos[rec.record_id]
            p.mat[i] = 0.0
            p.meta[i] = {"status": "erased"}

    def _scrub_logs(self, closure: set[str]) -> None:
        for row in self.store.db.query("SELECT qid, doc FROM quarantine"):
            d = orjson.loads(row["doc"])
            if d.get("record_id") in closure:
                d.update({"reasons": [TOMBSTONE], "note": None, "status": "expired" if d.get("status") == "open"
                          else d.get("status")})
                self.store.db.execute("UPDATE quarantine SET doc=?, status=? WHERE qid=?",
                                      (orjson.dumps(d), d["status"], row["qid"]))
        for row in self.store.db.query("SELECT gate_id, doc FROM gate_log"):
            d = orjson.loads(row["doc"])
            deps = set(d.get("decisive_records", [])) | set(d.get("decisive_sources", []))
            for t in d.get("arg_taint", []):
                deps |= set(t.get("support_records", [])) | set(t.get("support_sources", []))
            deps |= set((d.get("_ctx") or {}).get("records", [])) | set((d.get("_ctx") or {}).get("sources", []))
            if not deps & closure:
                continue
            d["args"] = {k: TOMBSTONE for k in d.get("args", {})}
            for t in d.get("arg_taint", []):
                t["value"] = TOMBSTONE
            d["explanation"] = "[erased: decision text removed under DPDP erasure]"
            ui = d.get("_ui") or {}
            for a in ui.get("args", []):
                a["value"] = TOMBSTONE
            ui["explanation"] = d["explanation"]
            d["_ui"] = ui
            self.store.db.execute("UPDATE gate_log SET doc=? WHERE gate_id=?", (orjson.dumps(d), row["gate_id"]))

    async def erase(self, *, tenant_id: str, actor: str, owner: str | None = None,
                    source_ids: list[str] | None = None) -> RollbackReport:
        op_id = "op_" + str(ULID())
        roots: list[str] = []
        if owner:
            roots += [s.source_id for s in self.store.sources(tenant_id, owner=owner)]
            about = {owner, f"user:{owner}", f"person:{owner}"}
            roots += [r.record_id for r in self.store.records(tenant_id)
                      if r.owner == owner or r.claim.subject in about]
        roots += list(source_ids or [])
        roots = list(dict.fromkeys(roots))
        self.emit("recover.started", {"op_id": op_id, "kind": "erase", "roots": roots}, tenant_id=tenant_id,
                  subject_id=op_id)
        hops = self.closure(roots)
        closure = {n for h in hops for n in h}
        # sources that only fed erased records are erased with them (their raw content is personal data too)
        cm = self._child_map({tenant_id})
        for n in list(closure):
            r = self.store.get_record(n) if n.startswith("mem_") else None
            for sid in (r.sources if r else []):
                if sid not in closure and all(c in closure for c in cm.get(sid, [])):
                    closure.add(sid)
                    hops[0].append(sid) if hops else hops.append([sid])
        start = self.ledger.append("erase", tenant_id=tenant_id, subject_id=op_id, actor=actor, payload={
            "op_id": op_id, "kind": "erase", "owner_digest": sha256_hex(owner) if owner else None,
            "roots": roots, "closure_size": len(closure)})
        self.emit("recover.closure", {"op_id": op_id, "nodes": sorted(closure), "edges": []}, tenant_id=tenant_id,
                  subject_id=op_id)
        expo = self.exposure(closure, tenant_id)       # before the logs are scrubbed
        derived_members = [r for r in (self.store.get_record(n) for n in closure if n.startswith("mem_"))
                           if r and self._is_derived(r) and r.owner != owner]
        derived_texts = {r.record_id: self._clean_texts(r, closure) for r in derived_members}
        erased: list[str] = []
        erased_sources: list[str] = []
        with self.store.db.tx():
            for n in closure:
                rec = self.store.get_record(n) if n.startswith("mem_") else None
                if rec is not None:
                    digest = sha256_hex(rec.content)
                    self._scrub_vector(rec)
                    rec.content = TOMBSTONE
                    rec.claim = rec.claim.model_copy(update={
                        "text": TOMBSTONE, "object": TOMBSTONE, "qualifiers": {}, "subject": TOMBSTONE,
                        "key": f"erased|{digest[:16]}"})
                    rec.status, rec.closed_at = Status.ERASED, now_utc()
                    rec.signature = sign_record(self.ledger.signer, rec)
                    self.store.put_record(rec)
                    erased.append(n)
                    self.ledger.append("erase", tenant_id=rec.tenant_id, subject_id=n, actor=actor, payload={
                        "op_id": op_id, "content_sha256": digest, "kind": "record"})
                    continue
                src = self.store.get_source(n) if n.startswith("src_") else None
                if src is not None:
                    self.store.mark_source_erased(n)
                    erased_sources.append(n)
                    self.ledger.append("erase", tenant_id=src.tenant_id, subject_id=n, actor=actor, payload={
                        "op_id": op_id, "raw_hash": src.raw_hash, "kind": "source"})
        # blobs: delete content unless a surviving source shares the same content hash
        for n in erased_sources:
            src = self.store.get_source(n)
            if src is None:
                continue
            shared = self.store.db.one("SELECT 1 FROM sources WHERE raw_hash=? AND erased=0 AND source_id<>?",
                                       (src.raw_hash, n))
            if shared is None:
                self.blobs.delete(src.raw_hash)
        self._scrub_logs(closure)
        # shared summaries: re-derive from the parents that survive
        rederived: dict[str, str] = {}
        if self.write_fn is not None:
            for rec in derived_members:
                texts, cites = derived_texts[rec.record_id]
                if not texts:
                    continue
                new_text = await self.rederiver(rec, texts)
                if not new_text:
                    continue
                for v in await self.write_fn(WriteRequest(
                        tenant_id=rec.tenant_id, owner=rec.owner, session_id=rec.session_id, content=new_text,
                        via="summary", cites=cites, purposes=rec.purposes, sensitivity_hint=rec.sensitivity, wait=True)):
                    nr = self.store.get_record(getattr(v, "record_id", "") or "")
                    if nr is not None and nr.status == Status.COMMITTED:
                        nr.flags = [*nr.flags, "rederived", f"rederived_from:{rec.record_id}"]
                        nr.signature = sign_record(self.ledger.signer, nr)
                        self.store.put_record(nr)
                        self.prov.add_many(nr.record_id, cites, "derived_from")
                        self.ledger.append("rederive", tenant_id=nr.tenant_id, subject_id=nr.record_id, actor=actor,
                                           payload={"op_id": op_id, "old": rec.record_id, "new": nr.record_id})
                        rederived.setdefault(rec.record_id, nr.record_id)
        report = RollbackReport(
            op_id=op_id, kind="erase", roots=roots, closure=[n for h in hops for n in h], reverted=erased,
            restored=[], rederived=list(rederived.values()), erased_sources=erased_sources,
            exposed_sessions=expo["sessions"], exposed_principals=expo["principals"],
            affected_gate_ids=[g["gate_id"] for g in expo["gates"]], ledger_from=start.seq,
            ledger_to=self.ledger.head()[0])
        self._finish(report, hops, rederived, expo, tenant_id)
        return report

    # ------------------------------------------------------------------ independent completeness scan (M12)
    def canary_scan(self, tenant_id: str, canaries: list[str]) -> dict[str, list[str]]:
        """Where do canary strings still appear? {canary: [where, ...]} (empty lists = erased everywhere)."""
        found: dict[str, list[str]] = {c: [] for c in canaries}
        tables = {"records": "SELECT record_id, doc FROM records WHERE tenant_id=?",
                  "quarantine": "SELECT qid, doc FROM quarantine WHERE tenant_id=?",
                  "gate_log": "SELECT gate_id, doc FROM gate_log WHERE tenant_id=?"}
        for tname, sql in tables.items():
            for row in self.store.db.query(sql, (tenant_id,)):
                doc = _s(row[1])
                for c in canaries:
                    if c in doc:
                        found[c].append(f"{tname}:{row[0]}")
        for s in self.store.sources(tenant_id):
            t = self.blobs.text(s.raw_hash)
            for c in canaries:
                if t and c in t:
                    found[c].append(f"blob:{s.source_id}")
        for row in self.store.db.query("SELECT seq, payload FROM ledger"):
            doc = _s(row[1])
            for c in canaries:
                if c in doc:
                    found[c].append(f"ledger:{row[0]}")
        return found

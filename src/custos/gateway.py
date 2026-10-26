"""CustosGateway: the facade the agent and the bench talk to (CONTRACTS C2). It is the sole path to memory."""
from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import orjson

from .blobs import BlobStore
from .context import ReplanFn, ReplanRegistry, session_no
from .crypto import Signer
from .db import DB
from .embed import Embedder, get_embedder
from .gate import Gate
from .ledger import Ledger
from .provenance import Provenance
from .read import PrincipalDirectory, ReadPath
from .recover import Recover, Rederiver, record_sig_ok
from .session import GatewayFlags
from .settings import Settings, get_settings
from .store import NativeStore, NumpyIndex
from .telemetry import EventBus
from .types import (
    GateDecision,
    GateRequest,
    MemoryRecord,
    ReadLogEntry,
    ReadRequest,
    RetrievedContext,
    RollbackReport,
    SourceRecord,
    SourceType,
    Status,
    Verdict,
    WriteRequest,
)

_DEFAULT_SIGNER: Signer | None = None


def default_signer() -> Signer:
    """One signer per process, so warm-memory snapshots restore into any gateway of the same process."""
    global _DEFAULT_SIGNER
    if _DEFAULT_SIGNER is None:
        _DEFAULT_SIGNER = Signer.load_or_create() if os.getenv("CUSTOS_SIGNING_KEY_PATH") else Signer.generate()
    return _DEFAULT_SIGNER


class CustosGateway:
    def __init__(self, db_path: str = ":memory:", *, flags: GatewayFlags | None = None, embedder: Embedder | None = None,
                 signer: Signer | None = None, blobs_root: str | None = None, settings: Settings | None = None):
        self.flags = flags or GatewayFlags()
        self.settings = settings or get_settings()
        self.db = DB(db_path)
        mem = str(db_path) == ":memory:"
        self.signer = signer or (default_signer() if mem else Signer.load_or_create())
        self.ledger = Ledger(self.db, self.signer)
        self.store = NativeStore(self.db, embedder or get_embedder(), NumpyIndex())
        self.prov = Provenance(self.db)
        self.blobs = BlobStore(blobs_root or (tempfile.mkdtemp(prefix="custos-blobs-") if mem
                                              else Path(db_path).parent / "blobs"))
        self.bus = EventBus(lane="D3")
        self.directory = PrincipalDirectory()
        self.replans = ReplanRegistry()
        self.session_principal: dict[str, str] = {}
        self._gate_events: dict[str, dict] = {}
        self.writer: Any = None
        try:   # W2 owns the write pipeline (contract C1)
            from .write import WritePipeline
            self.writer = WritePipeline(self.store, self.ledger, self.prov, self.blobs, flags=self.flags,
                                        settings=self.settings)
            self.writer.events.append(self._on_writer_event)
        except ImportError:
            pass
        self.reader = ReadPath(self.store, self.ledger, self.flags, self.settings, self.directory, emit=self.bus.emit)
        self.gate_engine = Gate(self.store, self.ledger, self.prov, self.flags, self.settings, self.replans,
                                emit=self.bus.emit, principal_of=self._principal_of)
        self.recovery = Recover(self.store, self.ledger, self.prov, self.blobs, self.flags, emit=self.bus.emit,
                                write_fn=self._write_for_recover)

    # ------------------------------------------------------------------ events
    def on_event(self, cb: Callable[[dict], None]) -> None:
        self.bus.subscribe(cb)

    def _on_writer_event(self, ev: dict) -> None:
        self.bus.ingest_writer_event(ev)

    # ------------------------------------------------------------------ configuration hooks
    def register_principal(self, principal_id: str, tenant_id: str, role: str = "ap_clerk",
                           purposes: list[str] | None = None) -> None:
        self.directory.register(principal_id, tenant_id, role, purposes)

    def register_replan(self, handle: str, fn: ReplanFn) -> None:
        self.replans.register(handle, fn)

    def set_rederiver(self, fn: Rederiver) -> None:
        self.recovery.rederiver = fn

    def set_sor_lookup(self, fn: Callable[..., Any] | None) -> None:
        self.gate_engine.sor_lookup = fn

    @property
    def audit_mode(self) -> bool:
        return self.gate_engine.audit

    @audit_mode.setter
    def audit_mode(self, v: bool) -> None:
        self.gate_engine.audit = v

    def _principal_of(self, session_id: str) -> str | None:
        p = self.session_principal.get(session_id)
        if p:
            return p
        row = self.db.one("SELECT doc FROM read_log WHERE session_id=? ORDER BY ts DESC LIMIT 1", (session_id,))
        return ReadLogEntry.model_validate_json(row[0]).principal if row else None

    # ------------------------------------------------------------------ write (W2)
    def _need_writer(self) -> Any:
        if self.writer is None:
            raise RuntimeError("custos.write.WritePipeline is not available (workstream W2)")
        return self.writer

    async def register_source(self, **kw: Any) -> SourceRecord:
        return await self._need_writer().register_source(**kw)

    async def write(self, req: WriteRequest) -> list[Verdict]:
        return await self._need_writer().write(req)

    async def _write_for_recover(self, req: WriteRequest) -> list[Verdict]:
        return await self._need_writer().write(req) if self.writer is not None else []

    async def drain(self, tenant_id: str | None = None) -> None:
        if self.writer is not None:
            await self.writer.drain(tenant_id)

    # ------------------------------------------------------------------ read / gate / recover
    async def read(self, req: ReadRequest) -> RetrievedContext:
        self.session_principal[req.session_id] = req.principal
        return await self.reader.read(req)

    async def gate(self, req: GateRequest, replan: ReplanFn | None = None) -> GateDecision:
        return await self.gate_engine.evaluate(req, replan)

    async def gate_response(self, gate_id: str, approved: bool) -> None:
        self.gate_engine.respond(gate_id, approved)

    async def revert(self, roots: list[str], actor: str) -> RollbackReport:
        return await self.recovery.revert(roots, actor)

    async def erase(self, *, tenant_id: str, actor: str, owner: str | None = None,
                    source_ids: list[str] | None = None) -> RollbackReport:
        return await self.recovery.erase(tenant_id=tenant_id, actor=actor, owner=owner, source_ids=source_ids)

    def recover_detail(self, op_id: str) -> dict[str, Any] | None:
        return self.recovery.detail(op_id)

    # ------------------------------------------------------------------ ledger
    def verify_ledger(self) -> dict[str, Any]:
        rep = self.ledger.verify(pinned_public_key=self.signer.public_key_hex)
        bad = [r["source_id"] for r in self.db.query("SELECT source_id, raw_hash, erased FROM sources")
               if not r["erased"] and not self.blobs.verify(r["raw_hash"])]
        rep["blob_failures"] = len(bad)
        if bad:
            rep["ok"] = False
            rep["failures_by_type"] = {**rep["failures_by_type"], "blob": len(bad)}
        return rep

    def record_sig_ok(self, rec: MemoryRecord) -> bool:
        return record_sig_ok(self.signer.public_key_hex, rec)

    # ------------------------------------------------------------------ graph (DEMO_DECK_PLAN §3.8)
    def tenants(self) -> list[str]:
        return [r[0] for r in self.db.query(
            "SELECT tenant_id FROM sources UNION SELECT tenant_id FROM records ORDER BY 1") if r[0]]

    def graph(self, tenant_id: str) -> dict[str, Any]:
        if tenant_id in ("", "*", None):
            tids = self.tenants()
        else:
            tids = [tenant_id]
        nodes: dict[str, dict] = {}
        edges: dict[str, dict] = {}

        def edge(kind: str, a: str, b: str) -> None:
            if a in nodes and b in nodes and a != b:
                eid = f"{kind}:{a}->{b}"
                edges.setdefault(eid, {"id": eid, "source": a, "target": b, "kind": kind})

        recs: list[MemoryRecord] = []
        srcs: list[SourceRecord] = []
        for t in tids:
            srcs += self.store.sources(t)
            recs += self.store.records(t)
        summary_srcs = {s.source_id for s in srcs if s.source_type == SourceType.SUMMARY}
        for s in srcs:
            nodes[s.source_id] = {"id": s.source_id, "kind": "source", "label": f"{s.source_type.value} · {s.origin}",
                                  "tier": f"T{int(s.tier)}", "status": "n/a", "session": session_no(s.session_id)}
        for r in recs:
            summ = bool(r.parents) or bool(set(r.sources) & summary_srcs)
            label = "[erased]" if r.status == Status.ERASED else \
                f"{r.claim.subject.split(':')[-1]}.{r.claim.relation} {r.claim.object}"[:80]
            nodes[r.record_id] = {"id": r.record_id, "kind": "summary" if summ else "record", "label": label,
                                  "tier": f"T{int(r.tier)}", "status": r.status.value, "session": session_no(r.session_id)}
        for s in srcs:
            for p in s.parents:
                edge("derived_from", p, s.source_id)
        for r in recs:
            for p in [*r.sources, *r.parents]:
                edge("derived_from", p, r.record_id)
            if r.supersedes:
                edge("supersedes", r.record_id, r.supersedes)
        for child, parent, typ in self.prov.edges():
            if typ in ("derived_from", "cites"):
                edge("derived_from", parent, child)
            elif typ in ("conflicts_with", "supersedes"):
                edge(typ, child, parent)
        for row in self.db.query("SELECT doc FROM quarantine"):
            q = orjson.loads(row[0])
            for c in q.get("conflicts_with", []):
                edge("conflicts_with", q.get("record_id", ""), c)
        for t in tids:
            for row in self.db.query("SELECT doc FROM read_log WHERE tenant_id=? ORDER BY ts", (t,)):
                rl = ReadLogEntry.model_validate_json(row[0])
                n = session_no(rl.session_id)
                sid = f"sess_{n}" if n else f"sess_{rl.session_id}"
                nodes.setdefault(sid, {"id": sid, "kind": "session", "label": f"S{n}" if n else rl.session_id,
                                       "tier": None, "status": "n/a", "session": n})
                for rid in rl.returned:
                    edge("read_by", rid, sid)
        return {"nodes": list(nodes.values()), "edges": list(edges.values())}

    # ------------------------------------------------------------------ snapshots (warm memory)
    async def snapshot(self) -> bytes:
        buf = io.BytesIO()
        with tempfile.TemporaryDirectory() as td:
            dbf = Path(td) / "db.sqlite"
            self.db.backup_to(dbf)
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(dbf, "db.sqlite")
                z.writestr("index.bin", self.store.index.snapshot())
                for p in Path(self.blobs.root).rglob("*"):
                    if p.is_file():
                        z.write(p, f"blobs/{p.name}")
                z.writestr("meta.json", json.dumps({"v": 1, "key_id": self.signer.key_id}))
        return buf.getvalue()

    async def restore(self, blob: bytes) -> None:
        with zipfile.ZipFile(io.BytesIO(blob)) as z, tempfile.TemporaryDirectory() as td:
            z.extract("db.sqlite", td)
            src = sqlite3.connect(str(Path(td) / "db.sqlite"))
            with self.db.lock:
                src.backup(self.db.conn)
            src.close()
            self.store.index.restore(z.read("index.bin"))
            root = Path(self.blobs.root)
            if root.exists():
                shutil.rmtree(root, ignore_errors=True)
            root.mkdir(parents=True, exist_ok=True)
            for name in z.namelist():
                if name.startswith("blobs/") and name != "blobs/":
                    self.blobs.put(z.read(name))
        self.session_principal.clear()

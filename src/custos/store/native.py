"""Custos-native store (SPEC §10.3): SourceRecords + MemoryRecords in SQLite, committed records in a per-tenant
NumpyIndex. The write path (W2) and read/recover paths (W3) share one instance via the gateway facade."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from ..crypto import ts_str
from ..db import DB
from ..embed import Embedder, get_embedder
from ..types import MemoryRecord, SourceRecord, Status
from .vector import NumpyIndex

Mask = Callable[[dict[str, Any]], bool]


def index_meta(r: MemoryRecord) -> dict[str, Any]:
    """Metadata the read-path pre-filter sees (never the content)."""
    return {"owner": r.owner, "sensitivity": r.sensitivity.value, "purposes": list(r.purposes),
            "status": r.status.value, "tier": int(r.tier), "kind": r.claim.kind.value,
            "is_instruction": r.claim.is_instruction, "key": r.claim.key}


class NativeStore:
    def __init__(self, db: DB, embedder: Embedder | None = None, index: NumpyIndex | None = None):
        self.db = db
        self.embedder = embedder or get_embedder()
        self.index = index or NumpyIndex()

    # ------------------------------------------------------------------ sources
    def put_source(self, s: SourceRecord) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO sources (source_id, tenant_id, owner, session_id, source_type, tier,"
            " received_at, raw_hash, erased, doc) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (s.source_id, s.tenant_id, s.owner, s.session_id, s.source_type.value, int(s.tier),
             ts_str(s.received_at), s.raw_hash, 0, s.model_dump_json()))

    def get_source(self, source_id: str) -> SourceRecord | None:
        row = self.db.one("SELECT doc FROM sources WHERE source_id=?", (source_id,))
        return SourceRecord.model_validate_json(row[0]) if row else None

    def sources(self, tenant_id: str | None = None, owner: str | None = None) -> list[SourceRecord]:
        sql, args = "SELECT doc FROM sources WHERE 1=1", []
        if tenant_id is not None:
            sql, args = sql + " AND tenant_id=?", args + [tenant_id]
        if owner is not None:
            sql, args = sql + " AND owner=?", args + [owner]
        return [SourceRecord.model_validate_json(r[0]) for r in self.db.query(sql, args)]

    def mark_source_erased(self, source_id: str) -> None:
        self.db.execute("UPDATE sources SET erased=1 WHERE source_id=?", (source_id,))

    # ------------------------------------------------------------------ records
    def put_record(self, r: MemoryRecord) -> None:
        """Insert or replace the record row. Keeps the vector index status in sync (pending/quarantined
        records are never retrievable: only COMMITTED rows are indexed as alive)."""
        self.db.execute(
            "INSERT OR REPLACE INTO records (record_id, tenant_id, owner, session_id, key, status, tier,"
            " recorded_at, doc) VALUES (?,?,?,?,?,?,?,?,?)",
            (r.record_id, r.tenant_id, r.owner, r.session_id, r.claim.key, r.status.value, int(r.tier),
             ts_str(r.recorded_at), r.model_dump_json()))
        if r.status != Status.COMMITTED:
            self.index.delete(r.tenant_id, r.record_id)
        else:
            self.index.update_meta(r.tenant_id, r.record_id, **index_meta(r))

    def get_record(self, record_id: str) -> MemoryRecord | None:
        row = self.db.one("SELECT doc FROM records WHERE record_id=?", (record_id,))
        return MemoryRecord.model_validate_json(row[0]) if row else None

    def records(self, tenant_id: str | None = None, *, status: Status | Iterable[Status] | None = None,
                key: str | None = None, owner: str | None = None) -> list[MemoryRecord]:
        sql, args = "SELECT doc FROM records WHERE 1=1", []
        if tenant_id is not None:
            sql, args = sql + " AND tenant_id=?", args + [tenant_id]
        if status is not None:
            sts = [status] if isinstance(status, Status) else list(status)
            sql += f" AND status IN ({','.join('?' * len(sts))})"
            args += [s.value for s in sts]
        if key is not None:
            sql, args = sql + " AND key=?", args + [key]
        if owner is not None:
            sql, args = sql + " AND owner=?", args + [owner]
        sql += " ORDER BY recorded_at"
        return [MemoryRecord.model_validate_json(r[0]) for r in self.db.query(sql, args)]

    # ------------------------------------------------------------------ vectors
    async def index_record(self, r: MemoryRecord) -> None:
        """Embed and index a COMMITTED record (call after put_record with status=COMMITTED)."""
        vec = (await self.embedder.embed([r.content], "passage"))[0]
        self.index.upsert(r.tenant_id, r.record_id, vec, index_meta(r))

    def unindex(self, r: MemoryRecord) -> None:
        self.index.delete(r.tenant_id, r.record_id)

    async def search(self, tenant_id: str, query: str, k: int, mask: Mask | None = None,
                     input_type: str = "query") -> list[tuple[MemoryRecord, float]]:
        """Retrieval over one tenant's committed records; `mask` runs before scoring."""
        vec = (await self.embedder.embed([query], input_type))[0]
        hits = self.index.search(tenant_id, vec, k, mask)
        out = []
        for rid, score in hits:
            rec = self.get_record(rid)
            if rec is not None and rec.status == Status.COMMITTED:
                out.append((rec, score))
        return out

    async def similar(self, tenant_id: str, text: str, k: int, mask: Mask | None = None
                      ) -> list[tuple[MemoryRecord, float]]:
        """Passage-to-passage neighbours (conflict candidates for the write path)."""
        return await self.search(tenant_id, text, k, mask, input_type="passage")

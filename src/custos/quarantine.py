"""Quarantine queue (SPEC §5.5): held records wait for a reviewer, a user confirmation or a retry."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ulid import ULID

from .crypto import now_utc
from .types import MemoryRecord, QuarantineItem, SourceType, Status, Tier, Verdict

if TYPE_CHECKING:  # pragma: no cover
    from .write import WritePipeline

MAX_RETRIES = 3


class QuarantineQueue:
    def __init__(self, pipeline: WritePipeline):
        self.p = pipeline
        self.db = pipeline.store.db

    # ------------------------------------------------------------------ storage
    def _save(self, it: QuarantineItem) -> None:
        self.db.execute("INSERT OR REPLACE INTO quarantine (qid, tenant_id, record_id, status, doc) VALUES (?,?,?,?,?)",
                        (it.qid, it.tenant_id, it.record_id, it.status, it.model_dump_json()))

    def get(self, qid: str) -> QuarantineItem | None:
        row = self.db.one("SELECT doc FROM quarantine WHERE qid=?", (qid,))
        return QuarantineItem.model_validate_json(row[0]) if row else None

    def for_record(self, record_id: str, status: str | None = "open") -> list[QuarantineItem]:
        sql, args = "SELECT doc FROM quarantine WHERE record_id=?", [record_id]
        if status:
            sql, args = sql + " AND status=?", args + [status]
        return [QuarantineItem.model_validate_json(r[0]) for r in self.db.query(sql, args)]

    def list(self, tenant_id: str, status: str = "open") -> list[QuarantineItem]:
        rows = self.db.query("SELECT doc FROM quarantine WHERE tenant_id=? AND status=?", (tenant_id, status))
        return sorted((QuarantineItem.model_validate_json(r[0]) for r in rows), key=lambda i: i.created_at)

    def create(self, rec: MemoryRecord, rule: str, reasons: list[str], conflicts: list[str], kind: str) -> QuarantineItem:
        it = QuarantineItem(qid="q_" + str(ULID()), tenant_id=rec.tenant_id, record_id=rec.record_id, rule=rule,
                            reasons=reasons, conflicts_with=conflicts, kind=kind, created_at=now_utc())  # type: ignore[arg-type]
        self._save(it)
        return it

    def _open(self, qid: str) -> tuple[QuarantineItem, MemoryRecord]:
        it = self.get(qid)
        if it is None:
            raise KeyError(f"unknown quarantine item {qid}")
        if it.status != "open":
            raise ValueError(f"{qid} is already {it.status}")
        rec = self.p.store.get_record(it.record_id)
        if rec is None or rec.status != Status.QUARANTINED:
            raise ValueError(f"record {it.record_id} is not quarantined")
        return it, rec

    def _close(self, it: QuarantineItem, status: str, actor: str, note: str | None) -> None:
        it.status, it.resolved_at, it.reviewer, it.note = status, now_utc(), actor, note  # type: ignore[assignment]
        self._save(it)

    # ------------------------------------------------------------------ resolutions
    async def release(self, qid: str, actor: str, note: str | None = None) -> MemoryRecord:
        """Commit the quarantined record, or version it over the committed records it conflicts with."""
        it, rec = self._open(qid)
        out = await self.p.release_record(rec, it, actor, note)
        self._close(it, "released", actor, note)
        return out

    async def confirm_t3(self, qid: str, actor: str) -> MemoryRecord:
        """USER_CONFIRMED upgrade (T3, out-of-band confirmation), then release."""
        it, rec = self._open(qid)
        src = await self.p._register(
            tenant_id=rec.tenant_id, owner=rec.owner, session_id=rec.session_id, source_type=SourceType.USER_CONFIRMED,
            origin=f"user:{actor}", principal=actor, raw=f"user {actor} confirmed {rec.record_id} {rec.claim.key}",
            external_ref=rec.record_id)
        rec.sources = [*rec.sources, src.source_id]
        rec.base_tier = rec.tier = Tier.T3
        rec.flags = [*rec.flags, "user_confirmed"]
        self.p.prov.add(rec.record_id, src.source_id, "cites")
        out = await self.p.release_record(rec, it, actor, "confirmed", event_note="confirm_t3")
        self._close(it, "released", actor, "confirmed")
        return out

    async def reject(self, qid: str, actor: str, note: str | None = None) -> None:
        it, rec = self._open(qid)
        await self.p.reject_record(rec, actor, note, it)
        self._close(it, "rejected", actor, note)

    async def retry(self, qid: str) -> list[Verdict]:
        """Fail-closed hold: re-run the checks (up to 3 times, then the item becomes a review item)."""
        it, rec = self._open(qid)
        if it.kind != "retry":
            raise ValueError("only kind=retry items can be retried")
        n = self.p.retry_count(rec.record_id) + 1
        self.p.set_retry_count(rec.record_id, n)
        self._close(it, "expired", "gateway", f"re-evaluated (attempt {n})")
        vs = await self.p.reevaluate(rec)
        if vs and vs[0].rule == "R0_CHECKER_UNAVAILABLE" and n >= MAX_RETRIES:
            for i in self.for_record(rec.record_id):
                i.kind = "review"
                i.reasons = [*i.reasons, f"checkers unavailable after {n} retries: manual review"]
                self._save(i)
        return vs

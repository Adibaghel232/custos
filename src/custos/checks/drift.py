"""Check f: per tenant x subject CUSUM drift alarm (SPEC §5.4 f).

x_t = w(relation) * g(tier) * c;  S_t = max(0, S_{t-1} + x_t - k);  alarm when S_t > h.
w: critical 1.0, normal 0.4, other:* 0.1.  g: T0 1.0, T1 0.6, T2 0.2, T3+ 0.
c: 1.0 if the subject already has a T3+ claim, else 0.5.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..crypto import ts_str
from ..db import DB
from ..store import NativeStore
from ..types import CheckResult, MemoryRecord, Status

G = {0: 1.0, 1: 0.6, 2: 0.2}
DEFAULT_K, DEFAULT_H = 0.25, 1.5      # SPEC §5.4 f; override via settings thresholds drift_k / drift_h (dev-calibrated)


def weight(rec: MemoryRecord) -> float:
    if rec.claim.relation.startswith("other:"):
        return 0.1
    return 1.0 if rec.claim.critical else 0.4


class Drift:
    def __init__(self, db: DB, store: NativeStore, k: float = DEFAULT_K, h: float = DEFAULT_H, window_days: int = 30):
        self.db, self.store, self.k, self.h, self.window = db, store, k, h, window_days

    def state(self, tenant_id: str, subject: str) -> float:
        row = self.db.one("SELECT s FROM cusum_state WHERE tenant_id=? AND entity=?", (tenant_id, subject))
        return float(row["s"]) if row else 0.0

    def has_t3(self, tenant_id: str, subject: str) -> bool:
        prefix = f"{tenant_id}|{subject}|"
        return self.db.one("SELECT 1 FROM records WHERE tenant_id=? AND status='committed' AND tier>=3"
                           " AND substr(key,1,?)=? LIMIT 1", (tenant_id, len(prefix), prefix)) is not None

    def peek(self, rec: MemoryRecord, pending: dict[str, float]) -> tuple[CheckResult, float]:
        """Next CUSUM value for the record's subject (chained through `pending` within one write). Not persisted."""
        subj = rec.claim.subject
        s_prev = pending.get(subj, self.state(rec.tenant_id, subj))
        c = 1.0 if self.has_t3(rec.tenant_id, subj) else 0.5
        x = weight(rec) * G.get(int(rec.tier), 0.0) * c
        s = max(0.0, s_prev + x - self.k)
        pending[subj] = s
        alarm = s > self.h
        return (CheckResult(check="drift", ok=not alarm, label="alarm" if alarm else None, score=s,
                            detail={"x": round(x, 4), "s_prev": round(s_prev, 4), "k": self.k, "h": self.h}), s)

    def persist(self, tenant_id: str, subject: str, s: float, when: datetime) -> None:
        self.db.execute("INSERT OR REPLACE INTO cusum_state (tenant_id, entity, s, last_ts, doc) VALUES (?,?,?,?,?)",
                        (tenant_id, subject, s, ts_str(when), b"{}"))

    def window_records(self, tenant_id: str, subject: str, now: datetime) -> list[MemoryRecord]:
        """Recent low-tier (T0/T1) claims on the subject, to flag `drift_window` on alarm."""
        lo = now - timedelta(days=self.window)
        return [r for r in self.store.records(tenant_id, status=[Status.COMMITTED, Status.QUARANTINED])
                if r.claim.subject == subject and int(r.tier) <= 1 and r.recorded_at >= lo]

"""One SQLite database per gateway instance (WAL). Holds every Custos table, including the ledger."""
from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  source_id TEXT PRIMARY KEY, tenant_id TEXT, owner TEXT, session_id TEXT, source_type TEXT,
  tier INTEGER, received_at TEXT, raw_hash TEXT, erased INTEGER DEFAULT 0, doc BLOB);
CREATE INDEX IF NOT EXISTS ix_sources_owner ON sources(tenant_id, owner);
CREATE TABLE IF NOT EXISTS records (
  record_id TEXT PRIMARY KEY, tenant_id TEXT, owner TEXT, session_id TEXT, key TEXT, status TEXT,
  tier INTEGER, recorded_at TEXT, doc BLOB);
CREATE INDEX IF NOT EXISTS ix_records_key ON records(tenant_id, key, status);
CREATE INDEX IF NOT EXISTS ix_records_status ON records(tenant_id, status);
CREATE TABLE IF NOT EXISTS provenance_edges (from_id TEXT, to_id TEXT, type TEXT, PRIMARY KEY (from_id, to_id, type));
CREATE INDEX IF NOT EXISTS ix_prov_to ON provenance_edges(to_id, type);
CREATE TABLE IF NOT EXISTS quarantine (qid TEXT PRIMARY KEY, tenant_id TEXT, record_id TEXT, status TEXT, doc BLOB);
CREATE TABLE IF NOT EXISTS read_log (read_id TEXT PRIMARY KEY, tenant_id TEXT, session_id TEXT, ts TEXT, doc BLOB);
CREATE TABLE IF NOT EXISTS gate_log (gate_id TEXT PRIMARY KEY, tenant_id TEXT, session_id TEXT, ts TEXT, doc BLOB);
CREATE TABLE IF NOT EXISTS fingerprints (value_fp TEXT, tenant_id TEXT, first_tier INTEGER, first_source TEXT,
  first_seen TEXT, PRIMARY KEY (value_fp, tenant_id));
CREATE TABLE IF NOT EXISTS cusum_state (tenant_id TEXT, entity TEXT, s REAL, last_ts TEXT, doc BLOB,
  PRIMARY KEY (tenant_id, entity));
CREATE TABLE IF NOT EXISTS ledger (
  seq INTEGER PRIMARY KEY, ts TEXT, event TEXT, tenant_id TEXT, subject_id TEXT, actor TEXT, payload BLOB,
  payload_digest TEXT, prev_hash TEXT, entry_hash TEXT, signature TEXT, key_id TEXT);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v BLOB);
"""


class DB:
    """Thread-safe sqlite3 wrapper. Operations are sub-millisecond, so async callers may call it directly."""

    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.lock = threading.RLock()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Atomic multi-statement transaction (re-entrant within a thread)."""
        with self.lock:
            nested = self.conn.in_transaction
            if not nested:
                self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                if not nested:
                    self.conn.execute("COMMIT")
            except BaseException:
                if not nested:
                    self.conn.execute("ROLLBACK")
                raise

    def backup_to(self, path: str | Path) -> None:
        dst = sqlite3.connect(str(path))
        with self.lock:
            self.conn.backup(dst)
        dst.close()

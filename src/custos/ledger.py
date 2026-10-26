"""Append-only, hash-chained, Ed25519-signed custody ledger (SPEC §9).

entry_hash = sha256(f"{prev_hash}|{seq}|{ts}|{event}|{subject_id}|{payload_digest}"), genesis prev = "0"*64.
Personal content never goes into payloads in clear: callers pass digests (sha256) for PII text.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

import orjson

from .crypto import Signer, jcs, now_utc, sha256_hex, ts_str
from .db import DB
from .types import LedgerEntry

GENESIS = "0" * 64
CHECKPOINT_EVERY = 500


def entry_hash(prev_hash: str, seq: int, ts: str, event: str, subject_id: str | None, payload_digest: str) -> str:
    return sha256_hex(f"{prev_hash}|{seq}|{ts}|{event}|{subject_id or ''}|{payload_digest}")


class Ledger:
    def __init__(self, db: DB, signer: Signer):
        self.db = db
        self.signer = signer
        if self.db.one("SELECT 1 FROM ledger LIMIT 1") is None:
            self.append("key", tenant_id=None, subject_id=signer.key_id, actor="gateway",
                        payload={"public_key": signer.public_key_hex, "alg": "ed25519"})

    def head(self) -> tuple[int, str]:
        row = self.db.one("SELECT seq, entry_hash FROM ledger ORDER BY seq DESC LIMIT 1")
        return (row["seq"], row["entry_hash"]) if row else (0, GENESIS)

    def append(self, event: str, *, tenant_id: str | None, subject_id: str | None, actor: str,
               payload: dict[str, Any]) -> LedgerEntry:
        body = jcs(payload)
        digest = sha256_hex(body)
        with self.db.tx():
            seq, prev = self.head()
            seq += 1
            ts = ts_str(now_utc())
            h = entry_hash(prev, seq, ts, event, subject_id, digest)
            sig = self.signer.sign_hex(h)
            self.db.execute(
                "INSERT INTO ledger (seq, ts, event, tenant_id, subject_id, actor, payload, payload_digest,"
                " prev_hash, entry_hash, signature, key_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (seq, ts, event, tenant_id, subject_id, actor, body, digest, prev, h, sig, self.signer.key_id))
        entry = LedgerEntry(seq=seq, ts=ts, event=event, tenant_id=tenant_id, subject_id=subject_id, actor=actor,
                            payload=orjson.loads(body), payload_digest=digest, prev_hash=prev, entry_hash=h,
                            signature=sig, key_id=self.signer.key_id)
        if seq % CHECKPOINT_EVERY == 0 and event != "checkpoint":
            self.checkpoint()
        return entry

    def checkpoint(self) -> LedgerEntry:
        seq, h = self.head()
        return self.append("checkpoint", tenant_id=None, subject_id=None, actor="gateway",
                           payload={"head_seq": seq, "head_hash": h})

    def entries(self, since: int = 0, tenant_id: str | None = None, limit: int | None = None,
                events: tuple[str, ...] | None = None) -> list[LedgerEntry]:
        sql, args = "SELECT * FROM ledger WHERE seq > ?", [since]
        if tenant_id is not None:
            sql += " AND (tenant_id = ? OR tenant_id IS NULL)"
            args.append(tenant_id)
        if events:
            sql += f" AND event IN ({','.join('?' * len(events))})"
            args.extend(events)
        sql += " ORDER BY seq"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [self._row(r) for r in self.db.query(sql, args)]

    @staticmethod
    def _row(r) -> LedgerEntry:
        return LedgerEntry(seq=r["seq"], ts=r["ts"], event=r["event"], tenant_id=r["tenant_id"],
                           subject_id=r["subject_id"], actor=r["actor"], payload=orjson.loads(r["payload"]),
                           payload_digest=r["payload_digest"], prev_hash=r["prev_hash"],
                           entry_hash=r["entry_hash"], signature=r["signature"], key_id=r["key_id"])

    def verify(self, pinned_public_key: str | None = None, checkpoint_hash: str | None = None) -> dict[str, Any]:
        """Check sequence, payload digests, hash chain, signatures, key pinning and (optionally) a published
        checkpoint. Returns {ok, entries, first_bad_seq, failures_by_type}."""
        rows = self.db.query("SELECT * FROM ledger ORDER BY seq")
        fails: Counter[str] = Counter()
        first_bad: int | None = None
        prev, expected_seq = GENESIS, 1
        pub = pinned_public_key
        key_id = sha256_hex(bytes.fromhex(pub))[:16] if pub else None

        def bad(kind: str, seq: int) -> None:
            nonlocal first_bad
            fails[kind] += 1
            first_bad = seq if first_bad is None else min(first_bad, seq)

        for r in rows:
            seq = r["seq"]
            if seq != expected_seq:
                bad("seq_gap", seq)
            if sha256_hex(r["payload"]) != r["payload_digest"]:
                bad("payload_digest", seq)
            if r["prev_hash"] != prev:
                bad("prev_hash", seq)
            h = entry_hash(r["prev_hash"], seq, r["ts"], r["event"], r["subject_id"], r["payload_digest"])
            if h != r["entry_hash"]:
                bad("entry_hash", seq)
            if r["event"] == "key" and pub is None:  # trust-on-first-use pin from the genesis key entry
                pub = orjson.loads(r["payload"]).get("public_key")
                key_id = sha256_hex(bytes.fromhex(pub))[:16] if pub else None
            if key_id and r["key_id"] != key_id:
                bad("key_id", seq)
            if not pub or not Signer.verify(pub, r["entry_hash"], r["signature"]):
                bad("signature", seq)
            prev, expected_seq = r["entry_hash"], seq + 1
        if checkpoint_hash is not None and not any(r["entry_hash"] == checkpoint_hash for r in rows):
            bad("checkpoint_missing", expected_seq)
        return {"ok": not fails, "entries": len(rows), "first_bad_seq": first_bad,
                "failures_by_type": dict(fails)}

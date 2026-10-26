"""Gateway objects -> the JSON shapes of DEMO_DECK_PLAN §3.8 (what the React console consumes)."""
from __future__ import annotations

import time
from collections import deque
from typing import Any

import orjson

from ..context import session_no
from ..crypto import Signer, sha256_hex, ts_str
from ..db import DB
from ..gateway import CustosGateway
from ..ledger import Ledger, entry_hash
from ..types import MemoryRecord, SourceRecord, SourceType, Status, Verdict

UI_SOURCE_TYPE = {
    SourceType.EMAIL: "email", SourceType.WEB: "web", SourceType.FILE: "file", SourceType.RAG_DOC: "file",
    SourceType.PEER_EXTERNAL: "peer_agent", SourceType.PEER_INTERNAL: "peer_agent", SourceType.USER: "user",
    SourceType.USER_CONFIRMED: "user", SourceType.SOR: "erp", SourceType.INTERNAL_TOOL: "tool",
    SourceType.INTERNAL_KB: "tool", SourceType.IMPORT: "tool", SourceType.SYSTEM: "system",
    SourceType.ASSISTANT: "derived", SourceType.SUMMARY: "derived",
}
UI_CHECK = {"key_conflict": "conflict", "embed_conflict": "conflict", "nli": "nli", "grounding": "grounding",
            "drift": "cusum", "instruction": "instruction"}
UI_LEDGER_EVENT = {
    "source": "WRITE", "write": "WRITE", "key": "WRITE", "checkpoint": "WRITE",
    "verdict": "DECIDE", "commit": "DECIDE", "version": "DECIDE", "quarantine": "DECIDE", "reject": "DECIDE",
    "corroborate": "DECIDE", "release": "PROMOTE", "read": "READ", "gate": "GATE", "gate_response": "GATE",
    "revert": "REVERT", "restore": "REVERT", "rederive": "REVERT", "erase": "ERASE",
}
STATUS_EVENTS = {"commit": "committed", "version": "committed", "quarantine": "quarantined", "reject": "rejected",
                 "release": "committed", "revert": "reverted", "restore": "committed", "erase": "erased"}


def tier_s(t: Any) -> str | None:
    return None if t is None else f"T{int(t)}"


def iso(dt: Any) -> str | None:
    return ts_str(dt) if dt is not None else None


def get_verdict(gw: CustosGateway, rec: MemoryRecord) -> Verdict | None:
    if not rec.verdict_id:
        return None
    row = gw.db.one("SELECT v FROM kv WHERE k=?", (f"verdict:{rec.verdict_id}",))
    return Verdict.model_validate_json(row[0]) if row else None


def ui_source(src: SourceRecord | None) -> dict[str, Any]:
    if src is None:
        return {"type": "derived", "ref": ""}
    return {"type": UI_SOURCE_TYPE.get(src.source_type, "tool"), "ref": src.external_ref or src.source_id,
            "from": src.origin}


def status_history(gw: CustosGateway, record_id: str) -> list[dict[str, Any]]:
    ev = tuple(STATUS_EVENTS)
    rows = gw.db.query(f"SELECT * FROM ledger WHERE subject_id=? AND event IN ({','.join('?' * len(ev))}) ORDER BY seq",
                       (record_id, *ev))
    return [{"ts": r["ts"], "status": STATUS_EVENTS[r["event"]], "by": r["actor"]} for r in rows]


def root_source(gw: CustosGateway, rec: MemoryRecord) -> SourceRecord | None:
    """The raw-evidence source behind a record (skips agent-generated ASSISTANT/SUMMARY wrapper sources)."""
    seen: set[str] = set()
    stack = list(rec.sources) + list(rec.parents)
    fallback = None
    while stack:
        n = stack.pop(0)
        if n in seen:
            continue
        seen.add(n)
        if n.startswith("mem_"):
            r = gw.store.get_record(n)
            stack += (r.sources + r.parents) if r else []
            continue
        s = gw.store.get_source(n)
        if s is None:
            continue
        fallback = fallback or s
        if s.source_type in (SourceType.ASSISTANT, SourceType.SUMMARY) and s.parents:
            stack += list(s.parents)
        elif s.source_type not in (SourceType.ASSISTANT, SourceType.SUMMARY):
            return s
    return fallback


def record_view(gw: CustosGateway, rec: MemoryRecord) -> dict[str, Any]:
    erased = rec.status == Status.ERASED
    main = root_source(gw, rec)
    summary = bool(rec.parents) or any(
        (s := gw.store.get_source(i)) is not None and s.source_type == SourceType.SUMMARY for i in rec.sources)
    v = get_verdict(gw, rec)
    out: dict[str, Any] = {
        "id": rec.record_id, "tenant": rec.tenant_id, "text": "[erased]" if erased else rec.content,
        "tier": tier_s(rec.tier), "status": rec.status.value, "kind": "summary" if summary else rec.claim.kind.value,
        "source": ui_source(main), "principal": main.principal if main else rec.owner,
        "session": session_no(rec.session_id), "parents": [*rec.parents, *rec.sources],
        "raw_hash": main.raw_hash if main else "", "ledger_seq": rec.ledger_seq or (main.ledger_seq if main else 0),
        "sig_ok": gw.record_sig_ok(rec), "valid_from": iso(rec.valid_from), "valid_to": iso(rec.valid_to),
        "recorded_at": iso(rec.recorded_at),
        "claims": [{"id": f"cl_{rec.record_id}_1", "s": rec.claim.subject, "r": rec.claim.relation,
                    "o": rec.claim.object, "type": rec.claim.kind.value,
                    **({"valid_from": iso(rec.claim.valid_from)} if rec.claim.valid_from else {})}],
        "status_history": status_history(gw, rec.record_id),
    }
    if v is not None:
        checks = []
        for c in v.checks:
            if c.check not in UI_CHECK:
                continue
            item: dict[str, Any] = {"check": UI_CHECK[c.check],
                                    "result": (c.label or ("ok" if c.ok else "flagged")) + (f" ({c.error})" if c.error else ""),
                                    "ms": round(c.latency_ms, 1)}
            if c.score is not None:
                item["score"] = c.score
            if c.against:
                item["against"] = c.against[0]
            if c.detail.get("model"):
                item["model"] = c.detail["model"]
            checks.append(item)
        out["checks"] = checks
        out["decision"] = {"verdict": v.action, "rule": v.rule, "explanation": "; ".join(v.reasons)}
        for cid in v.conflicts_with:
            other = gw.store.get_record(cid)
            if other is not None:
                out["conflicts_with"] = {"id": cid, "tier": tier_s(other.tier), "text": other.content}
                break
    return out


def ledger_row(row: Any, pub: str) -> dict[str, Any]:
    ok = entry_hash(row["prev_hash"], row["seq"], row["ts"], row["event"], row["subject_id"],
                    row["payload_digest"]) == row["entry_hash"] and Signer.verify(pub, row["entry_hash"], row["signature"])
    out: dict[str, Any] = {"seq": row["seq"], "ts": row["ts"], "event": UI_LEDGER_EVENT.get(row["event"], "WRITE"),
                           "actor": row["actor"], "payload_hash": row["payload_digest"],
                           "prev_hash": row["prev_hash"], "hash": row["entry_hash"], "sig_ok": ok,
                           "kind": row["event"]}
    if row["subject_id"] and row["event"] not in ("key", "checkpoint"):
        out["record_id"] = row["subject_id"]
    return out


def ledger_view(gw: CustosGateway, start: int = 0, limit: int | None = None, db: DB | None = None,
                until: int | None = None) -> list[dict[str, Any]]:
    d = db or gw.db
    sql, args = "SELECT * FROM ledger WHERE seq>=?", [start]
    if until is not None:
        sql, args = sql + " AND seq<=?", args + [until]
    sql += " ORDER BY seq" + (f" LIMIT {int(limit)}" if limit else "")
    return [ledger_row(r, gw.signer.public_key_hex) for r in d.query(sql, args)]


def verify_view(gw: CustosGateway) -> dict[str, Any]:
    t0 = time.perf_counter()
    rep = gw.verify_ledger()
    rep["ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return rep


def tamper_demo(gw: CustosGateway) -> dict[str, Any]:
    """Edit a payload digest on a COPY of the ledger and show that verification catches it (the real ledger is untouched)."""
    dst = DB(":memory:")
    with gw.db.lock:
        gw.db.conn.backup(dst.conn)
    n = dst.one("SELECT COUNT(*) FROM ledger")[0]
    seq = max(2, n // 2) if n > 1 else 1
    dst.execute("UPDATE ledger SET payload_digest=? WHERE seq=?", (sha256_hex("tampered"), seq))
    t0 = time.perf_counter()
    rep = Ledger(dst, gw.signer).verify(pinned_public_key=gw.signer.public_key_hex)
    rep["ms"] = round((time.perf_counter() - t0) * 1000, 2)
    rep.update({"tampered_seq": seq, "tampered_field": "payload_hash",
                "copy": ledger_view(gw, start=max(1, seq - 100), limit=200, db=dst)})
    return rep


def rollback_view(gw: CustosGateway, op_id: str) -> dict[str, Any]:
    d = gw.recover_detail(op_id) or {}
    return {"root": (d.get("roots") or [""])[0], "mode": d.get("mode", "revert"),
            "closure_by_hop": d.get("closure_by_hop", []), "rederived": d.get("rederived", []),
            "exposure": d.get("exposure", {"sessions": [], "users": [], "reads": 0, "tool_calls": []}),
            "ledger_seq": d.get("ledger_seq", 0), "op_id": op_id}


def neighbourhood(graph: dict[str, Any], focus: str, hops: int = 2) -> dict[str, Any]:
    """Undirected neighbourhood of `focus` within `hops` edges."""
    adj: dict[str, set[str]] = {}
    for e in graph["edges"]:
        adj.setdefault(e["source"], set()).add(e["target"])
        adj.setdefault(e["target"], set()).add(e["source"])
    seen, q = {focus}, deque([(focus, 0)])
    while q:
        n, d = q.popleft()
        if d >= hops:
            continue
        for m in adj.get(n, ()):
            if m not in seen:
                seen.add(m)
                q.append((m, d + 1))
    return {"nodes": [n for n in graph["nodes"] if n["id"] in seen],
            "edges": [e for e in graph["edges"] if e["source"] in seen and e["target"] in seen]}


def source_view(gw: CustosGateway, src: SourceRecord) -> dict[str, Any]:
    erased = bool(gw.db.one("SELECT erased FROM sources WHERE source_id=?", (src.source_id,))["erased"])
    return {**orjson.loads(src.model_dump_json()), "raw": None if erased else gw.blobs.text(src.raw_hash),
            "erased": erased}

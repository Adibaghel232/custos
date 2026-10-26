"""Gateway event bus + SPEC §18 envelopes + DEMO_DECK_PLAN §3.8 TraceEvent adapters. OTel spans are optional no-ops."""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from .context import session_no
from .crypto import now_utc, ts_str

# writer (C1) event names -> SPEC §18.2 names
WRITER_TYPE_MAP = {
    "stamp": "write.received", "claims": "write.extracted", "check": "write.check", "decision": "write.verdict",
    "quarantine": "quarantine.opened", "release": "quarantine.resolved", "reject": "quarantine.resolved",
    "source": "source.registered",
}
RECORD_ACTION_TYPES = {"commit": "record.committed", "version": "record.versioned",
                       "quarantine": "record.quarantined", "reject": "record.rejected"}
# §18.2 name -> TraceEvent.type used by the React console (§3.8)
TRACE_TYPE_MAP = {
    "write.received": "stamp", "write.extracted": "claims", "write.check": "check", "write.verdict": "decision",
    "read.performed": "memory_read", "gate.decision": "gate", "recover.completed": "revert",
    "agent.message": "message", "agent.tool_call": "tool_call", "ledger.checkpoint": "ledger",
}
ENVELOPE_KEYS = ("v", "seq", "ts", "type", "lane", "tenant_id", "session_id", "subject_id", "trace_id", "data")


class EventBus:
    """In-process pub/sub with a replay ring buffer. Subscribers are plain callables (thread-safe emit)."""

    def __init__(self, lane: str = "D3", buffer: int = 5000) -> None:
        self.lane = lane
        self._subs: list[Callable[[dict], None]] = []
        self._seq = 0
        self._lock = threading.Lock()
        self.buffer: deque[dict] = deque(maxlen=buffer)
        self.t0 = time.monotonic()

    def subscribe(self, cb: Callable[[dict], None]) -> Callable[[], None]:
        self._subs.append(cb)
        return lambda: self._subs.remove(cb) if cb in self._subs else None

    def emit(self, type: str, data: dict[str, Any] | None = None, *, tenant_id: str | None = None,
             session_id: str | None = None, subject_id: str | None = None, trace_id: str | None = None,
             raw_type: str | None = None, lane: str | None = None) -> dict:
        with self._lock:
            self._seq += 1
            ev = {"v": 1, "seq": self._seq, "ts": ts_str(now_utc()), "type": type, "lane": lane or self.lane,
                  "tenant_id": tenant_id, "session_id": session_id, "subject_id": subject_id,
                  "trace_id": trace_id, "data": data or {}}
            ev["payload"] = ev["data"]          # C1/C3 callers read "payload"
            if raw_type:
                ev["raw_type"] = raw_type
            self.buffer.append(ev)
            subs = list(self._subs)
        for cb in subs:
            try:
                cb(ev)
            except Exception:  # a broken subscriber must never break the gateway
                pass
        return ev

    def ingest_writer_event(self, ev: dict) -> list[dict]:
        """Wrap a C1 writer event {"type", "payload"} into §18 envelope(s) (verdicts also emit record.*)."""
        raw = ev.get("type", "")
        data = dict(ev.get("payload") or ev.get("data") or {})
        typ = WRITER_TYPE_MAP.get(raw, raw)
        kw = {"tenant_id": ev.get("tenant_id") or data.get("tenant_id"),
              "session_id": ev.get("session_id") or data.get("session_id"),
              "subject_id": data.get("record_id")}
        if raw in ("release", "reject"):
            data = {"qid": data.get("qid"), "status": "released" if raw == "release" else "rejected",
                    "reviewer": data.get("actor"), "record_id": data.get("record_id"), "tier": data.get("tier")}
        if raw == "alert":
            data = {"severity": "warning", "title": data.get("message", "alert"), "body": data.get("message", ""),
                    "refs": [data.get("record_id")] if data.get("record_id") else [], **data}
        out = [self.emit(typ, data, raw_type=raw, **kw)]
        if raw == "release":
            out.append(self.emit("record.versioned" if ev.get("payload", {}).get("action") == "version"
                                 else "record.committed", {"record_id": data.get("record_id"),
                                                           "tier": data.get("tier"), "status": "committed"}, **kw))
        action = data.get("action") or data.get("verdict")
        if typ == "write.verdict" and action in RECORD_ACTION_TYPES:
            out.append(self.emit(RECORD_ACTION_TYPES[action], {
                "record_id": data.get("record_id"), "tier": data.get("tier"), "status": data.get("status"),
                "supersedes": data.get("supersedes")}, **kw))
        return out

    def replay(self, since_seq: int = 0) -> list[dict]:
        return [e for e in list(self.buffer) if e["seq"] > since_seq]


def passes(ev: dict, *, tenant_id: str | None = None, session_id: str | None = None, lane: str | None = None,
           types: str | list[str] | None = None) -> bool:
    if tenant_id and ev.get("tenant_id") not in (None, tenant_id):
        return False
    if session_id and ev.get("session_id") not in (None, session_id):
        return False
    if lane and ev.get("lane") != lane:
        return False
    if types:
        ts = [t for t in (types.split(",") if isinstance(types, str) else types) if t]
        if ts and not any(ev["type"] == t or ev["type"].startswith(t.rstrip("*")) for t in ts):
            return False
    return True


def to_trace_event(ev: dict, t0: float | None = None) -> dict:
    """§18 envelope -> DEMO_DECK_PLAN §3.8 TraceEvent {seq, t_ms, side, session, type, payload}."""
    typ = TRACE_TYPE_MAP.get(ev["type"], ev.get("raw_type") or ev["type"])
    data = ev.get("data") or {}
    payload = data.get("ui", data) if typ in ("gate", "revert", "memory_read") else data
    return {"seq": ev["seq"], "t_ms": int((time.monotonic() - t0) * 1000) if t0 is not None else 0,
            "side": "custos" if ev.get("lane", "D3") == "D3" else "undefended",
            "session": session_no(ev.get("session_id")), "type": typ, "payload": payload}


def validate_envelope(ev: dict) -> list[str]:
    """Return a list of schema problems (empty = valid §18.2 envelope)."""
    bad = [k for k in ENVELOPE_KEYS if k not in ev]
    if not bad:
        if ev["v"] != 1 or not isinstance(ev["seq"], int) or not isinstance(ev["type"], str):
            bad.append("types")
        if ev["lane"] not in ("D0", "D3") or not isinstance(ev["data"], dict):
            bad.append("lane/data")
    return bad


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Any]:
    """OpenTelemetry span if the SDK is installed (SHOULD tier); otherwise a no-op."""
    try:
        from opentelemetry import trace  # type: ignore
    except Exception:
        yield None
        return
    with trace.get_tracer("custos").start_as_current_span(name) as sp:
        for k, v in attrs.items():
            try:
                sp.set_attribute(k, v if isinstance(v, (str, int, float, bool)) else str(v))
            except Exception:
                pass
        yield sp

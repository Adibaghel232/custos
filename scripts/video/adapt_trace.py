"""Adapt the recorded LIVE traces to the console's Trace schema (ui/src/types.ts), for both acts.

  .venv/Scripts/python.exe scripts/video/adapt_trace.py [--out deliverables/video/work/traces_ui]

Why an adapter: the golden-path recorder (scripts/demo_golden_path.py) wrote the right top-level Trace shape, but its event
payloads are the gateway's raw events (stamp/claims per source, gate proposals, aggregate memory_write, no record data).
The console needs one pipeline per record, full MemoryRecord payloads and GateDecision args. The reshaping is done in
scripts/video/rehydrate.py, which also rebuilds a real CustosGateway so graph / record drawer / ledger / rollback are real
code on the recorded data. Nothing is invented: see the module docstring there for the exact rules.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rehydrate as R  # noqa: E402

EVENT_TYPES = {"session_start", "inbound", "message", "memory_write", "stamp", "claims", "check", "decision", "memory_read",
               "tool_call", "gate", "revert", "ledger", "outcome", "beat"}
REQUIRED = {
    "session_start": ["n", "label"], "inbound": ["kind", "ref", "from", "text"], "message": ["role", "text"],
    "stamp": ["record_id", "tier", "source", "principal", "raw_hash", "ledger_seq"], "claims": ["record_id", "claims"],
    "check": ["record_id", "check", "result"], "decision": ["record_id", "verdict", "rule", "explanation", "status"],
    "memory_write": ["id", "text", "status", "kind", "source", "session"], "memory_read": ["read_id", "query", "returned"],
    "tool_call": ["call_id", "tool", "args", "risk", "status"], "gate": ["call_id", "tool", "args", "decision", "rule", "ms"],
    "outcome": ["side", "verdict", "money_moved"], "beat": ["id", "title"],
}


def validate(trace: dict) -> list[str]:
    errs: list[str] = []
    for k in ("trace_id", "scenario", "act", "recorded_at", "live", "models", "backend", "seed", "sessions", "events"):
        if k not in trace:
            errs.append(f"trace missing {k}")
    last_seq, last_t = -1, -1
    for e in trace["events"]:
        if e["seq"] <= last_seq or e["t_ms"] < last_t:
            errs.append(f"non-monotonic at seq {e['seq']}")
        last_seq, last_t = e["seq"], e["t_ms"]
        if e["type"] not in EVENT_TYPES or e["side"] not in ("undefended", "custos"):
            errs.append(f"bad event {e['seq']} {e['type']} {e['side']}")
        for k in REQUIRED.get(e["type"], []):
            if k not in e["payload"]:
                errs.append(f"event {e['seq']} {e['type']} missing payload.{k}")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(R.ROOT / "deliverables" / "video" / "work" / "traces_ui"))
    ap.add_argument("--src", default=str(R.ROOT / "demo" / "live" / "traces_live"))
    args = ap.parse_args()
    ok = True
    for act in (1, 2):
        b = R.build(Path(args.src) / f"trace_act{act}.json")
        errs = validate(b.trace)
        d = Path(args.out) / f"act{act}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"trace_act{act}.json").write_text(json.dumps(b.trace, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"act {act}: {len(b.trace['events'])} events, {b.notes['records']} records, {b.notes['ledger_entries']} ledger entries -> {d}")
        for k, v in b.notes["dropped"].items():
            print(f"   folded/dropped {v:3d}  {k}")
        for e in errs[:10]:
            print("   SCHEMA:", e)
        ok &= not errs
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

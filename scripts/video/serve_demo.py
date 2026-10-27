"""Serve the console on the recorded run: FastAPI create_app(...) over a rehydrated CustosGateway.

  .venv/Scripts/python.exe scripts/video/serve_demo.py --act 2 --port 8080

/api/traces serves the adapted live trace; /api/graph, /api/records, /api/ledger (+verify, +tamper-demo), /api/quarantine and
/api/rollback are the real gateway code over the recorded data. /api/bench/* has no real results yet, so the console falls back
to its SAMPLE fixtures for the Bench tab (watermarked in the UI).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rehydrate as R  # noqa: E402

from custos.api.app import create_app  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--act", type=int, default=2)
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--src", default=str(R.ROOT / "demo" / "live" / "traces_live"))
    ap.add_argument("--work", default=str(R.ROOT / "deliverables" / "video" / "work"))
    ap.add_argument("--bench", default=str(R.ROOT / "results" / "final" / "ui"))
    ap.add_argument("--ui", default=str(R.ROOT / "ui" / "dist"))
    a = ap.parse_args()

    raw = R.load_raw(Path(a.src) / f"trace_act{a.act}.json")
    first = R.build(raw)
    tdir = Path(a.work) / "traces_served" / f"act{a.act}"
    tdir.mkdir(parents=True, exist_ok=True)
    for old in tdir.glob("*.json"):
        old.unlink()
    (tdir / f"trace_act{a.act}.json").write_text(json.dumps(first.trace, ensure_ascii=False), encoding="utf-8")
    spare = [first.gw]

    def factory():
        return spare.pop() if spare else R.build(raw).gw      # first call: the prebuilt gateway; /api/demo/reset: a fresh one

    app = create_app(factory, traces_dir=str(tdir), bench_dir=a.bench, ui_dist=a.ui)
    import uvicorn
    print(f"[serve_demo] act {a.act}: {first.notes['records']} records, {first.notes['ledger_entries']} ledger entries -> http://{a.host}:{a.port}/", flush=True)
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()

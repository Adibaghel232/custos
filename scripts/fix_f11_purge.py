"""One-time cleanup: drop f11 episodes recorded before the leak-check fix (base.py, commit 6f89cb3) so the
resumable runner backfills them with the corrected target_behaviour. Safe to run multiple times (no-op once clean).
"""
from __future__ import annotations

import json
import pathlib

PATHS = ["runs/main/episodes.jsonl", "runs/ablation/episodes.jsonl"]

for p in PATHS:
    pp = pathlib.Path(p)
    if not pp.exists():
        continue
    lines = pp.read_text().splitlines()
    kept = [l for l in lines if json.loads(l).get("episode", {}).get("family") != "f11"]
    pp.write_text("\n".join(kept) + ("\n" if kept else ""))
    print(f"{p}: {len(lines)} -> {len(kept)} (dropped {len(lines) - len(kept)} f11 rows)")

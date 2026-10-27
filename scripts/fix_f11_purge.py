"""Purge results invalidated by fixes before the final re-run: f11 rows (leak-check fix, 6f89cb3) and every D3/D3-*
row (write-path fixes 09aa3a5 + c96e552) in runs/main; the whole ablation grid (D3-* only, partially run on mixed code).
The resumable runner then backfills exactly these. Idempotent: re-running after the backfill only drops fresh rows
if the marker file is absent, so it writes runs/.purged_<tag> once."""
from __future__ import annotations

import json
import pathlib
import sys

TAG = sys.argv[1] if len(sys.argv) > 1 else "freeze1"
marker = pathlib.Path(f"runs/.purged_{TAG}")
if marker.exists():
    print("already purged for", TAG)
    sys.exit(0)

main = pathlib.Path("runs/main/episodes.jsonl")
if main.exists():
    lines = main.read_text().splitlines()
    keep = []
    for line in lines:
        ep = json.loads(line).get("episode", {})
        if ep.get("family") == "f11" or str(ep.get("defence", "")).startswith("D3"):
            continue
        keep.append(line)
    main.write_text("\n".join(keep) + ("\n" if keep else ""))
    print(f"{main}: {len(lines)} -> {len(keep)}")
abl = pathlib.Path("runs/ablation/episodes.jsonl")
if abl.exists():
    abl.rename(abl.with_name("episodes.mixedcode.jsonl.bak"))
    print("ablation episodes moved aside")
marker.write_text("done\n")

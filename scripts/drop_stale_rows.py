"""Before the final metrics: keep only episode rows whose config_hash matches the frozen configuration, so rows from runs
before the threshold change (commit 09aa3a5 changed configs/settings.yaml thresholds -> new episode ids) are not
double-counted next to their re-runs. Removed rows go to <grid>/episodes.stale.jsonl. Usage: python scripts/drop_stale_rows.py runs
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from custos_bench.harness.config import config_hash  # noqa: E402


def main() -> None:
    base = Path(sys.argv[1] if len(sys.argv) > 1 else "runs")
    cur = config_hash()
    for f in sorted(base.glob("*/episodes.jsonl")):
        keep, stale = [], []
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                h = json.loads(line)["episode"].get("config_hash")
            except (json.JSONDecodeError, KeyError):
                continue
            (keep if h == cur else stale).append(line)
        if stale:
            with (f.parent / "episodes.stale.jsonl").open("a", encoding="utf-8") as fh:
                fh.write("\n".join(stale) + "\n")
            f.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
        print(f"{f}: kept {len(keep)}, moved {len(stale)} stale rows (config_hash != {cur})")


if __name__ == "__main__":
    main()

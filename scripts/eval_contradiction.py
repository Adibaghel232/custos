"""M7 contradiction F1: the Custos NLI check (src/custos/checks/nli.py, role "judge") on the 300 labelled test pairs of
src/custos_bench/datasets/contradiction_set.py -> runs/contradiction_eval.csv (read by custos_bench metrics).

Usage: python scripts/eval_contradiction.py [--out runs/contradiction_eval.csv] [--split test]
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from custos.checks.nli import MAX_PAIRS, run_nli  # noqa: E402
from custos_bench.datasets.contradiction_set import build, f1  # noqa: E402

GOLD = {"poison": "contradiction", "legit": "legit_update", "non": "compatible"}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "runs" / "contradiction_eval.csv"))
    ap.add_argument("--split", default="test")
    ap.add_argument("--concurrency", type=int, default=16)
    a = ap.parse_args()
    pairs = [p for p in build() if p.split == a.split]
    sem = asyncio.Semaphore(a.concurrency)
    preds: dict[int, tuple[str, float]] = {}

    async def batch(lo: int) -> None:
        chunk = [{"pair_id": i, "existing": {"text": pairs[i].existing, "tier": "T3", "date": "2026-03-01"},
                  "new": {"text": pairs[i].new}} for i in range(lo, min(lo + MAX_PAIRS, len(pairs)))]
        async with sem:
            res = await run_nli(chunk)
        for c in chunk:
            it = res.get(c["pair_id"])
            preds[c["pair_id"]] = (it.label, it.confidence) if it else ("missing", 0.0)

    await asyncio.gather(*(batch(i) for i in range(0, len(pairs), MAX_PAIRS)))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["pair_id", "split", "category", "subtype", "method", "gold", "pred", "confidence"])
        for i, p in enumerate(pairs):
            w.writerow([p.pair_id, p.split, p.category, p.subtype, "custos_nli", GOLD[p.category], *preds[i]])
    print(out, f1(pairs, [preds[i][0] == "contradicts" for i in range(len(pairs))]))


if __name__ == "__main__":
    asyncio.run(main())

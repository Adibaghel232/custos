"""Parallel version of custos_bench.metrics.audit.run_audit: one process per defence -> run_dir/provenance.csv.
The serial audit (125 tamper ops x 5 categories x each Custos variant, each a full ledger copy + verify) takes hours.
Usage: python scripts/audit_parallel.py runs [--per-category 40]
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]


def _one(args):
    from custos_bench.metrics.audit import NOT_RUN, audit_lineage, audit_tamper
    d, paths, per, run_name = args
    paths = [Path(p) for p in paths]
    ok = tot = 0
    for p in paths:
        a, b = audit_lineage(p)
        ok, tot = ok + a, tot + b
    rows = [{"run_id": run_name, "check": "lineage", "defence": d, "n": tot, "k": ok,
             "notes": f"{len(paths)} sampled ledgers; structural: signature + resolvable sources/parents + content-addressed blob uri"}]
    res = audit_tamper(paths, per)
    rows.append({"run_id": run_name, "check": "tamper", "defence": d, "n": sum(v["n"] for v in res.values()),
                 "k": sum(v["k"] for v in res.values()), "notes": json.dumps({"by_category": res, "not_run": NOT_RUN})})
    return rows


def main() -> None:
    import pandas as pd
    from custos_bench.records_io import load_all_records
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--per-category", type=int, default=40)
    a = ap.parse_args()
    recs = {r.episode.episode_id: r.episode.defence for r in load_all_records(a.run_dir)}
    by_def: dict[str, list[str]] = {}
    for sub in [a.run_dir, *sorted(p for p in a.run_dir.glob("*") if p.is_dir())]:
        led = sub / "ledgers"
        for p in sorted(led.glob("*.db")) if led.exists() else []:
            by_def.setdefault(recs.get(p.stem, "D3"), []).append(str(p))
    print({d: len(v) for d, v in by_def.items()}, flush=True)
    rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=len(by_def) or 1) as ex:
        for r in ex.map(_one, [(d, v, a.per_category, a.run_dir.name) for d, v in sorted(by_def.items())]):
            rows += r
    pd.DataFrame(rows).to_csv(a.run_dir / "provenance.csv", index=False)
    for r in rows:
        print(r["defence"], r["check"], f"{r['k']}/{r['n']}")


if __name__ == "__main__":
    main()

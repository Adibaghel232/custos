"""Real bench results -> the demo console's Bench view (results/final/ui/bench_summary.json + bench_F1/F3/F4/F5.json).

Input is a results dir written by `custos_bench.cli metrics/report` (metrics.json, final.json, latency_raw.csv).
The console expects percentages and the fixture row schema in ui/public/fixtures; rates in final.json are fractions.
Usage: python scripts/export_ui_bench.py results/final [--out results/final/ui] [--gpu-hours 160]
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

DEF = {"D0": "undefended", "D1": "pi_filter", "D2": "amemguard_style", "D3": "custos"}
LABEL = {"D0": "Undefended", "D1": "PI filter", "D2": "A-MemGuard-style", "D3": "Custos"}
PCT_KEYS = ("ASR_", "ISR_", "AASR_", "FQR_", "ERASE_", "TAMPER", "LINEAGE", "ATTR", "EXTRACT_", "UTIL_D")
PCT_EXCLUDE = {"TAMPER_N"}


def pct(x):
    return None if x is None else round(100 * float(x), 1)


def row_pct(m: dict) -> dict:
    return {"n": m["n"], "k": m["k"], "rate_pct": pct(m["rate"]), "ci_low_pct": pct(m["ci_low"]), "ci_high_pct": pct(m["ci_high"])}


def family_names() -> dict[str, str]:
    try:
        from custos_bench.attacks import FAMILIES
        return {k.lower(): v.name for k, v in FAMILIES.items()}
    except Exception:  # noqa: BLE001 - names are cosmetic
        return {}


def summary(res: Path, gpu_hours: float | None, run_id: str | None) -> dict:
    f = json.loads((res / "final.json").read_text(encoding="utf-8"))
    out = {}
    for k, v in f.items():
        out[k] = pct(v) if (isinstance(v, (int, float)) and k.startswith(PCT_KEYS) and k not in PCT_EXCLUDE) else v
    gpus = [g.strip() for g in str(f.get("GPU") or "").split(",") if "A100" in g]
    if gpus:
        out["GPU"] = f"{len(gpus)}× {gpus[0].replace('NVIDIA ', '')}"
    if gpu_hours is not None:
        out["GPU_HOURS"] = gpu_hours
    if run_id:
        out["RUN_ID"] = run_id
    out.pop("SAMPLE", None)
    return out


def fig_f1(m: dict) -> list[dict]:
    names, rows = family_names(), []
    for fam in sorted(m.get("by_family", {})):
        if not fam.lower().startswith("f"):
            continue
        for d, x in sorted(m["by_family"][fam].items()):
            a = x.get("ASR")
            if d in DEF and a and a.get("rate") is not None:
                rows.append({"family": fam.upper(), "family_name": names.get(fam.lower(), fam), "defence": DEF[d], "backend": "native",
                             "memory_state": "warm", "attacker": "static", **row_pct(a)})
    return rows


def fig_f3(m: dict) -> list[dict]:
    rows = []
    for d, x in m["by_defence"].items():
        for p in x.get("AdaptiveCurve") or []:
            if d in DEF:
                rows.append({"defence": DEF[d], "backend": "native", "memory_state": "warm", "iter": p["K"], **row_pct(p)})
    return rows


def fig_f4(m: dict) -> list[dict]:
    rows = []
    for d, x in m["by_defence"].items():
        fq, a = x.get("FQR"), x.get("ASR")
        if d in DEF and fq and a and fq.get("rate") is not None and a.get("rate") is not None:
            rows.append({"id": DEF[d], "label": LABEL[d], "kind": "defence", "backend": "native", "memory_state": "warm",
                         "fqr_pct": pct(fq["rate"]), "fqr_ci_low_pct": pct(fq["ci_low"]), "fqr_ci_high_pct": pct(fq["ci_high"]),
                         "asr_pct": pct(a["rate"]), "asr_ci_low_pct": pct(a["ci_low"]), "asr_ci_high_pct": pct(a["ci_high"])})
    return rows


def fig_f5(res: Path) -> list[dict]:
    p = res / "latency_raw.csv"
    if not p.exists():
        return []
    by: dict[str, list[float]] = {}
    crit: dict[str, bool] = {}
    with p.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("defence") != "D3" or not r.get("ms"):
                continue
            by.setdefault(r["op"], []).append(float(r["ms"]))
            crit[r["op"]] = str(r.get("on_critical_path")).lower() == "true"
    rows = []
    for op, xs in sorted(by.items()):
        xs.sort()
        p95 = xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]
        rows.append({"op": op, "label": op.replace("_", " ").title(), "defence": "custos", "backend": "native",
                     "p50_ms": round(statistics.median(xs), 2), "p95_ms": round(p95, 2), "n": len(xs),
                     "on_critical_path": crit.get(op, True)})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("res", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--gpu-hours", type=float, default=None)
    ap.add_argument("--run-id", default=None)
    a = ap.parse_args()
    out = a.out or a.res / "ui"
    out.mkdir(parents=True, exist_ok=True)
    m = json.loads((a.res / "metrics.json").read_text(encoding="utf-8"))
    files = {"bench_summary.json": summary(a.res, a.gpu_hours, a.run_id), "bench_F1.json": fig_f1(m),
             "bench_F3.json": fig_f3(m), "bench_F4.json": fig_f4(m), "bench_F5.json": fig_f5(a.res)}
    for name, data in files.items():
        (out / name).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{out / name}: {len(data)} {'keys' if isinstance(data, dict) else 'rows'}")


if __name__ == "__main__":
    main()

"""Write metrics.json and the CSV/JSON files of DEMO_DECK_PLAN §5 (episodes, cells, adaptive, latency_raw, utility,
configs, scores, final.json, FINAL.md, run_meta.json). Everything is derived from `Computed`; nothing is hand-typed."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from ..defences.factory import defence_label
from .compute import Computed

EPISODE_COLS = ["run_id", "episode_id", "seed", "config_id", "scenario", "family", "family_name", "variant_id", "backend",
                "defence", "memory_state", "warm_records", "attacker", "attack_iter", "is_attack", "poison_written",
                "poison_retrievable", "poison_retrieved", "target_behaviour", "gate_decision", "quarantined",
                "persistence_sessions", "benign_updates", "benign_updates_held", "task_success", "attribution_correct",
                "llm_calls", "tokens_in", "tokens_out", "extra_tokens_write", "wall_ms", "error"]
CELL_COLS = ["run_id", "metric", "backend", "defence", "family", "scenario", "memory_state", "attacker", "n", "k", "rate",
             "ci_low", "ci_high", "r3_upper", "grid", "user_policy"]


def _clean(o: Any) -> Any:
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "item"):
        return _clean(o.item())
    return o


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(_clean(obj), indent=2, default=str), encoding="utf-8")


def episodes_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=EPISODE_COLS)
    d = df.copy()
    d["config_id"] = d["defence"]
    d["family"] = d["family"].map(lambda f: f.upper() if f.startswith("f") and f[1:].isdigit() else f)
    d["family_name"] = d["family_name"].where(d["is_attack"], "BENIGN")
    d.loc[~d["is_attack"], "family"] = "BENIGN"
    d["defence"] = d["label"]
    d["benign_updates"], d["benign_updates_held"] = d["legit_writes"], d["false_quarantines"]
    d["task_success"] = d["benign_success"]
    d["attribution_correct"] = d["attrib_hit"]
    return d[[c for c in EPISODE_COLS if c in d.columns]]


def scores_table(metrics: dict) -> pd.DataFrame:
    rows = []
    for d, m in metrics["by_defence"].items():
        p = m["pillars"]
        rows.append({"run_id": metrics["run_id"], "defence": d, "label": m["label"], "P1_poisoning": p["security"],
                     "P2_provenance": p["provenance"], "P3_consistency": p["consistency"], "P4_access": p["access"],
                     "MGS": m["MGS"], "provisional": p.get("provisional", True),
                     "missing": json.dumps(p.get("missing", {}))})
    return pd.DataFrame(rows)


def _pct(m: dict | None, key: str = "rate") -> str:
    if not m or m.get(key) is None:
        return "n/a"
    return f"{100 * m[key]:.1f}%"


def _cell(m: dict | None) -> str:
    if not m:
        return "not measured"
    s = f"{100 * m['rate']:.1f}% [{100 * m['ci_low']:.1f}, {100 * m['ci_high']:.1f}] (k={m['k']}, n={m['n']})"
    if m.get("r3_upper") is not None:
        s += f"; rule of three <= {100 * m['r3_upper']:.2f}%"
    return s


def final_json(c: Computed, run_meta: dict | None) -> dict:
    bd = c.metrics["by_defence"]
    g = lambda d, k: (bd.get(d) or {}).get(k)  # noqa: E731
    r = lambda m: (m or {}).get("rate")  # noqa: E731
    f = {"RUN_ID": c.metrics["run_id"], "EPISODES": c.metrics["n_ok"], "N_ATTACK": c.metrics["n_attack_episodes"],
         "CALLS": c.metrics["usage"]["llm_calls"], "GPU_HOURS": None, "COST_USD": None,
         "GPU": ", ".join((run_meta or {}).get("gpu", [])) or None, "N_BACKENDS": int(c.episodes["backend_group"].nunique()) if len(c.episodes) else 0}
    [m["AdaptiveASR"] for m in bd.values() if m.get("AdaptiveASR")]
    f["K"] = max((len(m["AdaptiveCurve"]) for m in bd.values() if m.get("AdaptiveCurve")), default=None)
    for key, d in (("U", "D0"), ("PI", "D1"), ("AMG", "D2"), ("C", "D3")):
        f[f"ASR_{key}"] = r(g(d, "ASR"))
    f["ISR_U"], f["ISR_C"] = r(g("D0", "ISR")), r(g("D3", "ISR"))
    for key, d in (("U", "D0"), ("PI", "D1"), ("C", "D3")):
        f[f"AASR_{key}"] = r(g(d, "AdaptiveASR"))
    cells = pd.DataFrame(c.cells)

    def fam(metric: str, d: str, fid: str) -> float | None:
        if cells.empty:
            return None
        q = cells[(cells.metric == metric) & (cells.defence == d) & (cells.family == fid) & (cells.backend == "primary")]
        return float(q.iloc[0]["rate"]) if len(q) else None

    f["ASR_U_F03"], f["ASR_C_F03"], f["ASR_U_F04"] = fam("asr", "D0", "f03"), fam("asr", "D3", "f03"), fam("asr", "D0", "f04")
    f["ISR_U_MEM0"] = f["ISR_C_MEM0"] = None
    if not cells.empty and (cells.backend == "mem0").any():
        for d, key in (("D0", "ISR_U_MEM0"), ("D3", "ISR_C_MEM0")):
            q = cells[(cells.metric == "isr") & (cells.defence == d) & (cells.backend == "mem0") & (cells.family == "ALL")]
            f[key] = float(q.iloc[0]["rate"]) if len(q) else None
    f["FQR_C"], f["FQR_AMG"] = r(g("D3", "FQR")), r(g("D2", "FQR"))
    f["UTIL_D"] = (g("D3", "UtilityDelta") or {}).get("delta")
    lk = g("D3", "Leakage") or {}
    f["LEAK_K"], f["LEAK_N"] = lk.get("k"), lk.get("n")
    f["EXTRACT_C"], f["ERASE_C"], f["ERASE_U"] = r(g("D3", "Extraction")), (g("D3", "Erasure") or {}).get("completeness"), \
        (g("D0", "Erasure") or {}).get("completeness")
    f["DERIVED_N"] = None
    tm = g("D3", "Tamper") or {}
    f["TAMPER"], f["TAMPER_N"] = tm.get("rate"), tm.get("n")
    f["LINEAGE"], f["ATTR"], f["LEDGER_N"] = r(g("D3", "Lineage")), r(g("D3", "Attribution")), None
    oh = g("D3", "Overhead") or {}
    f["P95_READ"], f["P95_GATE"], f["TOKENS_WRITE"] = oh.get("read_ms_p95"), oh.get("gate_ms_p95"), oh.get("extra_tokens_write_p95")
    f["WRITES_S"] = f["READS_S"] = None
    f["MGS_C"], f["MGS_U"] = g("D3", "MGS") if bd.get("D3") else None, g("D0", "MGS") if bd.get("D0") else None
    f["MET"] = sum(1 for s in c.metrics["scorecard"] if s["measured"] not in (None, [None, None], [None, None, None]))
    f["DEMO_X"] = f["DEMO_K"] = None
    return f


def _mgs(x: dict) -> str:
    if x["MGS"] is None:
        return "n/a"
    return f"{x['MGS']:.3f}" + (" (provisional)" if x["pillars"].get("provisional") else "")


def final_md(c: Computed, fj: dict) -> str:
    m = c.metrics
    lines = [f"# FINAL - run `{m['run_id']}`", "", f"Generated {m['generated']} from `episodes.jsonl` only. "
             f"{m['n_ok']} ok episodes ({m['n_errors']} infrastructure errors excluded), {m['n_attack_episodes']} static attack episodes. "
             f"Primary grid: `{m['primary_grid']}`. Every rate: 95% Wilson interval, k, n.", "", "## Headline by defence", "",
             "| defence | ISR | ASR | ASR fatigued | Adaptive ASR@K | Attribution | FQR | Extraction | MGS |", "|---|---|---|---|---|---|---|---|---|"]
    for d, x in m["by_defence"].items():
        lines.append(f"| {d} ({x['label']}) | {_cell(x['ISR'])} | {_cell(x['ASR'])} | {_cell(x['ASR_fatigued'])} | "
                     f"{_cell(x['AdaptiveASR'])} | {_cell(x['Attribution'])} | {_cell(x['FQR'])} | {_cell(x['Extraction'])} | "
                     f"{_mgs(x)} |")
    lines += ["", "## Pillars", "", "| defence | Poisoning | Provenance | Consistency | Access | MGS | unmeasured |", "|---|---|---|---|---|---|---|"]
    for d, x in m["by_defence"].items():
        p = x["pillars"]
        fm = lambda v: "n/a" if v is None else f"{v:.3f}"  # noqa: E731
        lines.append(f"| {d} | {fm(p['security'])} | {fm(p['provenance'])} | {fm(p['consistency'])} | {fm(p['access'])} | "
                     f"{fm(x['MGS'])} | {p.get('missing') or '-'} |")
    lines += ["", "## Scorecard (14 metrics, headline defence)", "", "| id | metric | target | measured | CI | n |", "|---|---|---|---|---|---|"]
    for s in m["scorecard"]:
        lines.append(f"| {s['id']} | {s['metric']} | {s['target']} | {s['measured']} | {s['ci']} | {s['n']} |")
    if m["flags"]:
        lines += ["", "## Flags (read before quoting any number)", ""] + [f"- {x}" for x in m["flags"]]
    lines += ["", f"MGS formula: {m['mgs_formula']}. Baselines have no ledger, so Lineage = Tamper = 0 for them by construction; "
              "Attribution for them uses the stated stand-in 'top-ranked retrieved memory'.", "",
              "## final.json", "", "```json", json.dumps(_clean(fj), indent=2), "```"]
    return "\n".join(lines)


def configs_table(c: Computed) -> pd.DataFrame:
    from custos.session import GatewayFlags
    rows = []
    for d in sorted(c.episodes["defence"].unique()) if len(c.episodes) else []:
        params: dict[str, Any] = {}
        if d.startswith("D3"):
            params = GatewayFlags.for_defence(d).model_dump()
        rows.append({"config_id": d, "defence": defence_label(d), "params_json": json.dumps(params)})
    return pd.DataFrame(rows)


def write_all(c: Computed, out: Path, run_meta: dict | None = None) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    write_json(out / "metrics.json", c.metrics)
    paths["metrics"] = out / "metrics.json"
    episodes_table(c.episodes).to_csv(out / "episodes.csv", index=False)
    cells = pd.DataFrame(c.cells)
    (cells[[x for x in CELL_COLS if x in cells.columns]] if len(cells) else pd.DataFrame(columns=CELL_COLS)).to_csv(out / "cells.csv", index=False)
    pd.DataFrame(c.adaptive).to_csv(out / "adaptive.csv", index=False)
    pd.DataFrame(c.latency).to_csv(out / "latency_raw.csv", index=False)
    pd.DataFrame(c.utility).to_csv(out / "utility.csv", index=False)
    configs_table(c).to_csv(out / "configs.csv", index=False)
    scores_table(c.metrics).to_csv(out / "scores.csv", index=False)
    fj = final_json(c, run_meta)
    write_json(out / "final.json", fj)
    (out / "FINAL.md").write_text(final_md(c, fj), encoding="utf-8")
    if run_meta:
        write_json(out / "run_meta.json", run_meta)
    return paths

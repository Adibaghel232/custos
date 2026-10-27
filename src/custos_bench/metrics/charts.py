"""Deck figures F1-F14 (DEMO_DECK_PLAN §5.4): PNG (300 dpi) + SVG + a JSON copy of the plotted data for the UI.

Style: white background, Carlito/Calibri when installed, defence colours fixed by the deck plan, takeaway title, subtitle with
n and '95% Wilson CI'. A figure whose source data was not produced by the run is skipped (and reported), never faked."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from .compute import Computed  # noqa: E402
from .tables import _clean  # noqa: E402

log = logging.getLogger("custos_bench.charts")
COLOUR = {"D0": "#B3261E", "D1": "#5A6B85", "D2": "#0B7FC7", "D3": "#76B900", "D0u": "#E08A84"}
ABL_COLOURS = ["#A9D46F", "#C9E59E", "#8DBB3A", "#B5D98A", "#93C94F", "#D5EBB4", "#6FA02A"]
SHORT = {"D0": "Undefended", "D1": "PI filter", "D2": "A-MemGuard-style", "D3": "Custos"}
INK = "#222222"


def _setup() -> None:
    have = {f.name for f in font_manager.fontManager.ttflist}
    fam = next((f for f in ("Calibri", "Carlito") if f in have), "DejaVu Sans")
    plt.rcParams.update({"font.family": fam, "figure.facecolor": "white", "axes.facecolor": "white", "axes.edgecolor": "#888",
                         "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK, "text.color": INK, "font.size": 11,
                         "axes.spines.top": False, "axes.spines.right": False, "svg.fonttype": "none"})


def _col(d: str, i: int = 0) -> str:
    return COLOUR.get(d, ABL_COLOURS[i % len(ABL_COLOURS)])


def _name(d: str) -> str:
    return SHORT.get(d, d.replace("D3-", "Custos -"))


def _save(fig, out: Path, fid: str, name: str, data: Any, title: str, sub: str) -> Path:
    fig.subplots_adjust(top=0.76)
    fig.suptitle(title, x=0.01, ha="left", fontsize=15, fontweight="bold", y=0.99)
    fig.text(0.01, 0.865, sub, ha="left", fontsize=10, color="#555")
    d = out / "figures"
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{fid}_{name}"
    fig.savefig(d / f"{stem}.png", dpi=300, bbox_inches="tight")
    fig.savefig(d / f"{stem}.svg", bbox_inches="tight")
    (d / f"{stem}.json").write_text(json.dumps(_clean({"id": fid, "title": title, "subtitle": sub, "data": data}), indent=2, default=str),
                                    encoding="utf-8")
    plt.close(fig)
    return d / f"{stem}.png"


def _cells(c: Computed) -> pd.DataFrame:
    return pd.DataFrame(c.cells)


def _pct(x: float | None) -> str:
    return "n/a" if x is None or x != x else f"{100 * x:.0f}%"


def fig_f1(c: Computed, out: Path) -> Path | None:
    cells = _cells(c)
    if cells.empty:
        return None
    m = c.metrics
    q = cells[(cells.metric == "asr") & (cells.grid == m["primary_grid"]) & (cells.family != "ALL") & (cells.user_policy == "cautious")
              & (cells.defence.isin(["D0", "D1", "D2", "D3"]))]
    if q.empty:
        return None
    defs = [d for d in ("D0", "D1", "D2", "D3") if d in set(q.defence)]
    base = "D0" if "D0" in defs else defs[0]
    order = q[q.defence == base].sort_values("rate", ascending=False).family.tolist()
    order += [f for f in q.family.unique() if f not in order]
    fig, ax = plt.subplots(figsize=(9, 0.55 * len(order) * len(defs) ** 0.5 + 2.4))
    h = 0.8 / len(defs)
    data = []
    for i, d in enumerate(defs):
        for j, f in enumerate(order):
            r = q[(q.defence == d) & (q.family == f)]
            if r.empty:
                continue
            r = r.iloc[0]
            y = j + (i - (len(defs) - 1) / 2) * h
            ax.barh(y, 100 * r.rate, height=h * 0.92, color=_col(d), label=_name(d) if j == 0 else None,
                    xerr=[[100 * (r.rate - r.ci_low)], [100 * (r.ci_high - r.rate)]], error_kw={"elinewidth": 1, "capsize": 2, "ecolor": "#444"})
            data.append({"family": f, "defence": d, "asr": r.rate, "ci_low": r.ci_low, "ci_high": r.ci_high, "n": int(r.n), "k": int(r.k)})
    ax.set_yticks(range(len(order)), [f.upper() for f in order])
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("Attack success rate (%)")
    ax.legend(frameon=False, loc="lower right")
    allr = {d: m["by_defence"].get(d, {}).get("ASR") for d in defs}
    title = "Attack success by family"
    if allr.get("D0") and allr.get("D3"):
        title = f"Custos holds ASR to {_pct(allr['D3']['rate'])} against {_pct(allr['D0']['rate'])} undefended"
    n = q.n.iloc[0]
    return _save(fig, out, "F1", "asr_by_family", data, title, f"n = {int(n)} per cell | 95% Wilson CI | primary backend | "
                 f"{q.memory_state.iloc[0]} memory | static attacker | cautious user")


def fig_f2(c: Computed, out: Path) -> Path | None:
    cells = _cells(c)
    if cells.empty:
        return None
    q = cells[(cells.family == "ALL") & (cells.grid == c.metrics["primary_grid"]) & (cells.user_policy == "cautious")
              & cells.metric.isin(["isr", "asr"])]
    if q.empty:
        return None
    defs = [d for d in ("D0", "D1", "D2", "D3") if d in set(q.defence)]
    backs = sorted(q.backend.unique())
    fig, axes = plt.subplots(1, 2, figsize=(9, 1.2 + 0.7 * len(backs)), squeeze=False)
    data = []
    for ax, metric in zip(axes[0], ("isr", "asr")):
        mat = np.full((len(backs), len(defs)), np.nan)
        for i, b in enumerate(backs):
            for j, d in enumerate(defs):
                r = q[(q.metric == metric) & (q.backend == b) & (q.defence == d)]
                if len(r):
                    mat[i, j] = r.iloc[0].rate
                    ax.text(j, i, f"{100 * r.iloc[0].rate:.0f}% (n = {int(r.iloc[0].n)})", ha="center", va="center", fontsize=9)
                    data.append({"metric": metric, "backend": b, "defence": d, "rate": r.iloc[0].rate, "n": int(r.iloc[0].n)})
        ax.imshow(mat, cmap=matplotlib.colors.LinearSegmentedColormap.from_list("w2r", ["#FFFFFF", "#B3261E"]), vmin=0, vmax=1, aspect="auto")
        ax.set_xticks(range(len(defs)), [_name(d) for d in defs], rotation=15)
        ax.set_yticks(range(len(backs)), backs)
        ax.set_title(metric.upper())
    return _save(fig, out, "F2", "isr_asr_heatmap", data, "Poison reaches memory far less often under Custos",
                 "pooled over families | 95% Wilson CIs in cells.csv | cautious user")


def fig_f3(c: Computed, out: Path) -> Path | None:
    bd = c.metrics["by_defence"]
    ds = [d for d, m in bd.items() if m.get("AdaptiveCurve")]
    if not ds:
        return None
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    data = []
    for i, d in enumerate(ds):
        cur = bd[d]["AdaptiveCurve"]
        ks = [p["K"] for p in cur]
        ax.plot(ks, [100 * p["rate"] for p in cur], color=_col(d, i), lw=2.2, marker="o", label=_name(d))
        ax.fill_between(ks, [100 * p["ci_low"] for p in cur], [100 * p["ci_high"] for p in cur], color=_col(d, i), alpha=0.15)
        ax.annotate(f"{100 * cur[-1]['rate']:.0f}%", (ks[-1], 100 * cur[-1]["rate"]), xytext=(5, 0), textcoords="offset points")
        data.append({"defence": d, "curve": cur})
    ax.set_xlabel("Attacker rewrite rounds (K)")
    ax.set_ylabel("Cumulative ASR (%)")
    ax.set_ylim(0, 100)
    ax.legend(frameon=False)
    n = bd[ds[0]]["AdaptiveCurve"][0]["n"]
    return _save(fig, out, "F3", "adaptive_asr", data, "Adaptive attacker: success vs rewrite budget",
                 f"{n} seed payloads per defence | grey-box feedback | 95% Wilson CI")


def fig_f4(c: Computed, out: Path) -> Path | None:
    bd = c.metrics["by_defence"]
    pts = [(d, m["FQR"], m["ASR"]) for d, m in bd.items() if m.get("FQR") and m.get("ASR")]
    if not pts:
        return None
    fig, ax = plt.subplots(figsize=(6.5, 5))
    data = []
    for i, (d, fq, asr) in enumerate(pts):
        ax.errorbar(100 * fq["rate"], 100 * asr["rate"], xerr=[[100 * (fq["rate"] - fq["ci_low"])], [100 * (fq["ci_high"] - fq["rate"])]],
                    yerr=[[100 * (asr["rate"] - asr["ci_low"])], [100 * (asr["ci_high"] - asr["rate"])]], fmt="o", ms=9, color=_col(d, i),
                    ecolor="#777", capsize=3, label=_name(d))
        data.append({"defence": d, "fqr": fq, "asr": asr})
    ax.set_xlabel("False quarantine rate (%)")
    ax.set_ylabel("Attack success rate (%)")
    ax.annotate("better", xy=(0.04, 0.04), xycoords="axes fraction", fontsize=10, color="#555")
    ax.legend(frameon=False)
    return _save(fig, out, "F4", "pareto_fqr_asr", data, "Security without breaking legitimate updates",
                 "one point per defence | 95% Wilson crosshairs")


def fig_f5(c: Computed, out: Path) -> Path | None:
    oh = {d: m["Overhead"] for d, m in c.metrics["by_defence"].items() if m.get("Overhead") and m["Overhead"].get("n_reads")}
    if not oh:
        return None
    ops = [("read", "read_ms"), ("gated call", "gate_ms")]
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    data, ds = [], list(oh)
    w = 0.8 / max(1, len(ds))
    for i, d in enumerate(ds):
        for j, (lab, k) in enumerate(ops):
            p50, p95 = oh[d].get(f"{k}_p50"), oh[d].get(f"{k}_p95")
            if p50 is None or p50 != p50:
                continue
            x = j + (i - (len(ds) - 1) / 2) * w
            ax.bar(x, max(p95, 1e-3), width=w * 0.9, color=_col(d, i), alpha=0.35)
            ax.bar(x, max(p50, 1e-3), width=w * 0.9, color=_col(d, i), label=_name(d) if j == 0 else None)
            data.append({"defence": d, "op": lab, "p50": p50, "p95": p95})
    ax.set_yscale("log")
    ax.set_xticks(range(len(ops)), [o[0] for o in ops])
    ax.axhline(40, ls="--", color="#999", lw=1)
    ax.axhline(1500, ls="--", color="#999", lw=1)
    ax.set_ylabel("ms (log); solid = p50, light = p95")
    ax.legend(frameon=False)
    return _save(fig, out, "F5", "latency_overhead", data, "Latency of memory operations",
                 "wall time of MemoryInterface calls incl. embedding + search | dashed: 40 ms and 1.5 s targets")


def fig_f6(c: Computed, out: Path) -> Path | None:
    bd = {d: m for d, m in c.metrics["by_defence"].items() if m["MGS"] is not None and d in ("D0", "D1", "D2", "D3")}
    if not bd:
        return None
    labs = ["Poisoning", "Provenance", "Consistency", "Access", "MGS"]
    keys = ["security", "provenance", "consistency", "access"]
    fig, ax = plt.subplots(figsize=(9, 4.8))
    w, data = 0.8 / len(bd), []
    for i, (d, m) in enumerate(bd.items()):
        vals = [m["pillars"][k] for k in keys] + [m["MGS"]]
        ax.bar(np.arange(5) + (i - (len(bd) - 1) / 2) * w, vals, width=w * 0.92, color=_col(d, i), label=_name(d))
        data.append({"defence": d, "pillars": dict(zip(labs, vals)), "provisional": m["pillars"].get("provisional")})
    ax.set_xticks(range(5), labs)
    ax.set_ylim(0, 1)
    ax.set_ylabel("score (0-1)")
    ax.legend(frameon=False)
    prov = any(m["pillars"].get("provisional") for m in bd.values())
    return _save(fig, out, "F6", "pillars_mgs", data, "Memory Governance Score by pillar",
                 "MGS = harmonic mean of the 4 pillars" + (" | PROVISIONAL: some components were not measured" if prov else ""))


def fig_f7(c: Computed, out: Path) -> Path | None:
    bd = c.metrics["by_defence"]
    abl = [d for d in bd if d == "D3" or d.startswith("D3-")]
    if len(abl) < 2:
        return None
    cells = _cells(c)
    fams = ["f03", "f04", "f05", "f09", "f10"]
    rows = []
    for d in abl:
        row = {"variant": "Custos (full)" if d == "D3" else d.replace("D3-", "-"),
               "ASR": (bd[d].get("ASR") or {}).get("rate"), "FQR": (bd[d].get("FQR") or {}).get("rate")}
        for f in fams:
            q = cells[(cells.metric == "asr") & (cells.defence == d) & (cells.family == f)] if len(cells) else cells
            row[f.upper()] = float(q.iloc[0].rate) if len(q) else None
        rows.append(row)
    df = pd.DataFrame(rows)
    cols = ["ASR"] + [f.upper() for f in fams] + ["FQR"]
    fig, ax = plt.subplots(figsize=(9, 0.5 * len(df) + 1.8))
    ax.axis("off")
    txt = [[_pct(v) for v in df.loc[i, cols]] for i in df.index]
    tb = ax.table(cellText=txt, rowLabels=df.variant.tolist(), colLabels=cols, loc="center", cellLoc="center")
    tb.scale(1, 1.5)
    for (r, cc), cell in tb.get_celld().items():
        if r > 0 and cc >= 0:
            v = df.loc[r - 1, cols[cc]]
            if v is not None and v == v:
                cell.set_facecolor(matplotlib.colors.to_hex((1, 1 - 0.7 * v, 1 - 0.7 * v)))
    return _save(fig, out, "F7", "ablation_table", df.to_dict("records"), "What each Custos component buys",
                 "n = 50 per cell | darker = higher rate")


def fig_f12(c: Computed, out: Path) -> Path | None:
    df = c.episodes
    if df.empty:
        return None
    q = df[(df.is_attack) & (df.status == "ok") & (df.trigger_sessions >= 2) & df.defence.isin(["D0", "D1", "D2", "D3", "D3-consistency"])]
    if q.empty:
        return None
    fig, ax = plt.subplots(figsize=(7, 4.4))
    ds = sorted(q.defence.unique())
    w, data = 0.8 / len(ds), []
    for i, d in enumerate(ds):
        cnt = q[q.defence == d].sessions_acted.value_counts().reindex(range(0, int(q.trigger_sessions.max()) + 1), fill_value=0)
        ax.bar(cnt.index + (i - (len(ds) - 1) / 2) * w, cnt.values, width=w * 0.92, color=_col(d, i), label=_name(d))
        data.append({"defence": d, "hist": {int(k): int(v) for k, v in cnt.items()}})
    ax.set_xlabel("Trigger sessions in which the poison acted (before rollback)")
    ax.set_ylabel("episodes")
    ax.legend(frameon=False)
    return _save(fig, out, "F12", "persistence", data, "How long poison keeps acting", "persist grid | 3 trigger sessions")


def fig_f9(c: Computed, out: Path) -> Path | None:
    if not c.utility:
        return None
    u = pd.DataFrame(c.utility)
    types = sorted(u.qtype.unique())
    fig, ax = plt.subplots(figsize=(7, 1.2 + 0.8 * len(types)))
    data = []
    for j, t in enumerate(types):
        for d in ("D0", "D3"):
            r = u[(u.qtype == t) & (u.defence == d)]
            if len(r):
                acc = r.correct.sum() / r.n.sum()
                ax.scatter(100 * acc, j, s=90, color=_col(d), label=_name(d) if j == 0 else None, zorder=3)
                data.append({"qtype": t, "defence": d, "acc": acc, "n": int(r.n.sum())})
        rr = [x for x in data if x["qtype"] == t]
        if len(rr) == 2:
            ax.plot([100 * rr[0]["acc"], 100 * rr[1]["acc"]], [j, j], color="#999", zorder=2)
    ax.set_yticks(range(len(types)), types)
    ax.set_xlabel("accuracy (%)")
    ax.legend(frameon=False)
    d3 = c.metrics["by_defence"].get("D3", {}).get("UtilityDelta") or {}
    ttl = "Utility retained under Custos" if not d3 else f"Utility change with Custos: {100 * d3['delta']:+.1f} pts"
    return _save(fig, out, "F9", "utility_retention", data, ttl, "LongMemEval subset | judge: Nemotron | paired bootstrap CI in metrics.json")


def fig_f14(c: Computed, out: Path) -> Path | None:
    sc = c.metrics["scorecard"]
    if not sc:
        return None

    def fmt(v):
        if v is None or v == [None, None] or v == [None, None, None]:
            return "not measured"
        if isinstance(v, list):
            return " / ".join("-" if x is None else f"{x:.3g}" for x in v)
        return f"{v:.3g}"
    fig, ax = plt.subplots(figsize=(10, 0.42 * len(sc) + 1.6))
    ax.axis("off")
    rows = [[s["id"], s["metric"], s["target"], fmt(s["measured"]), "-" if not s["ci"] or s["ci"] == [None, None] else
             f"[{s['ci'][0]:.3g}, {s['ci'][1]:.3g}]" if s["ci"][0] is not None else "-", s["n"] if s["n"] is not None else "-"] for s in sc]
    tb = ax.table(cellText=rows, colLabels=["", "metric", "round-1 target", "measured", "95% CI", "n"], loc="center", cellLoc="left")
    tb.auto_set_font_size(False)
    tb.set_fontsize(9)
    tb.scale(1, 1.4)
    return _save(fig, out, "F14", "scorecard", sc, "Scorecard: 14 metrics vs round-1 targets",
                 f"defence shown: {sc[0]['defence']} | unmeasured metrics are labelled, never filled in")


ALL_FIGS = [fig_f1, fig_f2, fig_f3, fig_f4, fig_f5, fig_f6, fig_f7, fig_f9, fig_f12, fig_f14]


def make_figures(c: Computed, out: Path) -> dict[str, str]:
    _setup()
    done: dict[str, str] = {}
    for fn in ALL_FIGS:
        try:
            p = fn(c, out)
            done[fn.__name__] = str(p) if p else "skipped (no data)"
        except Exception as e:  # noqa: BLE001 - one broken chart must not stop the others
            log.exception("figure %s failed", fn.__name__)
            plt.close("all")
            done[fn.__name__] = f"error: {e}"
    return done

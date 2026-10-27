#!/usr/bin/env python
"""Custos final-presentation generator (AEGIS 2026 finals, PS-04, team Charlies).

    python scripts/build_deck.py --results <dir> --out deliverables/Custos_AEGIS2026_Final.pptx
    python scripts/build_deck.py --make-sample data/fake_results      # synthetic SAMPLE results dir (red watermark)

Reads the bench outputs written by `custos-bench metrics` (metrics.json is the source of truth; optional: run.json /
run_meta.json, cells.csv, compute.csv). Every number on a slide is read from those files; a missing metric renders as
"not measured". Also writes docs/CLAIMS.md (slide number -> metrics key map) and deliverables/claims_values.csv.

The template is docs/round1/round1_solution.pptx (cover slide, theme, fonts and logos are reused).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData, XyChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION, XL_MARKER_STYLE
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "docs" / "round1" / "round1_solution.pptx"
NM = "not measured"

# ------------------------------------------------------------------ palette (AEGIS 2026 theme)
NAVY, INK, GREEN, BLUE, AMBER = "0A1F4B", "0F1B33", "76B900", "0B7FC7", "F5A623"
GREY, RED, SLATE, CARD, WHITE = "5A6B85", "B3261E", "3A5A8C", "F6F8FC", "FFFFFF"
T_AMBER, T_GREEN, T_BLUE, LINE = "FDF1DC", "EDF6DC", "E4F0FA", "DDE5F0"
DEF_COLOR = {"D0": RED, "D1": GREY, "D2": BLUE, "D3": GREEN}
ABL_COLORS = ["A9D46F", "C9E59E", "8FBF4A", "B7D98A", "98C95A", "D6EBB4", "7FAF3A"]
DEF_LABEL = {"D0": "Undefended", "D1": "PI filter", "D2": "A-MemGuard-style", "D3": "Custos"}
ABL_NAME = {"tiers": "trust tiers", "consistency": "consistency checks", "gate": "consequence gate",
            "quoting": "quoted-data wrapping", "isolation": "isolation filter", "fingerprint": "fingerprint",
            "claimlwm": "claim low-water mark"}
FAMILY_NAME = {"f01": "Explicit command insertion", "f02": "Sleeper command", "f03": "Summary laundering",
               "f04": "Plausible false fact", "f05": "False precedent", "f06": "Poisoned procedure",
               "f07": "Retrieval-trigger backdoor", "f08": "Knowledge-base injection", "f09": "Slow drift",
               "f10": "Recommendation poisoning", "f11": "Cross-tenant extraction"}


def rgb(h: str) -> RGBColor:
    return RGBColor.from_string(h)


# ------------------------------------------------------------------ statistics (stdlib copy of custos_bench.metrics.stats)
def wilson(k: int, n: int, z: float = 1.96):
    if n <= 0:
        return None, None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def rate_row(k: int, n: int) -> dict:
    p, lo, hi = wilson(k, n)
    return {"n": n, "k": k, "rate": p, "ci_low": lo, "ci_high": hi, "r3_upper": min(1.0, 3 / n) if k == 0 and n else None}


# ------------------------------------------------------------------ results access + claim registry
class Results:
    """Dotted-path access into metrics.json that records every number shown on a slide (-> docs/CLAIMS.md)."""

    def __init__(self, metrics: dict, run_meta: dict | None, cells: list[dict], compute: list[dict], sample: bool, res_dir: Path):
        self.m, self.meta, self.cells, self.compute, self.sample, self.dir = metrics, run_meta or {}, cells, compute, sample, res_dir
        self.cur = 0
        self.claims: list[dict] = []

    def g(self, path: str):
        o = self.m
        for part in path.split("."):
            if isinstance(o, dict) and part in o:
                o = o[part]
            elif isinstance(o, list) and part.isdigit() and int(part) < len(o):
                o = o[int(part)]
            else:
                return None
        return o

    def _log(self, label: str, path: str, text: str, src: str = "metrics.json") -> None:
        self.claims.append({"slide": self.cur, "number": label, "key": path, "source": src, "value": text})

    # --- scalar formatters (return text, register claim)
    def pct(self, path: str, label: str, d: int = 1) -> str:
        v = self.g(path + ".rate") if isinstance(self.g(path), dict) else self.g(path)
        t = NM if v is None else fpct(v, d)
        self._log(label, path + (".rate" if isinstance(self.g(path), dict) else ""), t)
        return t

    def ci(self, path: str, label: str) -> str:
        m = self.g(path)
        if not isinstance(m, dict) or m.get("ci_low") is None:
            self._log(label + " 95% CI", path + ".ci_low/ci_high", NM)
            return NM
        t = f"{fpct(m['ci_low'])}–{fpct(m['ci_high'])}"
        self._log(label + " 95% CI", path + ".ci_low/ci_high", t)
        return t

    def n(self, path: str, label: str) -> str:
        m = self.g(path)
        t = NM if not isinstance(m, dict) or m.get("n") is None else f"{m['n']:,}"
        self._log(label + " n", path + ".n", t)
        return t

    def rl(self, path: str, label: str) -> str:
        """'5.0% [2.1–9.0]' (rate + 95% CI) or 'not measured'."""
        m = self.g(path)
        if not isinstance(m, dict) or m.get("rate") is None:
            self._log(label, path, NM)
            return NM
        t = f"{fpct(m['rate'])} [{fpct(m['ci_low'])}–{fpct(m['ci_high'])}]"
        self._log(label, path + ".rate/ci_low/ci_high", t)
        return t

    def rln(self, path: str, label: str) -> str:
        """rate + CI + n, for cells."""
        m = self.g(path)
        if not isinstance(m, dict) or m.get("rate") is None:
            self._log(label, path, NM)
            return NM
        t = f"{self.rl(path, label)}  n={m['n']:,}"
        return t

    def num(self, path: str, label: str, fmt: str = "{:.2f}", scale: float = 1.0, suffix: str = "") -> str:
        v = self.g(path)
        t = NM if v is None else fmt.format(v * scale) + suffix
        self._log(label, path, t)
        return t

    def raw(self, path: str, label: str, fmt: str = "{:,}"):
        v = self.g(path)
        t = NM if v is None else fmt.format(v)
        self._log(label, path, t)
        return t

    def have(self, path: str) -> bool:
        v = self.g(path)
        if isinstance(v, dict) and "rate" in v:
            return v["rate"] is not None
        return v is not None


def fpct(x: float | None, d: int = 1) -> str:
    if x is None:
        return NM
    if 0 < x < 0.001:
        return f"{100 * x:.2f}%"
    return f"{100 * x:.{d}f}%"


def load_results(res: Path, force_sample: bool) -> Results:
    p = res / "metrics.json" if res.is_dir() else res
    if not p.exists():
        sys.exit(f"metrics.json not found under {res} - run `custos-bench metrics <run_dir>` first")
    d = p.parent
    metrics = json.loads(p.read_text(encoding="utf-8"))
    meta = None
    for name in ("run_meta.json", "run.json"):
        if (d / name).exists():
            meta = json.loads((d / name).read_text(encoding="utf-8"))
            break
    cells = _read_csv(d / "cells.csv")
    compute = _read_csv(d / "compute.csv")
    sample = bool(metrics.get("SAMPLE")) or force_sample or "fake_results" in str(d).replace("\\", "/")
    return Results(metrics, meta, cells, compute, sample, d)


def _read_csv(p: Path) -> list[dict]:
    if not p.exists():
        return []
    with p.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# ------------------------------------------------------------------ pptx helpers
class Deck:
    def __init__(self, R: Results):
        self.R = R
        self.prs = Presentation(str(TEMPLATE))
        self.warn: list[str] = []
        self.appendix = False
        self.slide = None
        self.num = 0
        self.min_main = 14
        self.min_app = 10
        self.blank = self.prs.slide_layouts[0]

    # ---- text fitting estimate
    @staticmethod
    def _est_lines(text: str, size: float, width_in: float, bold: bool, font: str) -> int:
        f = (0.57 if bold else 0.53) if font == "Cambria" else (0.52 if bold else 0.49)
        cw = size / 72 * f
        lines = 0
        for para in text.split("\n"):
            words, cur = para.split(" "), 0.0
            n = 1
            for w in words:
                wl = len(w) * cw
                sp = cw
                if cur and cur + sp + wl > width_in:
                    n += 1
                    cur = wl
                else:
                    cur += (sp if cur else 0) + wl
            lines += n
        return lines

    def _check(self, name: str, text: str, size: float, w: float, h: float, bold: bool, font: str, after: float = 0.0, lsp: float = 1.2):
        lo = self.min_app if self.appendix else self.min_main
        if size < lo and not name.startswith("~"):
            self.warn.append(f"slide {self.num}: font {size}pt < {lo}pt in '{text[:40]}'")
        need = self._est_lines(text, size, max(w - 0.02, 0.2), bold, font) * size * lsp / 72 + after / 72
        if need > h * 1.04 + 0.03:
            self.warn.append(f"slide {self.num}: text may overflow ({need:.2f}in > {h:.2f}in) '{text[:50]}'")

    # ---- slide scaffolding
    def new_slide(self, tag: str, title: str, appendix: bool = False):
        self.appendix = appendix
        self.slide = self.prs.slides.add_slide(self.blank)
        self.num = len(self.prs.slides)
        self.R.cur = self.num
        self.tb(0.5, 0.40, 9.5, 0.3, tag, size=12, bold=True, color=SLATE, spc=200, anchor="m", name="~tag")
        if title:
            self.tb(0.5, 0.70, 12.33, 0.95, title, size=28 if not appendix else 26, bold=True, color=NAVY, font="Cambria", anchor="m")
        self.footer()
        return self.slide

    def footer(self):
        self.tb(0.5, 7.08, 8.5, 0.25, "Custos · Team Charlies · AEGIS 2026 · PS-04 Agent Memory Governance Evaluation",
                size=11, color=GREY, anchor="m", name="~foot")
        self.tb(9.2, 7.08, 3.63, 0.25, f"run {self.R.m.get('run_id', '?')}   ·   {self.num}", size=11, color=GREY,
                align="r", anchor="m", name="~foot")

    def source(self, text: str, y: float = 6.72, h: float = 0.3):
        self.tb(0.5, y, 12.33, h, text, size=12, color=GREY, italic=True, anchor="m", name="~src")

    # ---- primitives
    def tb(self, x, y, w, h, paras, size=14, color=INK, bold=False, font="Calibri", align="l", anchor="t", italic=False,
           spc=None, name=""):
        sh = self.slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = sh.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        tf.vertical_anchor = {"t": MSO_ANCHOR.TOP, "m": MSO_ANCHOR.MIDDLE, "b": MSO_ANCHOR.BOTTOM}[anchor]
        self._fill_tf(tf, paras, w, h, size, color, bold, font, align, italic, spc, name)
        return sh

    def _fill_tf(self, tf, paras, w, h, size, color, bold, font, align, italic, spc=None, name=""):
        if isinstance(paras, (str, dict)):
            paras = [paras]
        total_need = 0.0
        first = True
        for item in paras:
            d = item if isinstance(item, dict) else {"text": item}
            p = tf.paragraphs[0] if first else tf.add_paragraph()
            first = False
            al = d.get("align", align)
            p.alignment = {"l": PP_ALIGN.LEFT, "c": PP_ALIGN.CENTER, "r": PP_ALIGN.RIGHT}[al]
            after = d.get("after", 0)
            p.space_after = Pt(after)
            runs = d.get("runs") or [(d.get("text", ""), {})]
            psize = d.get("size", size)
            pbold = d.get("bold", bold)
            pfont = d.get("font", font)
            plain = ""
            for t, st in runs:
                r = p.add_run()
                r.text = t
                f = r.font
                f.name = st.get("font", pfont)
                f.size = Pt(st.get("size", psize))
                f.bold = st.get("bold", pbold)
                f.italic = st.get("italic", d.get("italic", italic))
                f.color.rgb = rgb(st.get("color", d.get("color", color)))
                if spc:
                    r._r.get_or_add_rPr().set("spc", str(spc))
                plain += t
            self._check(name, plain, psize, w, 99, pbold, pfont)  # min-size check only here
            total_need += self._est_lines(plain, psize, max(w - 0.02, 0.2), pbold, pfont) * psize * 1.2 / 72 + after / 72
        if total_need > h * 1.04 + 0.03 and not name.startswith("~"):
            txt = " | ".join((i if isinstance(i, str) else i.get("text") or "".join(t for t, _ in i["runs"]))[:30] for i in paras)
            self.warn.append(f"slide {self.num}: text may overflow ({total_need:.2f}in > {h:.2f}in) '{txt[:60]}'")

    def box(self, x, y, w, h, fill=CARD, line=None, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.04, dash=False, lw=1.0):
        sh = self.slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
        if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
            sh.adjustments[0] = radius
        if fill:
            sh.fill.solid()
            sh.fill.fore_color.rgb = rgb(fill)
        else:
            sh.fill.background()
        if line:
            sh.line.color.rgb = rgb(line)
            sh.line.width = Pt(lw)
            if dash:
                sh.line.dash_style = 4  # MSO_LINE.DASH
        else:
            sh.line.fill.background()
        sh.shadow.inherit = False
        return sh

    def card_text(self, x, y, w, h, paras, fill=CARD, line=None, pad=0.14, **kw):
        self.box(x, y, w, h, fill, line)
        return self.tb(x + pad, y + pad * 0.8, w - 2 * pad, h - 1.6 * pad, paras, **kw)

    def stat(self, x, y, w, h, big, label, src="", color=NAVY, fill=WHITE, big_size=32, line=LINE):
        self.box(x, y, w, h, fill, line)
        if big == NM or big.count(NM):
            big, big_size, color = NM, 20, GREY
        self.tb(x + 0.14, y + 0.08, w - 0.28, 0.62, big, size=big_size, bold=True, color=color, font="Cambria", anchor="m")
        lab_h = h - 0.78 - (0.3 if src else 0.0)
        self.tb(x + 0.14, y + 0.72, w - 0.28, lab_h, label, size=14, color=INK)
        if src:
            self.tb(x + 0.14, y + h - 0.34, w - 0.28, 0.26, src, size=12, color=GREY, anchor="m", name="~src")

    def line(self, x1, y1, x2, y2, color=LINE, w=1.5, dash=False):
        c = self.slide.shapes.add_connector(1, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
        c.line.color.rgb = rgb(color)
        c.line.width = Pt(w)
        if dash:
            c.line.dash_style = 4
        return c

    def dot(self, x, y, d, fill, line=None):
        return self.box(x, y, d, d, fill, line, shape=MSO_SHAPE.OVAL)

    def chip(self, x, y, w, h, text, fill=T_BLUE, color=NAVY, size=14, bold=True):
        self.box(x, y, w, h, fill, None, radius=0.3)
        self.tb(x + 0.1, y, w - 0.2, h, text, size=size, bold=bold, color=color, align="c", anchor="m")

    def not_measured(self, x, y, w, h, what=""):
        self.box(x, y, w, h, "FBFBFB", GREY, dash=True)
        self.tb(x, y, w, h, [{"text": NM.upper(), "bold": True, "color": GREY, "size": 20, "align": "c"},
                             {"text": what, "size": 14, "color": GREY, "align": "c"}], anchor="m")

    def table(self, x, y, w, rows: list[list[str]], col_w: list[float], row_h=0.36, size=14, header=True, fills=None,
              bold_first_col=False, aligns=None, header_fill=NAVY, colors=None, bold_cols=()):
        nr, nc = len(rows), len(rows[0])
        hdr_h = min(row_h, 0.4)
        gf = self.slide.shapes.add_table(nr, nc, Inches(x), Inches(y), Inches(w), Inches(row_h * (nr - 1) + hdr_h))
        tbl = gf.table
        tblPr = tbl._tbl.tblPr
        for a in ("bandRow", "firstRow"):
            tblPr.set(a, "0")
        sid = tblPr.find(qn("a:tableStyleId"))
        if sid is None:
            sid = etree.SubElement(tblPr, qn("a:tableStyleId"))
        sid.text = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"
        tot = sum(col_w)
        for j, cw in enumerate(col_w):
            tbl.columns[j].width = Inches(w * cw / tot)
        for i, row in enumerate(rows):
            tbl.rows[i].height = Inches(hdr_h if (header and i == 0) else row_h)
            for j, txt in enumerate(row):
                c = tbl.cell(i, j)
                c.margin_left = c.margin_right = Inches(0.07)
                c.margin_top = c.margin_bottom = Inches(0.02)
                c.vertical_anchor = MSO_ANCHOR.MIDDLE
                hdr = header and i == 0
                fill = header_fill if hdr else (fills[i][j] if fills and fills[i] and fills[i][j] else (CARD if i % 2 == 0 else WHITE))
                c.fill.solid()
                c.fill.fore_color.rgb = rgb(fill)
                tf = c.text_frame
                tf.word_wrap = True
                p = tf.paragraphs[0]
                p.alignment = {"l": PP_ALIGN.LEFT, "c": PP_ALIGN.CENTER, "r": PP_ALIGN.RIGHT}[(aligns[j] if aligns else "l")]
                r = p.add_run()
                r.text = txt
                r.font.name = "Calibri"
                r.font.size = Pt(size)
                r.font.bold = hdr or (bold_first_col and j == 0) or (j in bold_cols)
                col = WHITE if hdr else (colors[i][j] if colors and colors[i] and colors[i][j] else INK)
                r.font.color.rgb = rgb(col)
                cwi = w * col_w[j] / tot - 0.14
                need = self._est_lines(txt, size, cwi, bool(r.font.bold), "Calibri") * size * 1.18 / 72 + 0.04
                if need > (hdr_h if hdr else row_h) * 1.05:
                    self.warn.append(f"slide {self.num}: table cell may overflow ({need:.2f}in>{row_h:.2f}in) '{txt[:40]}'")
        lo = self.min_app if self.appendix else self.min_main
        if size < lo:
            self.warn.append(f"slide {self.num}: table font {size}pt < {lo}pt")
        return gf

    # ---- charts
    def chart_style(self, chart, size=14, legend=False):
        chart.font.size = Pt(size)
        chart.font.name = "Calibri"
        chart.font.color.rgb = rgb(INK)
        chart.has_title = False
        chart.has_legend = legend
        if legend:
            chart.legend.position = XL_LEGEND_POSITION.BOTTOM
            chart.legend.include_in_layout = False
            chart.legend.font.size = Pt(size)

    def axis_style(self, ax, vmin=None, vmax=None, fmt=None, grid=True, size=14, major=None):
        if vmin is not None:
            ax.minimum_scale = vmin
        if vmax is not None:
            ax.maximum_scale = vmax
        if major:
            ax.major_unit = major
        ax.has_major_gridlines = grid
        if grid:
            ax.major_gridlines.format.line.color.rgb = rgb(LINE)
            ax.major_gridlines.format.line.width = Pt(0.75)
        ax.format.line.color.rgb = rgb(GREY)
        ax.tick_labels.font.size = Pt(size)
        if fmt:
            ax.tick_labels.number_format = fmt
            ax.tick_labels.number_format_is_linked = False


C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def add_errbars(series, plus: list, minus: list, direction: str | None = None, color=INK, width=1.75, caps=True):
    """Custom +/- error bars on a native chart series (python-pptx has no API for them)."""
    def lit(vals):
        pts = "".join(f'<c:pt idx="{i}"><c:v>{v:.6f}</c:v></c:pt>' for i, v in enumerate(vals) if v is not None)
        return f'<c:numLit><c:formatCode>General</c:formatCode><c:ptCount val="{len(vals)}"/>{pts}</c:numLit>'
    d = f'<c:errDir val="{direction}"/>' if direction else ""
    xml = (f'<c:errBars xmlns:c="{C_NS}" xmlns:a="{A_NS}">{d}<c:errBarType val="both"/><c:errValType val="cust"/>'
           f'<c:noEndCap val="{0 if caps else 1}"/><c:plus>{lit(plus)}</c:plus><c:minus>{lit(minus)}</c:minus>'
           f'<c:spPr><a:ln w="{int(width * 12700)}"><a:solidFill><a:srgbClr val="{color}"/></a:solidFill></a:ln></c:spPr></c:errBars>')
    el = etree.fromstring(xml)
    ser = series._element
    anchor = ser.find(qn("c:cat"))
    if anchor is None:
        anchor = ser.find(qn("c:xVal"))
    anchor.addprevious(el)


def horiz_labels(ax):
    bp = ax._element.find(qn("c:txPr")).find(qn("a:bodyPr"))
    bp.set("rot", "0")
    bp.set("vert", "horz")


def clabel(d: str) -> str:
    return dlabel(d).replace("A-MemGuard-style", "A-MemGuard\nstyle")


def log_scale(value_axis):
    sc = value_axis._element.find(qn("c:scaling"))
    lb = etree.SubElement(sc, qn("c:logBase"))
    lb.set("val", "10")
    sc.remove(lb)
    sc.insert(0, lb)


# ------------------------------------------------------------------ deck content
def defence_ids(R: Results) -> list[str]:
    bd = R.g("by_defence") or {}
    return [d for d in ("D0", "D1", "D2", "D3") if d in bd]


def ablation_ids(R: Results) -> list[str]:
    bd = R.g("by_defence") or {}
    return sorted(d for d in bd if d.startswith("D3-"))


def dlabel(d: str) -> str:
    if d in DEF_LABEL:
        return DEF_LABEL[d]
    return "Custos − " + ABL_NAME.get(d.split("-", 1)[-1], d.split("-", 1)[-1])


def rate_of(R: Results, path: str):
    m = R.g(path)
    return m.get("rate") if isinstance(m, dict) else None


def bar_ci_chart(D: Deck, x, y, w, h, defs: list[str], metric: str, title: str, vmax=100):
    """Native column chart of one rate per defence with 95% Wilson whiskers; bars coloured by defence."""
    R = D.R
    D.tb(x, y, w, 0.32, title, size=16, bold=True, color=NAVY, anchor="m")
    vals, plus, minus = [], [], []
    for d in defs:
        m = R.g(f"by_defence.{d}.{metric}")
        if isinstance(m, dict) and m.get("rate") is not None:
            vals.append(100 * m["rate"])
            plus.append(100 * (m["ci_high"] - m["rate"]))
            minus.append(100 * (m["rate"] - m["ci_low"]))
        else:
            vals.append(None), plus.append(0), minus.append(0)
    if all(v is None for v in vals):
        D.not_measured(x, y + 0.36, w, h - 0.36, metric)
        return
    cd = CategoryChartData()
    cd.categories = [clabel(d) for d in defs]
    cd.add_series(metric, vals)
    gf = D.slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(x), Inches(y + 0.36), Inches(w), Inches(h - 0.36), cd)
    ch = gf.chart
    D.chart_style(ch)
    D.axis_style(ch.value_axis, 0, vmax, '0"%"', major=25)
    D.axis_style(ch.category_axis, grid=False)
    horiz_labels(ch.category_axis)
    pl = ch.plots[0]
    pl.gap_width = 60
    ser = pl.series[0]
    for i, d in enumerate(defs):
        pt = ser.points[i]
        pt.format.fill.solid()
        pt.format.fill.fore_color.rgb = rgb(DEF_COLOR.get(d, ABL_COLORS[i % len(ABL_COLORS)]))
    add_errbars(ser, plus, minus)


def slide_cover(D: Deck):
    s = D.prs.slides[0]
    D.slide, D.num = s, 1
    D.R.cur = 1
    for sh in s.shapes:
        if sh.name == "Title":
            for r in sh.text_frame.paragraphs[0].runs:
                r.font.size = Pt(54)
            sh.top, sh.height = Inches(1.5), Inches(1.0)
    D.tb(0.5, 2.62, 12.33, 0.75, "Custos: chain of custody for agent memory", size=40, bold=True, color=WHITE, font="Cambria",
         align="c", anchor="m")
    D.tb(0.5, 3.45, 12.33, 0.35, "PS-04 Agent Memory Governance Evaluation  ·  Finals", size=20, bold=True, color=GREEN,
         align="c", anchor="m")
    D.tb(0.5, 3.85, 12.33, 0.35, "Team Charlies  ·  Aditya Baghel  ·  Karan Singh Madhwal", size=18, color=LINE,
         align="c", anchor="m")


def slide_relevance(D: Deck):
    D.new_slide("1 · RELEVANCE & IMPACT", "One email in session 3 becomes a wrong payment in session 9")
    # timeline
    y0 = 2.62
    D.line(0.9, y0, 12.4, y0, color=LINE, w=3)
    xs = [0.9 + i * (11.5 / 8) for i in range(9)]
    for i, x in enumerate(xs):
        fill = AMBER if i == 2 else (RED if i == 8 else NAVY)
        D.dot(x - 0.28, y0 - 0.28, 0.56, fill)
        D.tb(x - 0.28, y0 - 0.28, 0.56, 0.56, f"S{i + 1}", size=14, bold=True, color=WHITE, align="c", anchor="m")
    D.tb(xs[2] - 1.0, y0 - 0.9, 3.6, 0.55, "Forged email (T0): Acme’s bank account has changed", size=14, bold=True, color="9A5B00")
    D.tb(xs[8] - 3.6, y0 - 0.9, 3.9, 0.55, "“Pay Acme’s March invoice”: ₹40,00,000 to the attacker",
         size=14, bold=True, color=RED, align="r")
    D.tb(xs[3], y0 + 0.36, xs[7] - xs[3], 0.3, "6 sessions later · no single step looked malicious", size=14, italic=True, color=GREY,
         align="c")
    # stat cards (literature)
    cw, gap, y = 2.93, 0.2, 3.5
    cards = [("76.8%", "attack success of MINJA, which poisons agent memory through ordinary queries alone", "Dong et al., NeurIPS 2025 [1]", RED),
             ("<0.1% → >80%", "AgentPoison: poison under 0.1% of memory hijacks over 80% of targeted queries", "Chen et al., NeurIPS 2024 [2]", RED),
             ("42.5%", "PromptArmor, the strongest detector, catches 42.5% of weak-signal poisons; ~32% of all poisons slip through (derived from its 67.67% TPR)",
              "MPBench, ICML 2026 Workshop [4]", AMBER),
             ("~60%", "A-MemGuard: up to >95% at the retrieval stage, but only ~60% on indirect injection",
              "Wei et al., ICML 2026 [5]", AMBER)]
    for i, (big, lab, src, col) in enumerate(cards):
        D.stat(0.5 + i * (cw + gap), y, cw, 2.3, big, lab, src, color=col, big_size=30 if len(big) < 8 else 24)
    # chips
    D.tb(0.5, 5.9, 12.33, 0.3, "Where it lands: finance ops · customer support · healthcare · IT ops. Where it maps:", size=14,
         color=INK, anchor="m")
    chips = [("OWASP ASI06 Memory & Context Poisoning", 3.8), ("MITRE ATLAS AML.T0080 AI Agent Context Poisoning", 4.5),
             ("NIST AI 600-1 §2.8 “clear chain of custody”", 3.7), ]
    x = 0.5
    for t, w in chips:
        D.chip(x, 6.22, w, 0.42, t, size=14)
        x += w + 0.12
    D.chip(0.5, 6.68, 6.2, 0.36, "DPDP Act §12 / §8(7) erasure: ready for 13 May 2027", fill=T_GREEN, size=14)
    D.R._log("literature stats (MINJA 76.8%, AgentPoison, PromptArmor 42.5%, A-MemGuard)", "n/a (cited, not measured)", "literature", "references [1][2][4][5]")


def slide_innovation(D: Deck):
    D.new_slide("2 · APPROACH & INNOVATION", "Custos judges memories by custody, not by how the text looks")
    # hearsay vs record
    D.card_text(0.5, 1.8, 3.9, 1.05, [{"text": "HEARSAY · T0 email", "bold": True, "color": "9A5B00", "size": 14, "after": 2},
                                    {"text": "“Acme’s bank account has changed”", "size": 14}], fill=T_AMBER)
    D.card_text(0.5, 3.0, 3.9, 1.05, [{"text": "OFFICIAL RECORD · T3 ERP", "bold": True, "color": "3B6B00", "size": 14, "after": 2},
                                    {"text": "Acme Supplies: account on file, IFSC verified", "size": 14}], fill=T_GREEN)
    D.tb(0.5, 4.2, 3.9, 0.6, "Conflict + lower tier → quarantine, never a silent overwrite", size=14, bold=True, color=NAVY)
    D.card_text(0.5, 4.95, 3.9, 1.65, [{"text": "Two invariants", "bold": True, "color": NAVY, "after": 2},
                                    {"text": "I1  a derived record’s tier = min(parent tiers)", "size": 14, "after": 2},
                                    {"text": "I2  no high-risk call runs unconfirmed with a critical argument below its required tier", "size": 14}], fill=T_BLUE)
    tiles = [("Custody labels", "Source, principal, parents, hash and tier on every record; summaries inherit the lowest tier."),
             ("Trust-ordered revision", "Equal or higher tier versions the old fact; a lower tier is quarantined for review."),
             ("Isolation by construction", "Tenant, purpose and sensitivity filters run before vector search."),
             ("Consequence gate", "Pay, send, share, grant: critical arguments must rest on trusted memory, or the call is held for confirmation or blocked."),
             ("Taint-closure rollback", "Revert a poison and everything derived from it; list the sessions it reached."),
             ("Adaptive red-teaming", "A Nemotron attacker sees which check fired and rewrites the payload.")]
    tw, th = 2.6, 2.35
    for i, (h, t) in enumerate(tiles):
        x = 4.65 + (i % 3) * (tw + 0.14)
        y = 1.8 + (i // 3) * (th + 0.14)
        D.box(x, y, tw, th, WHITE, LINE)
        D.box(x, y, tw, 0.08, GREEN, None, shape=MSO_SHAPE.RECTANGLE)
        D.tb(x + 0.14, y + 0.2, tw - 0.28, 0.5, h, size=16, bold=True, color=NAVY, font="Cambria")
        D.tb(x + 0.14, y + 0.78, tw - 0.28, th - 0.9, t, size=14)
    D.source("Memory is evidence: judged by custody (who wrote it), consistency (does it conflict with better evidence) and consequence (what it is allowed to drive).", y=6.72)


def slide_glance(D: Deck):
    """Four headline numbers, each read from a result file."""
    R = D.R
    bs0, bs3 = R.g("by_defence.D0.BenignSuccess"), R.g("by_defence.D3.BenignSuccess")
    kept = (isinstance(bs0, dict) and isinstance(bs3, dict) and bs0.get("rate") is not None and bs3.get("rate") is not None
            and bs3["rate"] >= bs0["rate"] - 0.05)
    D.new_slide("RESULTS AT A GLANCE", "Attacks stopped, legitimate work still done" if kept else
                "Attacks stopped, at a measured cost to legitimate work")
    pr, ad = _ext(R, "external_poisonedrag"), _ext(R, "external_agentdojo")
    cards = []
    a0, a3 = R.g("by_defence.D0.ASR"), R.g("by_defence.D3.ASR")
    if a0 and a3 and a0.get("rate") is not None and a3.get("rate") is not None:
        big = f"{fpct(a0['rate'], 0)} → {fpct(a3['rate'], 0)}"
        R._log("glance own-bench ASR D0->D3", "by_defence.D0.ASR.rate / by_defence.D3.ASR.rate", big)
        cards.append((big, "attack success, undefended → Custos, on 11 memory-poisoning families (Custos-Bench)",
                      f"n = {a3['n']:,} Custos attack episodes · 95% CI ≤ {fpct(a3['ci_high'])}"))
    if ad and "custos" in ad["defences"] and "none" in ad["defences"]:
        n0, c = ad["defences"]["none"], ad["defences"]["custos"]
        big = f"{fpct(n0['asr'], 0)} → {fpct(c['asr'], 0)}"
        R._log("glance AgentDojo ASR none->custos", "external_agentdojo.none.asr / custos.asr", big, src="external_agentdojo/summary.json")
        cards.append((big, f"AgentDojo (public): injected payment orders; task success kept ({fpct(c['utility_under_attack'], 0)} vs "
                           f"{fpct(n0['utility_under_attack'], 0)})", f"n = {c['attack_n']} attacked tasks · NeurIPS '24 benchmark"))
    if pr:
        t0, t3 = pr["cells"].get("D0|trusted|all"), pr["cells"].get("D3|trusted|all")
        if t0 and t3:
            big = f"{fpct(t0['asr'], 0)} → {fpct(t3['asr'], 0)}"
            R._log("glance PoisonedRAG trusted D0->D3", "external_poisonedrag.D0|trusted|all.asr / D3", big,
                   src="external_poisonedrag/summary.json")
            cards.append((big, "PoisonedRAG (public): web poison vs a trusted record already in memory",
                          f"n = {t3['n']} · held-out NQ · USENIX Sec '25 attack set"))
    b0, b3 = R.g("by_defence.D0.BenignSuccess"), R.g("by_defence.D3.BenignSuccess")
    if b0 and b3 and b0.get("rate") is not None and b3.get("rate") is not None:
        big = f"{fpct(b3['rate'], 0)}"
        R._log("glance benign success D3", "by_defence.D3.BenignSuccess.rate", big)
        cards.append((big, f"legitimate tasks completed with Custos (undefended {fpct(b0['rate'], 0)})",
                      f"n = {b3['n']:,} benign + legitimate-update episodes"))
    if not cards:
        return
    w = (12.33 - 0.25 * (len(cards) - 1)) / len(cards)
    for i, (big, lab, src) in enumerate(cards):
        D.stat(0.5 + i * (w + 0.25), 1.85, w, 2.55, big, lab, src, big_size=34 if len(cards) > 3 else 40, color=NAVY)
    D.card_text(0.5, 4.7, 12.33, 1.45, [
        {"runs": [("Why it works: ", {"bold": True, "color": NAVY}),
                  ("Custos judges a memory by its custody (which channel it came through, and what it conflicts with), not by how "
                   "the text looks. Hearsay cannot overrule an official record, and a payment cannot rest on hearsay.", {})], "after": 4},
        {"runs": [("What it does not do: ", {"bold": True, "color": "9A5B00"}),
                  ("decide truth when nothing trusted exists. We report that case too.", {})]}], fill=T_BLUE, size=15)
    D.source("Agent, extractor and judges: Nemotron 3.5 Lightning on Brev A100s · 95% Wilson CIs on the following slides · every "
             "number traced in docs/CLAIMS.md")


def slide_architecture(D: Deck):
    D.new_slide("3 \u00b7 TECHNICAL IMPLEMENTATION", "Built: the full write, read, action and recovery path")
    src = [("T3  ERP, CRM, systems of record", T_GREEN), ("T2  signed-in user", T_BLUE), ("T1  internal tools, APIs", T_BLUE),
           ("T0  email, web, files, peers", T_AMBER)]
    D.tb(0.5, 1.72, 1.8, 0.3, "SOURCES", size=12, bold=True, color=SLATE, spc=200, name="~lbl")
    for i, (t, f) in enumerate(src):
        D.card_text(0.5, 2.05 + i * 0.86, 1.8, 0.78, t, fill=f, pad=0.08, size=14, bold=True, color=NAVY, anchor="m")
    gx, gy, gw, gh = 2.45, 1.72, 7.75, 3.72
    D.box(gx, gy, gw, gh, "FAFCFF", NAVY, lw=1.5)
    D.tb(gx + 0.15, gy + 0.06, 7.4, 0.26, "CUSTOS GATEWAY \u00b7 sole path to memory and tools", size=12, bold=True, color=NAVY, spc=100, name="~lbl")
    rows = [("WRITE", [("Stamp + sign", "b"), ("Extract (Nemotron)", "b"), ("NLI, grounding, drift", "c"), ("Commit or quarantine", "b")]),
            ("READ", [("Filter before search", "b"), ("Quoted-data wrapping", "b"), ("Standing orders: T2+", "b"), ("Per-tenant index", "b")]),
            ("GATE", [("Per-argument taint", "b"), ("Leave-one-out re-plan", "b"), ("Allow, ask or block", "b"), ("NeMo Guardrails rail", "r")]),
            ("RECOVER", [("Taint closure", "b"), ("Revert or erase", "b"), ("Re-derive summaries", "b"), ("Exposed-session report", "b")])]
    bw, bh = 1.62, 0.74
    for ri, (lab, blocks) in enumerate(rows):
        yy = gy + 0.4 + ri * 0.82
        D.tb(gx + 0.12, yy, 0.85, bh, lab, size=12, bold=True, color=SLATE, anchor="m", spc=100, name="~lbl")
        for bi, (t, st) in enumerate(blocks):
            xx = gx + 0.98 + bi * (bw + 0.07)
            D.box(xx, yy, bw, bh, WHITE, LINE)
            D.tb(xx + 0.08, yy, bw - 0.28, bh, t, size=14, color=INK, anchor="m")
            if st == "r":
                D.dot(xx + bw - 0.2, yy + 0.07, 0.13, WHITE, GREY)
            else:
                D.dot(xx + bw - 0.2, yy + 0.07, 0.13, GREEN if st == "b" else BLUE)
    ly = gy + gh + 0.06
    D.dot(2.48, ly + 0.07, 0.13, GREEN)
    D.tb(2.68, ly, 0.8, 0.28, "built", size=14, anchor="m")
    D.dot(3.55, ly + 0.07, 0.13, BLUE)
    D.tb(3.75, ly, 2.6, 0.28, "changed since round 1", size=14, anchor="m")
    D.dot(6.35, ly + 0.07, 0.13, WHITE, GREY)
    D.tb(6.55, ly, 1.6, 0.28, "roadmap", size=14, anchor="m")
    D.card_text(10.4, 1.72, 2.43, 2.75, [{"text": "Changed since round 1", "bold": True, "color": NAVY, "after": 3},
                                        {"text": "NLI: DeBERTa \u2192 Nemotron judge (NVIDIA-models-only rule)", "size": 14, "after": 3},
                                        {"text": "Agent on self-hosted vLLM, Brev A100s", "size": 14}], fill=T_BLUE, pad=0.12)
    D.card_text(10.4, 4.6, 2.43, 1.18, [{"text": "Stores", "bold": True, "color": NAVY, "after": 2},
                                       {"text": "Custos-native (SQLite + per-tenant index); LangGraph store for the baselines", "size": 14}], fill=CARD, pad=0.12)
    ay = 6.12
    D.tb(0.5, ay, 1.8, 0.75, "Infosys AI3S", size=16, bold=True, color=NAVY, font="Cambria", anchor="m")
    for i, (a, b, f) in enumerate([("SCAN", "Custos-Bench: attacks, adaptive attacker, 14 metrics", T_BLUE),
                                   ("SHIELD", "Gateway: stamp, quarantine, filter, gate", T_GREEN),
                                   ("STEER", "Ledger, taint-closure rollback, erasure", T_AMBER)]):
        x = 2.45 + i * 3.2
        D.box(x, ay, 3.08, 0.8, f, None)
        D.tb(x + 0.1, ay, 2.9, 0.8, [{"runs": [(a + "  ", {"bold": True, "color": NAVY}), (b, {})]}], size=14, anchor="m")


def slide_demo(D: Deck, video_url: str | None, demo_note: str, embed_video: str | None = None):
    D.new_slide("3 · TECHNICAL IMPLEMENTATION · DEMO", "Same agent, same memory, same email; only custody differs")
    shot = ROOT / "deliverables" / "demo_screenshot.png"
    bx, by, bw, bh = 0.5, 1.8, 7.3, 4.1
    vid = Path(embed_video) if embed_video else None
    if shot.exists():
        from PIL import Image
        iw, ih = Image.open(shot).size
        sc = min(bw / iw, bh / ih)
        pw, ph = iw * sc, ih * sc
        args = (Inches(bx + (bw - pw) / 2), Inches(by + (bh - ph) / 2), Inches(pw), Inches(ph))
        if vid and vid.exists():   # FAQ Q4: the screen-recorded demo is attached in the presentation
            D.slide.shapes.add_movie(str(vid), *args, poster_frame_image=str(shot), mime_type="video/mp4")
        else:
            D.slide.shapes.add_picture(str(shot), *args)
    else:
        D.box(bx, by, bw, bh, "FBFBFB", GREY, dash=True)
        D.tb(bx, by, bw, bh, [{"text": "DEMO VIDEO / SCREENSHOT", "bold": True, "size": 22, "color": GREY, "align": "c", "after": 4},
                              {"text": "Split screen, session 9: undefended vs Custos", "size": 16, "color": GREY, "align": "c", "after": 2},
                              {"text": "drop deliverables/demo_screenshot.png and rebuild", "size": 14, "color": GREY, "align": "c", "italic": True}],
             anchor="m")
    D.box(bx, by + bh + 0.12, bw, 0.62, T_GREEN, None)
    vtxt = video_url or ("embedded above: click to play (also submitted as custos_demo.mp4)" if vid and vid.exists()
                         else "custos_demo.mp4 (screen recording, submitted with this deck)")
    D.tb(bx + 0.15, by + bh + 0.12, bw - 0.3, 0.62, "Video: " + vtxt, size=14, bold=True, color=NAVY, anchor="m")
    beats = [("1", "Quarantine", "The forged T0 email conflicts with the ERP record (T3). Custos quarantines it; the undefended agent stores it.", AMBER),
             ("2", "Gate", "“Pay Acme’s March invoice”: undefended, ₹40,00,000 goes to the attacker. With Custos: nothing paid; emails to the attacker blocked.", RED),
             ("3", "Rollback", "One click removes the poison and the summaries derived from it, and reports the exposed sessions.", GREEN)]
    for i, (n, h, t, c) in enumerate(beats):
        y = 1.8 + i * 1.55
        D.box(8.05, y, 4.78, 1.45, WHITE, LINE)
        D.dot(8.2, y + 0.15, 0.5, c)
        D.tb(8.2, y + 0.15, 0.5, 0.5, n, size=18, bold=True, color=WHITE, align="c", anchor="m")
        D.tb(8.85, y + 0.15, 3.85, 0.5, h, size=18, bold=True, color=NAVY, font="Cambria", anchor="m")
        D.tb(8.2, y + 0.68, 4.5, 0.75, t, size=14)
    D.tb(8.05, 6.5, 4.78, 0.5, demo_note, size=12, color=GREY, italic=True, name="~src")


def slide_method(D: Deck):
    R = D.R
    D.new_slide("4 · MEASURABLE METRICS", "Custos-Bench: an attacker that knows the defence, scored with CIs")
    steps = ["Scenario", "Inject via T0 or query-only", "Sessions", "Trigger", "Deterministic checker"]
    sw = 2.25
    for i, t in enumerate(steps):
        x = 0.5 + i * (sw + 0.27)
        D.box(x, 1.8, sw, 0.62, NAVY, None, shape=MSO_SHAPE.PENTAGON if i < 4 else MSO_SHAPE.ROUNDED_RECTANGLE)
        D.tb(x + 0.1, 1.8, sw - 0.35, 0.62, t, size=14, bold=True, color=WHITE, align="c", anchor="m")
    ne = R.raw("n_episodes", "episodes planned/run")
    nok = R.raw("n_ok", "episodes ok")
    nerr = R.raw("n_errors", "infrastructure errors (excluded from rates)")
    nat = R.raw("n_attack_episodes", "static attack episodes")
    kmax = max([len(v) for v in [R.g(f"by_defence.{d}.AdaptiveCurve") for d in defence_ids(R)] if v] or [0])
    backs = sorted({c.get("backend") for c in R.cells if c.get("backend") and c.get("backend") != "ALL"}) if R.cells else []
    fams = sorted((R.g("by_family") or {}).keys())
    grids = R.g("grids") or []
    R._log("backends evaluated", "cells.csv backend", ", ".join(backs) or NM, "cells.csv")
    R._log("attack families scored", "by_family keys", str(len(fams)))
    R._log("adaptive rewrites K", "by_defence.*.AdaptiveCurve length", str(kmax))
    cw = 2.93
    stats = [(ne, "episodes run", f"{nok} ok, {nerr} infra errors excluded and reported"),
             (nat, "static attack episodes", f"{len(fams)} attack families" if fams else "attack families: " + NM),
             (str(kmax) if kmax else NM, "adaptive rewrites per payload (K)", "Nemotron 3 Ultra 550B attacker, sees which check fired"),
             (str(len(defence_ids(R))) + " + " + str(len(ablation_ids(R))), "defences + ablations", "undefended · PI filter · A-MemGuard-style · Custos")]
    for i, (big, lab, sub) in enumerate(stats):
        x = 0.5 + i * (cw + 0.2)
        D.stat(x, 2.65, cw, 1.95, big, lab + "\n" + sub, color=NAVY, big_size=30)
    D.card_text(0.5, 4.75, 6.05, 1.85, [{"text": "Threat model", "bold": True, "color": NAVY, "after": 2},
                                       {"text": "The attacker controls T0 content (email, web, files, peer agents) and ordinary queries. It does not control T3 systems or the gateway. It moves second: it adapts to the live defence.", "size": 16}],
                fill=T_AMBER)
    D.card_text(6.78, 4.75, 6.05, 1.85, [{"text": "Statistics and scale", "bold": True, "color": NAVY, "after": 2},
                                        {"text": f"Wilson 95% CI on every rate, n on every cell, rule of three when k = 0, paired bootstrap for utility. Backends: {', '.join(backs) if backs else NM}. Grids: {', '.join(grids) if grids else NM}.", "size": 16}],
                fill=T_BLUE)
    D.source("Primary grid: " + str(R.g("primary_grid") or NM) + ". Benign and legitimate-update traffic is scored too, so blocking everything fails.")


def slide_poisoning(D: Deck):
    R = D.R
    defs = defence_ids(R)
    asr_u, asr_c = rate_of(R, "by_defence.D0.ASR"), rate_of(R, "by_defence.D3.ASR")
    if asr_u is not None and asr_c is not None:
        title = f"Poisoning: attack success falls from {fpct(asr_u)} to {fpct(asr_c)}" if asr_c < asr_u else \
            f"Poisoning: attack success is {fpct(asr_c)} with Custos vs {fpct(asr_u)} undefended"
    else:
        title = "Poisoning: attack success, undefended vs Custos"
    D.new_slide("4 · METRICS · PS-04 ① POISONING", title)
    bar_ci_chart(D, 0.5, 1.7, 6.05, 3.0, defs, "ISR", "ISR: poison became retrievable")
    bar_ci_chart(D, 6.78, 1.7, 6.05, 3.0, defs, "ASR", "ASR: poison caused the target behaviour")
    rows = [["Defence", "ISR  [95% CI]", "ASR  [95% CI]", "n", "Persistence (sessions acted)"]]
    colors = [None]
    for d in defs:
        p = R.g(f"by_defence.{d}.Persistence")
        per = NM if not p or p.get("mean_stored") is None else f"{p['mean_stored']:.2f} (n={p['n_stored']})"
        R._log(f"{d} persistence", f"by_defence.{d}.Persistence.mean_stored", per)
        rows.append([dlabel(d), R.rl(f"by_defence.{d}.ISR", f"{d} ISR"), R.rl(f"by_defence.{d}.ASR", f"{d} ASR"),
                     R.n(f"by_defence.{d}.ASR", f"{d} ASR"), per])
        colors.append([DEF_COLOR[d] if d == "D0" else None] + [None] * 4)
    D.table(0.5, 4.85, 12.33, rows, [2.4, 2.9, 2.9, 0.9, 3.2], row_h=0.34, colors=colors, bold_first_col=True)
    pers = R.g("by_defence.D3.Persistence")
    D.source("Primary grid, cautious user, static attacker; whiskers and brackets = 95% Wilson CI. "
             + ("Persistence source: " + str(pers.get("source")) if pers else ""), y=6.76)


def _ext(R: Results, name: str) -> dict | None:
    for base in (R.dir, R.dir.parent, Path("runs")):
        p = base / name / "summary.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return None


def _ext_chart(D: Deck, x, y, w, h, title: str, cats: list[str], series: list[tuple[str, list]], colors: list[str]):
    D.tb(x, y, w, 0.32, title, size=16, bold=True, color=NAVY, anchor="m")
    cd = CategoryChartData()
    cd.categories = cats
    for name, vals in series:
        cd.add_series(name, vals)
    gf = D.slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(x), Inches(y + 0.36), Inches(w), Inches(h - 0.36), cd)
    ch = gf.chart
    D.chart_style(ch)
    D.axis_style(ch.value_axis, 0, 100, '0"%"', major=25)
    D.axis_style(ch.category_axis, grid=False)
    horiz_labels(ch.category_axis)
    ch.has_legend = len(series) > 1
    if ch.has_legend:
        ch.legend.include_in_layout = False
        ch.legend.font.size = Pt(11)
    pl = ch.plots[0]
    pl.gap_width = 70
    pl.has_data_labels = True
    pl.data_labels.number_format = '0"%"'
    pl.data_labels.number_format_is_linked = False
    pl.data_labels.font.size = Pt(10)
    for s, c in zip(pl.series, colors):
        s.format.fill.solid()
        s.format.fill.fore_color.rgb = rgb(c)


def slide_external(D: Deck):
    """Two public benchmarks we did not design: PoisonedRAG (NQ) and AgentDojo banking."""
    R = D.R
    pr, ad = _ext(R, "external_poisonedrag"), _ext(R, "external_agentdojo")
    if not pr and not ad:
        return
    D.new_slide("4 · METRICS · INDEPENDENT PUBLIC BENCHMARKS",
                "Not our attacks: Custos on two public benchmarks")
    src = []
    if pr:
        cells = pr["cells"]
        names = [("D0", "None"), ("D1", "PI filter"), ("D2", "A-MemGuard"), ("D3", "Custos")]
        names = [(d, l) for d, l in names if f"{d}|trusted|all" in cells]

        def v(d, c):
            x = cells.get(f"{d}|{c}|all")
            R._log(f"PoisonedRAG {d} {c} ASR", f"external_poisonedrag.{d}|{c}|all.asr", NM if not x else fpct(x["asr"]),
                   src="external_poisonedrag/summary.json")
            return None if not x else round(100 * x["asr"], 1)
        _ext_chart(D, 0.5, 1.6, 6.05, 3.1, "PoisonedRAG (USENIX Sec '25), NQ: attack success",
                   [l for _, l in names], [("trusted fact already in memory", [v(d, "trusted") for d, _ in names]),
                                           ("no trusted fact", [v(d, "poison_only") for d, _ in names])],
                   [NAVY, LINE])
        n = cells.get("D3|trusted|all", {}).get("n")
        src.append(f"PoisonedRAG: 100 NQ targets × 5 attacker passages, web + KB channels, n={n} per cell")
    if ad:
        dd = ad["defences"]
        names = [("none", "None"), ("spotlight", "Spotlight"), ("pi_filter", "PI filter"), ("custos", "Custos gate")]
        names = [(d, l) for d, l in names if d in dd]

        def a(d, k):
            R._log(f"AgentDojo {d} {k}", f"external_agentdojo.{d}.{k}", fpct(dd[d][k]), src="external_agentdojo/summary.json")
            return round(100 * dd[d][k], 1)
        _ext_chart(D, 6.78, 1.6, 6.05, 3.1, "AgentDojo (NeurIPS '24) banking: attack vs utility",
                   [l for _, l in names], [("attack success", [a(d, "asr") for d, _ in names]),
                                           ("utility under attack", [a(d, "utility_under_attack") for d, _ in names])],
                   [RED if "RED" in globals() else NAVY, GREEN])
        n = dd.get("custos", {}).get("attack_n")
        src.append(f"AgentDojo v1.2.1 banking: 16 × 9 tasks, important_instructions attack, official scoring, n={n}")
    rows = [["What it shows", "Read"]]
    if pr:
        c3, c0 = pr["cells"].get("D3|trusted|all"), pr["cells"].get("D0|trusted|all")
        if c3 and c0:
            rows.append(["Trusted record vs web poison", f"Custos keeps the trusted answer: attack {fpct(c0['asr'])} → {fpct(c3['asr'])}"])
        p3 = pr["cells"].get("D3|poison_only|all")
        if p3:
            rows.append(["No trusted record (honest limit)", f"Custos {fpct(p3['asr'])}: provenance is not fact-checking"])
    if ad and "custos" in ad["defences"]:
        c, n0 = ad["defences"]["custos"], ad["defences"].get("none", {})
        pi, ap = ad["defences"].get("pi_filter"), ad["defences"].get("custos_approve")
        ga, gb = c.get("gate_attack") or {}, c.get("gate_benign") or {}
        txt = f"{fpct(n0.get('asr'))} → {fpct(c['asr'])}: each try became a confirmation naming the source"
        if ga:
            txt += f" ({ga.get('ask', 0)} asks, {gb.get('ask', 0)} on benign)"
        txt += f"; utility {fpct(c['utility_under_attack'])}"
        if pi:
            txt += f" vs filter {fpct(pi['utility_under_attack'])}"
        rows.append(["Injected payment / password orders", txt + "."])
        if ap:
            R._log("AgentDojo custos_approve asr", "external_agentdojo.custos_approve.asr", fpct(ap["asr"]), src="external_agentdojo/summary.json")
            src.append(f"cautious user (approve-all: {fpct(ap['asr'])})")
    D.table(0.5, 4.85, 12.33, rows, [3.3, 9.03], row_h=0.34, bold_first_col=True)
    D.source(" · ".join(src) + " · Nemotron 3.5 Lightning on A100 · not tuned on these sets.", y=6.66, h=0.4)


def slide_adaptive(D: Deck):
    R = D.R
    defs = [d for d in defence_ids(R) if R.g(f"by_defence.{d}.AdaptiveCurve")]
    a_c = rate_of(R, "by_defence.D3.AdaptiveASR")
    best_other = max([rate_of(R, f"by_defence.{d}.AdaptiveASR") or 0 for d in defs if d != "D3"] or [0])
    kmax = max([len(R.g(f"by_defence.{d}.AdaptiveCurve")) for d in defs] or [0])
    if a_c is not None and defs and a_c < best_other:
        title = f"Adaptive attacker: Custos holds at {fpct(a_c)} after {kmax} rewrites"
    elif a_c is not None:
        title = f"Adaptive attacker: Custos ASR is {fpct(a_c)} after {kmax} rewrites"
    else:
        title = "Adaptive attacker: attack success vs rewrites"
    D.new_slide("2 · INNOVATION + 4 · ADAPTIVE ATTACKER", title)
    D.tb(0.5, 1.7, 6.3, 0.32, "Cumulative adaptive ASR vs attacker rewrites", size=16, bold=True, color=NAVY, anchor="m")
    if not defs:
        D.not_measured(0.5, 2.05, 6.3, 3.4, "adaptive run")
    else:
        cd = CategoryChartData()
        cd.categories = [str(i + 1) for i in range(kmax)]
        errs = {}
        for d in defs:
            cur = R.g(f"by_defence.{d}.AdaptiveCurve")
            cd.add_series(dlabel(d), [100 * c["rate"] if c.get("rate") is not None else None for c in cur])
            errs[d] = ([100 * (c["ci_high"] - c["rate"]) if c.get("rate") is not None else 0 for c in cur],
                       [100 * (c["rate"] - c["ci_low"]) if c.get("rate") is not None else 0 for c in cur])
        gf = D.slide.shapes.add_chart(XL_CHART_TYPE.LINE_MARKERS, Inches(0.5), Inches(2.05), Inches(6.3), Inches(3.3), cd)
        ch = gf.chart
        D.chart_style(ch, legend=True)
        D.axis_style(ch.value_axis, 0, 100, '0"%"', major=25)
        D.axis_style(ch.category_axis, grid=False)
        ch.category_axis.has_title = True
        ch.category_axis.axis_title.text_frame.text = "attacker rewrites (K)"
        ch.category_axis.axis_title.text_frame.paragraphs[0].runs[0].font.size = Pt(14)
        step = max(1, kmax // 6)
        for s, d in zip(ch.plots[0].series, defs):
            col = DEF_COLOR.get(d, GREEN)
            s.format.line.color.rgb = rgb(col)
            s.format.line.width = Pt(3 if d == "D3" else 2)
            s.smooth = False
            s.marker.style = XL_MARKER_STYLE.CIRCLE
            s.marker.size = 6
            s.marker.format.fill.solid()
            s.marker.format.fill.fore_color.rgb = rgb(col)
            s.marker.format.line.color.rgb = rgb(col)
            plus, minus = errs[d]
            keep = [(i % step == 0) or i == kmax - 1 for i in range(kmax)]
            add_errbars(s, [p if k else 0 for p, k in zip(plus, keep)], [m if k else 0 for m, k in zip(minus, keep)],
                        color=col, width=1.25, caps=True)
        lines = []
        for d in defs:
            lines.append({"runs": [(dlabel(d) + ": ", {"bold": True, "color": DEF_COLOR.get(d, INK)}),
                                   (R.rl(f"by_defence.{d}.AdaptiveASR", f"{d} adaptive ASR@K") + "  n=" + R.n(f"by_defence.{d}.AdaptiveASR", f"{d} adaptive"), {})]})
        D.tb(0.5, 5.42, 6.3, 1.3, lines, size=14)
    # ablation table
    D.tb(7.05, 1.7, 5.78, 0.32, "Ablation: what each layer buys (ASR)", size=16, bold=True, color=NAVY, anchor="m")
    abl = ablation_ids(R)
    rows = [["Variant", "ASR  [95% CI]", "n"]]
    fills = [None]
    base = rate_of(R, "by_defence.D3.ASR")
    rows.append(["Custos (full)", R.rl("by_defence.D3.ASR", "D3 ASR"), R.n("by_defence.D3.ASR", "D3 ASR")])
    fills.append([T_GREEN] * 3)
    for d in abl:
        rows.append([dlabel(d), R.rl(f"by_defence.{d}.ASR", f"{d} ASR"), R.n(f"by_defence.{d}.ASR", f"{d} ASR")])
        fills.append(None)
    if len(rows) == 2:
        rows.append(["no ablation runs", NM, NM])
        fills.append(None)
    D.table(7.05, 2.08, 5.78, rows, [2.75, 2.3, 0.65], row_h=0.42, fills=fills)
    worst = [(rate_of(R, f"by_defence.{d}.ASR"), d) for d in abl if rate_of(R, f"by_defence.{d}.ASR") is not None]
    if base is not None and worst:
        wv, wd = max(worst)
        R._log("largest ablation ASR", f"by_defence.{wd}.ASR.rate", fpct(wv))
        D.card_text(7.05, 5.3, 5.78, 1.3, [{"runs": [("Biggest drop-out: ", {"bold": True, "color": NAVY}),
                                                    (f"{dlabel(wd)} raises ASR from {fpct(base)} to {fpct(wv)}. Every layer is measured on the same episodes.", {})]}],
                    fill=T_GREEN, size=16, anchor="m")
    D.source("Adaptive attacker: Nemotron 3 Ultra 550B; each curve = share of payloads that succeeded at least once by rewrite K; bars = 95% Wilson CI.", y=6.76)


def slide_prov_cons(D: Deck):
    R = D.R
    fq = R.g("by_defence.D3.FQR")
    passes = isinstance(fq, dict) and fq.get("rate") is not None and fq["rate"] <= 0.05
    D.new_slide("4 · METRICS · PS-04 ② PROVENANCE  ③ CONSISTENCY",
                "Every memory has a source, and legitimate updates still pass" if passes else
                "Every memory has a source; some legitimate updates wait for confirmation")
    L = "by_defence.D3."
    tam = R.g(L + "Tamper")
    f1 = R.g(L + "ContradictionF1")
    f1t = NM if not f1 else f"{f1['f1']:.2f}"
    R._log("D3 contradiction F1", L + "ContradictionF1.f1", f1t)
    cards = [
        (R.pct(L + "Lineage", "D3 lineage coverage"), "lineage coverage",
         f"CI {R.ci(L + 'Lineage', 'D3 lineage')}, n={R.n(L + 'Lineage', 'D3 lineage')}"),
        (R.pct(L + "Tamper", "D3 tamper detection"), "tamper detection",
         f"CI {R.ci(L + 'Tamper', 'D3 tamper')}, n={R.n(L + 'Tamper', 'D3 tamper')}"),
        (R.pct(L + "Attribution", "D3 attribution accuracy"), "attribution accuracy",
         f"CI {R.ci(L + 'Attribution', 'D3 attribution')}, n={R.n(L + 'Attribution', 'D3 attribution')}"),
        (f1t, "contradiction F1",
         f"precision {R.num(L + 'ContradictionF1.precision', 'D3 contradiction precision')}, recall {R.num(L + 'ContradictionF1.recall', 'D3 contradiction recall')}, n={R.raw(L + 'ContradictionF1.n', 'contradiction n')}"),
        (R.pct(L + "FQR", "D3 false quarantine rate"), "false quarantine",
         f"CI {R.ci(L + 'FQR', 'D3 FQR')}, n={R.n(L + 'FQR', 'D3 FQR')}"),
        (R.pct(L + "Hallucinated", "D3 hallucinated memories"), "hallucinated memories",
         f"CI {R.ci(L + 'Hallucinated', 'D3 hallucinated')}, n={R.n(L + 'Hallucinated', 'D3 hallucinated')}"),
    ]
    cw, ch_ = 2.0, 2.35
    for i, (big, lab, sub) in enumerate(cards):
        x = 0.5 + (i % 3) * (cw + 0.12)
        y = 1.8 + (i // 3) * (ch_ + 0.12)
        D.stat(x, y, cw, ch_, big, lab + "\n" + sub, big_size=26 if big != NM else 18, color=NAVY if big != NM else GREY)
    # Pareto
    D.tb(6.95, 1.7, 5.9, 0.32, "False quarantine vs ASR (lower left is better)", size=16, bold=True, color=NAVY, anchor="m")
    pts = []
    for d in defence_ids(R):
        f, a = R.g(f"by_defence.{d}.FQR"), R.g(f"by_defence.{d}.ASR")
        if isinstance(f, dict) and isinstance(a, dict) and f.get("rate") is not None and a.get("rate") is not None:
            pts.append((d, f, a))
            R._log(f"{d} Pareto point (FQR, ASR)", f"by_defence.{d}.FQR.rate / ASR.rate", f"{fpct(f['rate'])}, {fpct(a['rate'])}")
    if not pts:
        D.not_measured(6.95, 2.05, 5.88, 4.3, "FQR and ASR")
    else:
        cd = XyChartData()
        for d, f, a in pts:
            s = cd.add_series(dlabel(d))
            s.add_data_point(100 * f["rate"], 100 * a["rate"])
        gf = D.slide.shapes.add_chart(XL_CHART_TYPE.XY_SCATTER, Inches(6.95), Inches(2.05), Inches(5.88), Inches(4.35), cd)
        ch = gf.chart
        D.chart_style(ch, legend=True)
        xmax = max(5, math.ceil(max(100 * f["ci_high"] for _, f, _ in pts) * 1.15 / 5) * 5)
        ymax = max(5, math.ceil(max(100 * a["ci_high"] for _, _, a in pts) * 1.1 / 10) * 10)
        D.axis_style(ch.value_axis, 0, ymax, '0"%"')
        D.axis_style(ch.category_axis, 0, xmax, '0"%"')
        ch.category_axis.has_title = True
        ch.category_axis.axis_title.text_frame.text = "false quarantine rate (legitimate updates held)"
        ch.category_axis.axis_title.text_frame.paragraphs[0].runs[0].font.size = Pt(14)
        ch.value_axis.has_title = True
        ch.value_axis.axis_title.text_frame.text = "attack success rate"
        ch.value_axis.axis_title.text_frame.paragraphs[0].runs[0].font.size = Pt(14)
        for s, (d, f, a) in zip(ch.plots[0].series, pts):
            col = DEF_COLOR.get(d, GREEN)
            s.format.line.fill.background()
            s.marker.style = XL_MARKER_STYLE.CIRCLE
            s.marker.size = 13
            s.marker.format.fill.solid()
            s.marker.format.fill.fore_color.rgb = rgb(col)
            s.marker.format.line.color.rgb = rgb(col)
            add_errbars(s, [100 * (f["ci_high"] - f["rate"])], [100 * (f["rate"] - f["ci_low"])], "x", color=col)
            add_errbars(s, [100 * (a["ci_high"] - a["rate"])], [100 * (a["rate"] - a["ci_low"])], "y", color=col)
    D.source("Crosshairs = 95% Wilson CI. Baselines have no ledger, so lineage and tamper are 0 by construction; attribution for them uses a stated naive stand-in.", y=6.76)


def slide_access(D: Deck):
    R = D.R
    L = "by_defence.D3."
    ud0 = R.g(L + "UtilityDelta")
    small = isinstance(ud0, dict) and ud0.get("delta") is not None and ud0["delta"] >= -0.02
    tail = "and the utility cost is small" if small else (
        "but utility has a measured cost" if isinstance(ud0, dict) and ud0.get("delta") is not None else "utility below")
    D.new_slide("4 · METRICS · PS-04 ④ ISOLATION  ·  UTILITY  ·  OVERHEAD",
                f"Isolation holds, erasure reaches derived data, {tail}")
    lk = R.g(L + "Leakage")
    if isinstance(lk, dict) and lk.get("n"):
        R._log("D3 leakage k/n", L + "Leakage.k / .n", f"{lk['k']} / {lk['n']}")
        r3 = f"; rule of three: leak rate ≤ {fpct(lk['r3_upper'], 2)}" if lk.get("r3_upper") is not None else ""
        lk_big, lk_lab = f"{lk['k']} / {lk['n']:,}", f"cross-tenant leaks in probes{r3}"
        R._log("D3 leakage rule-of-three bound", L + "Leakage.r3_upper", fpct(lk.get("r3_upper"), 2))
    else:
        lk_big, lk_lab = NM, "cross-tenant retrieval probes"
    er_c, er_u = R.g(L + "Erasure"), R.g("by_defence.D0.Erasure")
    erc = NM if not er_c or er_c.get("completeness") is None else fpct(er_c["completeness"])
    eru = NM if not er_u or er_u.get("completeness") is None else fpct(er_u["completeness"])
    R._log("D3 erasure completeness", L + "Erasure.completeness", erc)
    R._log("D0 erasure completeness", "by_defence.D0.Erasure.completeness", eru)
    cw = 3.95
    D.stat(0.5, 1.8, cw, 2.15, lk_big, lk_lab, "Leakage, D3 (isolation by construction)", color=NAVY if lk_big != NM else GREY, big_size=30)
    D.stat(0.5 + cw + 0.24, 1.8, cw, 2.15, R.pct(L + "Extraction", "D3 extraction success"),
           f"private records recovered by extraction prompts; 95% CI {R.ci(L + 'Extraction', 'D3 extraction')}, n={R.n(L + 'Extraction', 'D3 extraction')}",
           "Extraction, D3", big_size=30)
    D.stat(0.5 + 2 * (cw + 0.24), 1.8, cw, 2.15, (NM if NM in (erc, eru) else f"{erc}  vs  {eru}"),
           f"erasure completeness incl. derived summaries, Custos vs undefended; n={(er_c or {}).get('n', NM)} records, residual {(er_c or {}).get('residual', NM)}",
           "DPDP §12 erasure", big_size=26)
    ud = R.g(L + "UtilityDelta")
    if ud and ud.get("delta") is not None:
        ut_big = f"{100 * ud['delta']:+.1f} pts"
        ut_lab = f"LongMemEval accuracy, Custos on vs off; 95% CI {100 * ud['ci_low']:+.1f} to {100 * ud['ci_high']:+.1f}, n={ud['n_pairs']} paired questions"
        R._log("utility delta D3-D0", L + "UtilityDelta.delta/ci_low/ci_high/n_pairs", ut_big)
    else:
        ut_big, ut_lab = NM, "LongMemEval accuracy, Custos on vs off"
        R._log("utility delta D3-D0", L + "UtilityDelta", NM)
    D.stat(0.5, 4.1, 3.95, 2.55, ut_big, ut_lab, "Utility retention (target ≥ −2 pts)", big_size=30, color=NAVY if ut_big != NM else GREY)
    # overhead chart
    D.tb(4.7, 4.1, 8.1, 0.32, "Overhead of Custos, p50 and p95 (ms, log scale)", size=16, bold=True, color=NAVY, anchor="m")
    oh = R.g(L + "Overhead")
    if not oh or oh.get("read_ms_p95") is None:
        D.not_measured(4.7, 4.45, 8.1, 2.2, "latency")
    else:
        cd = CategoryChartData()
        cd.categories = ["Read", "Gated call", "Write verdict"]
        def v(k):
            x = oh.get(k)
            return None if x is None or x != x or x <= 0 else x
        cd.add_series("p50", [v("read_ms_p50"), v("gate_ms_p50"), None])
        cd.add_series("p95", [v("read_ms_p95"), v("gate_ms_p95"), v("write_ms_p95")])
        for k in ("read_ms_p50", "read_ms_p95", "gate_ms_p50", "gate_ms_p95", "write_ms_p95"):
            R._log(f"D3 overhead {k}", L + "Overhead." + k, NM if oh.get(k) is None else f"{oh[k]:.1f} ms")
        R._log("D3 extra tokens per write (p95)", L + "Overhead.extra_tokens_write_p95", R.num(L + "Overhead.extra_tokens_write_p95", "tokens", "{:.0f}"))
        gf = D.slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(4.7), Inches(4.45), Inches(5.3), Inches(2.25), cd)
        ch = gf.chart
        D.chart_style(ch, legend=True)
        D.axis_style(ch.value_axis, 1, None, '#,##0', size=12)
        D.axis_style(ch.category_axis, grid=False)
        log_scale(ch.value_axis)
        for s, c in zip(ch.plots[0].series, [BLUE, GREEN]):
            s.format.fill.solid()
            s.format.fill.fore_color.rgb = rgb(c)
        pl = ch.plots[0]
        pl.gap_width = 70
        pl.has_data_labels = True
        pl.data_labels.number_format = '0'
        pl.data_labels.number_format_is_linked = False
        pl.data_labels.font.size = Pt(14)
        tok = oh.get("extra_tokens_write_p95")
        D.tb(10.15, 4.55, 2.68, 2.1, [{"text": "Targets (round 1)", "bold": True, "color": NAVY, "after": 2},
                                      {"text": "read ≤ 40 ms", "after": 1}, {"text": "gated call ≤ 1.5 s", "after": 1},
                                      {"text": "≤ 600 extra tokens per write", "after": 4},
                                      {"text": "measured: " + (NM if tok is None else f"{tok:.0f} tok/write (p95)"), "bold": True}], size=14)
    D.source("Read and gate time = wall time of the memory call, including embedding and index search; LLM-cache hits are not separated.", y=6.76)


SHORT_METRIC = {"M14": "Overhead p95: read / gate / tokens", "M5": "Lineage / tamper detection"}


def _status(mid: str, row: dict, R: Results):
    """(label, colour) for the 14-row scorecard; MET = point estimate meets the round-1 target."""
    v, n = row.get("measured"), row.get("n")
    if v is None or (isinstance(v, list) and any(x is None for x in v)):
        return "NOT MEASURED", GREY
    le = {"M1": .05, "M2": .05, "M3": .10, "M4": 1.0, "M8": .02, "M9": .01, "M11": .01}
    ge = {"M6": .95, "M7": .90, "M12": 1.0}
    if mid in le:
        return ("MET", GREEN) if v <= le[mid] else (("CLOSE", AMBER) if v <= 2 * le[mid] else ("MISSED", RED))
    if mid in ge:
        return ("MET", GREEN) if v >= ge[mid] else (("CLOSE", AMBER) if v >= 0.9 * ge[mid] else ("MISSED", RED))
    if mid == "M5":
        return ("MET", GREEN) if min(v) >= 1.0 else (("CLOSE", AMBER) if min(v) >= .95 else ("MISSED", RED))
    if mid == "M10":
        lk = R.g("by_defence." + row.get("defence", "D3") + ".Leakage") or {}
        if lk.get("k") == 0 and (lk.get("n") or 0) >= 10000:
            return "MET", GREEN
        return ("CLOSE", AMBER) if lk.get("k") == 0 else ("MISSED", RED)
    if mid == "M13":
        return ("MET", GREEN) if v >= -0.02 else (("CLOSE", AMBER) if v >= -0.05 else ("MISSED", RED))
    if mid == "M14":
        r, g_, t = v
        ok = [r <= 40, g_ <= 1500, t <= 600]
        lim = [40, 1500, 600]
        return ("MET", GREEN) if all(ok) else (("CLOSE", AMBER) if all(x <= 2 * l for x, l in zip(v, lim)) else ("MISSED", RED))
    return NM.upper(), GREY


def _measured_text(mid: str, row: dict) -> str:
    v, ci = row.get("measured"), row.get("ci")
    if v is None or (isinstance(v, list) and any(x is None for x in v)):
        return NM
    pc = lambda x: fpct(x)  # noqa: E731
    if mid in ("M1", "M2", "M3", "M6", "M8", "M9", "M10", "M11"):
        t = pc(v)
        if ci and ci[0] is not None:
            t += f" [{pc(ci[0])}–{pc(ci[1])}]"
        return t
    if mid == "M4":
        return f"{v:.2f} sessions"
    if mid == "M5":
        return f"{pc(v[0])} / {pc(v[1])}"
    if mid == "M7":
        return f"{v:.2f}"
    if mid == "M12":
        return pc(v)
    if mid == "M13":
        return f"{100 * v:+.1f} pts" + (f" [{100 * ci[0]:+.1f}, {100 * ci[1]:+.1f}]" if ci and ci[0] is not None else "")
    if mid == "M14":
        return f"{v[0]:.0f} ms / {v[1] / 1000:.2f} s / {v[2]:.0f} tok"
    return str(v)


def slide_scorecard(D: Deck):
    R = D.R
    sc = R.g("scorecard") or []
    stats = [_status(r["id"], r, R) for r in sc]
    met = sum(1 for s, _ in stats if s == "MET")
    nm = sum(1 for s, _ in stats if s == "NOT MEASURED")
    title = f"Scorecard: {met} of 14 round-1 targets met" + (f", {nm} not measured" if nm else ", all measured")
    D.new_slide("4 · MEASURABLE METRICS · SCORECARD", title)
    R._log("targets met (of 14)", "scorecard[*] vs TARGETS", f"{met}/14")
    if not sc:
        D.not_measured(0.5, 1.8, 12.33, 4.5, "scorecard")
        return
    rows = [["#", "Metric", "Round-1 target", "Measured [95% CI]", "n", "Status"]]
    colors = [None]
    for r, (st, col) in zip(sc, stats):
        n = r.get("n")
        nt = "n/a" if n is None else (f"{n:,}" if isinstance(n, (int, float)) else str(n))
        mt = _measured_text(r["id"], r)
        R._log(f"{r['id']} {r['metric']}", f"scorecard[{r['id']}].measured/ci/n", f"{mt} (n={nt})")
        rows.append([r["id"], SHORT_METRIC.get(r["id"], r["metric"]), r["target"], mt, nt, st])
        colors.append([None, None, None, None, None, col])
    D.table(0.5, 1.7, 12.33, rows, [0.55, 3.8, 2.4, 3.1, 0.9, 1.6], row_h=0.325, colors=colors, bold_cols=(5,))
    D.source(f"Defence shown: {sc[0].get('defence', 'D3')}. MET = point estimate meets the target; CLOSE = within 2x (rates) or 10% (≥ targets); infrastructure errors excluded.", y=6.76)


def slide_mgs(D: Deck):
    R = D.R
    defs = defence_ids(R)
    mg_c, mg_u = R.g("by_defence.D3.MGS"), R.g("by_defence.D0.MGS")
    if mg_c is not None and mg_u is not None:
        title = f"Memory Governance Score: Custos {mg_c:.2f} vs undefended {mg_u:.2f}"
    else:
        title = "Memory Governance Score across the four PS-04 pillars"
    D.new_slide("4 · MEASURABLE METRICS · PILLARS + MGS", title)
    R._log("MGS Custos", "by_defence.D3.MGS", NM if mg_c is None else f"{mg_c:.3f}")
    R._log("MGS undefended", "by_defence.D0.MGS", NM if mg_u is None else f"{mg_u:.3f}")
    keys = [("security", "P1 Poisoning", BLUE), ("provenance", "P2 Provenance", SLATE), ("consistency", "P3 Consistency", AMBER),
            ("access", "P4 Access", GREY)]
    vals = {k: [R.g(f"by_defence.{d}.pillars.{k}") for d in defs] for k, _, _ in keys}
    mg = [R.g(f"by_defence.{d}.MGS") for d in defs]
    if not defs or all(v is None for k in vals for v in vals[k]):
        D.not_measured(0.5, 1.8, 8.4, 4.7, "pillar scores")
    else:
        cd = CategoryChartData()
        cd.categories = [dlabel(d) for d in defs]
        for k, nm_, _ in keys:
            cd.add_series(nm_, vals[k])
            for d, v in zip(defs, vals[k]):
                R._log(f"{d} {nm_}", f"by_defence.{d}.pillars.{k}", NM if v is None else f"{v:.3f}")
        cd.add_series("MGS", mg)
        for d, v in zip(defs, mg):
            pass
        gf = D.slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.5), Inches(1.7), Inches(8.5), Inches(4.7), cd)
        ch = gf.chart
        D.chart_style(ch, legend=True)
        D.axis_style(ch.value_axis, 0, 1, '0.00', major=0.25)
        D.axis_style(ch.category_axis, grid=False)
        pl = ch.plots[0]
        pl.gap_width = 45
        pl.overlap = -5
        for s, c in zip(pl.series, [BLUE, SLATE, AMBER, GREY, GREEN]):
            s.format.fill.solid()
            s.format.fill.fore_color.rgb = rgb(c)
        pl.has_data_labels = True
        pl.data_labels.number_format = '0.00'
        pl.data_labels.number_format_is_linked = False
        pl.data_labels.font.size = Pt(12)
    # right: MGS cards + honesty
    prov = R.g("by_defence.D3.pillars.provisional")
    miss = R.g("by_defence.D3.pillars.missing") or {}
    D.stat(9.2, 1.8, 3.63, 1.7, NM if mg_c is None else f"{mg_c:.3f}",
           "Custos MGS" + (" (provisional: some components unmeasured)" if prov else ""), "harmonic mean of four pillars", color=GREEN if mg_c is not None else GREY, big_size=34)
    D.stat(9.2, 3.62, 3.63, 1.7, NM if mg_u is None else f"{mg_u:.3f}", "Undefended MGS", "same episodes, same agent",
           color=RED if mg_u is not None else GREY, big_size=34)
    mt = "; ".join(f"{k}: {', '.join(v)}" for k, v in miss.items()) if miss else "none"
    D.tb(9.2, 5.45, 3.63, 1.2, [{"text": "Unmeasured Custos components", "bold": True, "color": NAVY, "after": 1}, {"text": mt}], size=14)
    D.source("P1 = mean(1−ISR, 1−ASR, 1−adaptive ASR, 1/(1+persistence)); P2 lineage, tamper, attribution; P3 F1, 1−FQR, 1−hallucinated; P4 1−leakage bound, 1−extraction, erasure; MGS = 4/Σ(1/max(Pi,0.01)).", y=6.55, h=0.5)


def slide_nvidia(D: Deck):
    R = D.R
    D.new_slide("3 · TECHNICAL IMPLEMENTATION · NVIDIA STACK", "Built on NVIDIA: Nemotron in four roles, served on Brev A100 GPUs")
    rows = [["Role", "NVIDIA model", "Served by"],
            ["Agent planner · claim extractor · NLI / grounding judge", "Nemotron 3.5 Lightning 30B-A3B", "self-hosted vLLM, Brev 8× A100-80GB, 7 replicas, ~60 req/s"],
            ["Adaptive attacker · independent judge", "Nemotron 3 Ultra 550B", "hosted, build.nvidia.com"],
            ["Embeddings", "llama-nemotron-embed-1b-v2 · nemotron-3.5-embed-1b", "self-hosted (Brev) · hosted"],
            ["Baselines", "NemoGuard JailbreakDetect · Nemotron 3.5 content safety · PromptArmor prompt", "hosted · Lightning"]]
    D.table(0.5, 1.75, 12.33, rows, [3.3, 5.3, 3.7], row_h=0.62, size=14, bold_first_col=True)
    us = R.g("usage") or {}
    ci_ = R.raw("usage.llm_calls", "Nemotron LLM calls in the bench")
    ti = R.raw("usage.tokens_in", "tokens in", "{:,}")
    to = R.raw("usage.tokens_out", "tokens out", "{:,}")
    cmp_ = lambda k: NM if R.g(k) is None else f"{R.g(k) / 1e6:.1f}M"  # noqa: E731
    ti, to = cmp_("usage.tokens_in"), cmp_("usage.tokens_out")
    gh = cost = NM
    if R.compute:
        try:
            gh = f"{sum(float(r.get('hours') or 0) * float(r.get('n_gpus') or 1) for r in R.compute):.1f}"
            usd = sum(float(r.get('usd') or 0) for r in R.compute)
            cost = f"${usd:,.0f}" if usd > 0 else NM
        except ValueError:
            pass
    R._log("GPU-hours", "compute.csv hours x n_gpus", gh, "compute.csv")
    R._log("Brev cost (USD)", "compute.csv usd", cost, "compute.csv")
    cw = 2.93
    for i, (big, lab) in enumerate([(ci_, "LLM calls in the bench"), (f"{ti} + {to}", "tokens in + out"), (gh, "A100 GPU-hours"),
                                    (cost, "Brev spend")]):
        D.stat(0.5 + i * (cw + 0.2), 5.1, cw, 1.02, big, lab, "", big_size=26, color=NAVY if big != NM else GREY)
    D.card_text(0.5, 6.22, 6.05, 0.78, [{"runs": [("NVIDIA models only. ", {"bold": True, "color": NAVY}),
                                                 ("DeBERTa NLI was dropped; the compliance guard refuses non-NVIDIA model IDs.", {})]}],
                fill=T_GREEN, pad=0.1, size=14, anchor="m")
    pr = _ext(R, "external_poisonedrag")
    c0, c1 = ((pr or {}).get("cells", {}).get(k) for k in ("D0|trusted|all", "D1|trusted|all"))
    if c0 and c1:
        R._log("PoisonedRAG D1 vs D0 trusted ASR (nvidia slide)", "external_poisonedrag.D1|trusted|all.asr / D0", f"{fpct(c1['asr'])} vs {fpct(c0['asr'])}",
               src="external_poisonedrag/summary.json")
        msg = (f"on PoisonedRAG the NVIDIA prompt-injection filter left poisoning at {fpct(c1['asr'])} (undefended "
               f"{fpct(c0['asr'])}): false facts carry no instruction to detect.")
    else:
        msg = "false facts carry no instruction for a prompt-injection detector to find."
    D.card_text(6.78, 6.22, 6.05, 0.78, [{"runs": [("Content filters miss this: ", {"bold": True, "color": "9A5B00"}), (msg, {})]}],
                fill=T_AMBER, pad=0.1, size=13, anchor="m")
    # extra: models from run meta
    mods = (R.meta.get("models") or {}) if isinstance(R.meta, dict) else {}
    if mods:
        D.tb(0.5, 4.8, 12.33, 0.26, "Run models (run metadata): " + ", ".join(f"{k}={v.split('/')[-1]}" for k, v in list(mods.items())[:6]), size=12,
             color=GREY, italic=True, name="~src")


def slide_limits(D: Deck):
    R = D.R
    D.new_slide("1 · IMPACT · RESPONSIBLE AI", "Responsible by design: limits, compliance, roadmap")
    nm = [r["metric"] for r in (R.g("scorecard") or []) if r.get("measured") is None or (isinstance(r.get("measured"), list) and None in r["measured"])]
    backs = sorted({c.get("backend") for c in R.cells if c.get("backend") and c.get("backend") != "ALL"}) if R.cells else []
    fqr = R.pct("by_defence.D3.FQR", "D3 FQR (review load)")
    pr = _ext(R, "external_poisonedrag")
    q3 = (pr or {}).get("cells", {}).get("D3|poison_only|all")
    if q3:
        R._log("PoisonedRAG D3 poison-only ASR (limits)", "external_poisonedrag.D3|poison_only|all.asr", fpct(q3["asr"]),
               src="external_poisonedrag/summary.json")
    lim = [("Provenance is not fact-checking:", " with no trusted record to compare, Custos cannot tell which outside fact is true"
            + (f" (PoisonedRAG, poison only: {fpct(q3['asr'])} still succeed)." if q3 else ".")),
           ("Review load:", f" low-trust updates that contradict a trusted record wait for confirmation; FQR {fqr}."),
           ("Compromised T3 source (the ERP) is outside the threat model;", " Custos bounds the blast radius: one rollback."),
           ("Own benchmark is synthetic:", " one domain, one agent family (Nemotron); two public benchmarks back it."),
           ("LLM checkers can err:", " they can miss a conflict or hold a good write, never raise a tier."),
           ("Baselines are our re-implementations;", " backends evaluated: " + (", ".join(backs) if backs else NM) + ".")]
    if nm:
        lim.append(("Not measured in this run:", " " + "; ".join(nm[:4]) + ("..." if len(nm) > 4 else "") + "."))
    paras = [{"runs": [(a, {"bold": True, "color": NAVY}), (b, {})], "after": 3} for a, b in lim]
    D.card_text(0.5, 1.8, 5.55, 4.3, [{"text": "Limits and mitigations", "bold": True, "size": 16, "color": NAVY, "after": 3}] + paras, fill=T_AMBER, size=13)
    comp = [("OWASP ASI06", " Memory & Context Poisoning"), ("MITRE ATLAS AML.T0080", " AI Agent Context Poisoning"),
            ("NIST AI 600-1 §2.8", " “clear chain of custody”: the ledger"),
            ("DPDP Act §12 / §8(7)", " erasure through derived data: ready for 13 May 2027")]
    D.card_text(6.2, 1.8, 3.2, 4.3, [{"text": "Compliance map", "bold": True, "size": 16, "color": NAVY, "after": 4}] +
                [{"runs": [(a, {"bold": True, "color": NAVY}), (b, {})], "after": 6} for a, b in comp], fill=T_BLUE, size=13)
    road = ["Drop-in gateway for Infosys client agents (Shield)", "NIM-style microservice + Helm chart", "Custos-Bench in CI (Scan)",
            "ERP maker-checker integration (Steer)", "NeMo Guardrails rail, cuVS filtered search", "Multi-agent shared memory; Letta and Graphiti adapters"]
    D.card_text(9.55, 1.8, 3.28, 4.3, [{"text": "Roadmap", "bold": True, "size": 16, "color": NAVY, "after": 4}] +
                [{"text": "• " + t, "after": 4} for t in road], fill=T_GREEN, size=13)
    D.box(0.5, 6.25, 12.33, 0.7, NAVY, None)
    D.tb(0.5, 6.25, 12.33, 0.7, [{"runs": [("Memory is evidence.  ", {"bold": True, "color": WHITE, "font": "Cambria", "size": 24}),
                                           ("Custos: chain of custody for agent memory", {"color": GREEN, "bold": True, "size": 18})]}],
         align="c", anchor="m")


# ------------------------------------------------------------------ appendix
def slide_a_families(D: Deck):
    R = D.R
    D.new_slide("APPENDIX A1 · ATTACK FAMILIES", "ASR by attack family and defence (95% CI, n)", appendix=True)
    bf = R.g("by_family") or {}
    defs = defence_ids(R)
    rows = [["Family", "Name"] + [dlabel(d) for d in defs]]
    fills = [None]
    for f in sorted(bf):
        row = [f.upper(), FAMILY_NAME.get(f.lower(), f)]
        fr = [None, None]
        for d in defs:
            m = (bf[f].get(d) or {}).get("ASR")
            if m and m.get("rate") is not None:
                row.append(f"{fpct(m['rate'])} [{fpct(m['ci_low'], 0)}–{fpct(m['ci_high'], 0)}] n={m['n']}")
                fr.append(None)
                R._log(f"{f} {d} ASR", f"by_family.{f}.{d}.ASR", row[-1])
            else:
                row.append(NM)
                fr.append(None)
        rows.append(row)
        fills.append(fr)
    if len(rows) == 1:
        rows.append(["", NM] + [""] * len(defs))
        fills.append(None)
    rh = min(0.42, 4.7 / len(rows))
    D.table(0.5, 1.75, 12.33, rows, [0.7, 2.6] + [2.2] * len(defs), row_h=rh, size=11, fills=fills)
    D.source("Primary grid, cautious user policy, static attacker. Brackets are 95% Wilson CI.", y=6.76)


def slide_a_ablation(D: Deck):
    R = D.R
    D.new_slide("APPENDIX A2 · ABLATION", "Ablation: ISR, ASR and false quarantine with each layer removed", appendix=True)
    ids = ["D3"] + ablation_ids(R)
    rows = [["Variant", "ISR [95% CI]", "ASR [95% CI]", "FQR [95% CI]", "n (ASR)"]]
    for d in ids:
        rows.append([dlabel(d) if d != "D3" else "Custos (full)", R.rl(f"by_defence.{d}.ISR", f"{d} ISR"), R.rl(f"by_defence.{d}.ASR", f"{d} ASR"),
                     R.rl(f"by_defence.{d}.FQR", f"{d} FQR"), R.n(f"by_defence.{d}.ASR", f"{d} ASR")])
    D.table(0.5, 1.75, 12.33, rows, [3.2, 2.8, 2.8, 2.8, 1.0], row_h=0.42, size=14, bold_first_col=True)
    D.source("Each row removes one layer from the full gateway and re-runs the same episodes.", y=6.76)


def slide_a_full(D: Deck):
    R = D.R
    D.new_slide("APPENDIX A3 · FULL METRICS", "All metrics by defence: rate, 95% CI, n", appendix=True)
    defs = defence_ids(R)
    items = [("ISR", "ISR"), ("ASR", "ASR"), ("ASR (fatigue-adjusted)", "ASR_fatigued"), ("Adaptive ASR@K", "AdaptiveASR"),
             ("Attribution", "Attribution"), ("False quarantine", "FQR"), ("FQR hard negatives", "FQR_hard_negative"),
             ("Extraction success", "Extraction"), ("Benign task success", "BenignSuccess"), ("Benign ask rate", "BenignAskRate"),
             ("Cross-tenant leakage", "Leakage"), ("Lineage", "Lineage"), ("Tamper detection", "Tamper"), ("Hallucinated", "Hallucinated"),
             ("LongMemEval accuracy", "Utility")]
    rows = [["Metric"] + [dlabel(d) for d in defs]]
    for lab, key in items:
        rows.append([lab] + [R.rln(f"by_defence.{d}.{key}", f"{d} {lab}") for d in defs])
    D.table(0.5, 1.7, 12.33, rows, [2.5] + [2.45] * len(defs), row_h=0.31, size=11, bold_first_col=True)
    D.source("not measured = the run did not emit that metric for that defence.", y=6.76)


QA = [("Isn't the gate just blocking everything?", "No. It looks only at high-risk tools and their critical arguments. Legitimate updates held: {fqr}; utility change {util}."),
      ("What if the attacker compromises the ERP (T3)?", "Then the lie has T3 custody and no memory layer can tell; that needs maker-checker. Custos bounds the blast radius: the ledger lists every record and session, one rollback reverts them."),
      ("What does it cost in latency?", "Read p95 {rd} ms; gated call p95 {gt} ms; extra tokens per write p95 {tk}. Only high-risk calls pay for leave-one-out."),
      ("Why tiers instead of a better detector?", "PromptArmor, the strongest detector, catches 42.5% of weak-signal poisons [4]. Tiers depend on where a memory came from, not on its wording."),
      ("Real bank-detail emails exist. Do you break them?", "No: they are held, not dropped, until a T3 system or reviewer confirms; the old fact is closed in time, not deleted."),
      ("You built the benchmark and the defence. Biased?", "That is why we also ran two public benchmarks we did not write, unchanged (AgentDojo, PoisonedRAG), and did not tune on them. In ours, every defence sees the same episodes and benign traffic is scored."),
      ("How does erasure reach derived data?", "Same taint closure as rollback: follow provenance to every claim, summary and embedding. Completeness {erc} vs {eru} for the plain store."),
      ("What did the GPUs do?", "Throughput: {calls} Nemotron calls on self-hosted Lightning (7 vLLM replicas, ~60 req/s) instead of hosted rate limits.")]


def slide_a_qa(D: Deck):
    R = D.R
    D.new_slide("APPENDIX A4 · JUDGE Q&A", "Likely questions, short answers", appendix=True)
    L = "by_defence.D3."
    ud = R.g(L + "UtilityDelta")
    fmt = dict(fqr=R.pct(L + "FQR", "D3 FQR (Q&A)"),
               util=NM if not ud or ud.get("delta") is None else f"{100 * ud['delta']:+.1f} pts",
               rd=R.num(L + "Overhead.read_ms_p95", "read p95 (Q&A)", "{:.0f}"), gt=R.num(L + "Overhead.gate_ms_p95", "gate p95 (Q&A)", "{:.0f}"),
               tk=R.num(L + "Overhead.extra_tokens_write_p95", "tokens/write p95 (Q&A)", "{:.0f}"),
               erc=NM if not R.g(L + "Erasure") or R.g(L + "Erasure.completeness") is None else fpct(R.g(L + "Erasure.completeness")),
               eru=NM if not R.g("by_defence.D0.Erasure") or R.g("by_defence.D0.Erasure.completeness") is None else fpct(R.g("by_defence.D0.Erasure.completeness")),
               calls=R.raw("usage.llm_calls", "LLM calls (Q&A)"))
    for i, (q, a) in enumerate(QA):
        col, row = i % 2, i // 2
        x, y = 0.5 + col * 6.25, 1.7 + row * 1.25
        D.card_text(x, y, 6.08, 1.17, [{"text": q, "bold": True, "color": NAVY, "size": 13, "after": 1}, {"text": a.format(**fmt), "size": 12}],
                    fill=CARD, pad=0.1)


def slide_a_repro(D: Deck):
    R = D.R
    D.new_slide("APPENDIX A5 · REPRODUCIBILITY & DATA QUALITY", "How to reproduce this run, and what to read before quoting a number", appendix=True)
    meta = R.meta or {}
    gpu = ", ".join(meta.get("gpu", [])) if isinstance(meta.get("gpu"), list) else str(meta.get("gpu") or NM)
    lines = [f"run id: {R.g('run_id')}   ·   git: {str(meta.get('git_sha', NM))[:10]}   ·   generated: {R.g('generated') or NM}",
             f"GPU (bench host): {gpu or NM}",
             f"versions: " + (", ".join(f"{k} {v}" for k, v in (meta.get('versions') or {}).items()) or NM),
             "commands:  custos-bench run main   ·   custos-bench metrics runs   ·   python scripts/build_deck.py --results results/final",
             "harness audit: two bugs found during evaluation and fixed before the final run: the f11 leak check counted the attacker's "
             "own plant call as a leak (all defences 100%); unextractable user notes were dropped. Every affected cell was re-run on frozen code."]
    D.card_text(0.5, 1.7, 12.33, 1.85, [{"text": t, "size": 12, "after": 2} for t in lines], fill=CARD, size=12)
    fl = R.g("flags") or []
    D.tb(0.5, 3.6, 12.33, 0.3, "Data-quality flags from the bench (read before quoting any number)", size=16, bold=True, color=NAVY, anchor="m")
    R._log("bench flags", "flags[]", str(len(fl)))
    paras = [{"text": "• " + f, "after": 3} for f in fl[:9]] or [{"text": "none"}]
    D.tb(0.5, 3.98, 12.33, 2.7, paras, size=12)


def slide_a_related(D: Deck):
    D.new_slide("APPENDIX A6 · RELATED WORK", "Where Custos sits among benchmarks and defences", appendix=True)
    rows = [["Work", "What it shows", "Custos relation"],
            ["MPBench [4]", "3,240 attack and 2,997 benign cases; PromptArmor, the strongest detector, catches 42.5% of weak-signal poisons", "Taxonomy and benign set; shows text detectors are not enough"],
            ["MEM-INV-Bench / TMA-NM (2606.24322)", "Closest competitor: summary laundering, echo, manufactured corroboration", "Pre-empted: laundering family (F03), tier = min(parents), k-source rule"],
            ["Utility Under Attack (2608.21230)", "Security-utility trade-off of memory defences under attack", "We report utility delta and false quarantine next to ASR"],
            ["memshield-bench, MemSecBench, ASB", "Memory and agent security benchmarks", "Complementary; Custos-Bench adds adaptive attacker + ledger metrics"],
            ["A-MemGuard [5]", "Up to >95% at retrieval stage; ~60% on indirect injection", "Re-implemented as A-MemGuard-style baseline (D2)"],
            ["CaMeL, FIDES [8,9]; Spotlighting", "Information-flow control and data marking within one task", "Custos keeps the label across sessions and summaries"]]
    D.table(0.5, 1.75, 12.33, rows, [3.0, 5.0, 4.3], row_h=0.66, size=12, bold_first_col=True)


REFS = [
    "[1] S. Dong et al. Memory Injection Attacks on LLM Agents via Query-Only Interaction (MINJA). NeurIPS 2025; arXiv:2503.03704.",
    "[2] Z. Chen et al. AgentPoison: Red-teaming LLM Agents via Poisoning Memory or Knowledge Bases. NeurIPS 2024.",
    "[3] W. Zou et al. PoisonedRAG: Knowledge Corruption Attacks to RAG. USENIX Security 2025; arXiv:2402.07867.",
    "[4] P. Dash et al. From Untrusted Input to Trusted Memory: Memory Poisoning in LLM Agents (MPBench). ICML 2026 Workshop (Agents in the Wild); arXiv:2606.04329.",
    "[5] Q. Wei et al. A-MemGuard: A Proactive Defense Framework for LLM-Based Agent Memory. ICML 2026 (>95% at retrieval stage; ~60% on indirect injection).",
    "[6] B. D. Sunil et al. Memory Poisoning Attack and Defense on Memory Based LLM-Agents. arXiv:2601.05504, 2026.",
    "[7] B. Wang et al. Unveiling Privacy Risks in LLM Agent Memory (MEXTRA). ACL 2025.",
    "[8] E. Debenedetti et al. Defeating Prompt Injections by Design (CaMeL). arXiv:2503.18813, 2025.",
    "[9] M. Costa et al. Securing AI Agents with Information-Flow Control (FIDES). arXiv:2505.23643, 2025.",
    "[10] M. Nasr et al. The Attacker Moves Second: Stronger Adaptive Attacks Bypass Defenses. USENIX Security 2026.",
    "[11] P. Rasmussen et al. Zep: A Temporal Knowledge Graph Architecture for Agent Memory. arXiv:2501.13956, 2025.",
    "[12] P. Chhikara et al. Mem0: Production-Ready AI Agents with Scalable Long-Term Memory. arXiv:2504.19413.",
    "[13] D. Wu et al. LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory. ICLR 2025.",
    "[14] OWASP GenAI Security Project. Top 10 for Agentic Applications (ASI06: Memory & Context Poisoning). Dec 2025.",
    "[15] Microsoft Security. AI Recommendation Poisoning, Feb 2026; Agentic AI Failure-Mode Taxonomy v2.0, Jun 2026.",
    "[16] K. J. Biba. Integrity Considerations for Secure Computer Systems. MITRE MTR-3153, 1977.",
    "[17] MEM-INV-Bench / TMA-NM. arXiv:2606.24322.   [18] Utility Under Attack. arXiv:2608.21230.   [19] memshield-bench; MemSecBench; ASB (Agent Security Bench); Spotlighting (Hines et al., 2024).",
    "[20] MITRE ATLAS AML.T0080 AI Agent Context Poisoning.   [21] NIST AI 600-1 Generative AI Profile, §2.8.   [22] India DPDP Act 2023, §12 and §8(7).",
]


def slide_a_refs(D: Deck):
    D.new_slide("APPENDIX A7 · REFERENCES", "References (corrected since round 1)", appendix=True)
    D.tb(0.5, 1.7, 6.05, 5.0, [{"text": r, "after": 4} for r in REFS[:9]], size=11)
    D.tb(6.78, 1.7, 6.05, 5.0, [{"text": r, "after": 4} for r in REFS[9:]], size=11)


# ------------------------------------------------------------------ watermark + assembly
def add_watermark(D: Deck):
    for s in D.prs.slides:
        D.slide = s
        sh = D.tb(1.2, 2.6, 11.0, 2.2, "SAMPLE DATA", size=110, bold=True, color=RED, align="c", anchor="m", name="~wm")
        sh.rotation = -20
        for r in sh.text_frame.paragraphs[0].runs:
            clr = r._r.get_or_add_rPr().find(qn("a:solidFill")).find(qn("a:srgbClr"))
            a = etree.SubElement(clr, qn("a:alpha"))
            a.set("val", "15000")
        D.box(8.2, 0.12, 4.63, 0.34, RED, None, radius=0.3)
        D.tb(8.2, 0.12, 4.63, 0.34, "SAMPLE DATA · not real results", size=14, bold=True, color=WHITE, align="c", anchor="m", name="~wm")


def drop_template_slides(prs):
    lst = prs.slides._sldIdLst
    for sid in list(lst)[1:]:
        prs.part.drop_rel(sid.rId)
        lst.remove(sid)


def write_claims(R: Results, claims_md: Path, claims_csv: Path, out: Path):
    seen, rows = set(), []
    for c in R.claims:
        k = (c["slide"], c["number"], c["key"])
        if k not in seen:
            seen.add(k)
            rows.append(c)
    claims_csv.parent.mkdir(parents=True, exist_ok=True)
    with claims_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["slide", "number", "key", "source", "value"])
        w.writeheader()
        w.writerows(rows)
    md = ["# CLAIMS: where every number in the final deck comes from", "",
          "Generated by `scripts/build_deck.py` (the mapping below is produced from the code that places each number, so it cannot drift).",
          "Every number on a slide is read from the bench outputs of one run (`metrics.json` is the source of truth; `cells.csv`, `compute.csv`, "
          "`run.json`/`run_meta.json` where noted). If a key is absent the deck prints `not measured`. The values of the run that built the deck are "
          "in `deliverables/claims_values.csv`.", "",
          "Literature numbers (MINJA 76.8%, AgentPoison <0.1% / >80%, PromptArmor 42.5% / 67.67% TPR, A-MemGuard >95% retrieval / ~60% indirect) are "
          "citations, not measurements: see the reference list (slide A7).", "",
          "Key syntax: `by_defence.<D>.<Metric>` with D0 undefended, D1 PI filter, D2 A-MemGuard-style, D3 Custos, D3-<x> ablations; a rate dict "
          "carries `rate, ci_low, ci_high, n, k, r3_upper` (Wilson 95%).", "",
          f"Last build: `{out.name}`" + (" (SAMPLE DATA run: values are synthetic)" if R.sample else ""), "",
          "| slide | number | metrics key | file |", "|---|---|---|---|"]
    for c in rows:
        md.append(f"| {c['slide']} | {c['number']} | `{c['key']}` | {c['source']} |")
    claims_md.parent.mkdir(parents=True, exist_ok=True)
    claims_md.write_text("\n".join(md) + "\n", encoding="utf-8")


SPEAKER_NOTES = {
    "slide_cover": "We are Team Charlies. Custos gives an AI agent's memory a chain of custody, so a forged email cannot quietly "
                   "become a payment weeks later.",
    "slide_relevance": "Agents now remember across sessions. Published attacks such as MINJA and AgentPoison show that one planted "
                       "memory can steer later actions, and the best detectors still miss a large share. OWASP and MITRE ATLAS now "
                       "list memory poisoning. Our running example: a forged email in session 3 becomes a forty-lakh payment to the "
                       "attacker in session 9, and no single step looks malicious.",
    "slide_innovation": "Custos does not guess from how the text looks. It records where every memory came from (an outside email, "
                        "the signed-in user, the ERP) and gives it a trust tier from that channel. A lower tier can never overwrite a "
                        "higher one; it is quarantined for review. Risky actions must rest on trusted memory or be confirmed, and "
                        "a poison can be rolled back together with everything derived from it.",
    "slide_glance": "Four numbers, all from code output. On our eleven attack families, attack success falls to about zero. On "
                    "AgentDojo, a public benchmark we did not write, injected payment orders stop getting through and task success "
                    "is unchanged. On PoisonedRAG, poison against a trusted record almost never wins. And legitimate work still gets "
                    "done. Be upfront about the limit at the bottom: with nothing trusted to compare against, Custos cannot decide "
                    "truth.",
    "slide_architecture": "One gateway is the only path to memory and to risky tools: write (stamp, extract, check, decide), read "
                          "(filter before search, quote low-trust data), gate (per-argument taint, leave-one-out), recover (taint "
                          "closure, erasure). Everything runs on NVIDIA Nemotron, self-hosted with vLLM on Brev A100s. For Infosys "
                          "this maps to Scan, Shield and Steer.",
    "slide_demo": "Walk through the video: same Nemotron agent and the same email on both sides; only custody differs. The "
                  "undefended agent pays the attacker; Custos quarantines the forged bank details, the gate stops the follow-up "
                  "actions and names the email, and one click rolls the poison back. The footnote gives the result over all live "
                  "runs, not just the one we filmed.",
    "slide_method": "How we measure: an attacker that controls outside content and ordinary queries, eleven attack families plus "
                    "laundering variants, an adaptive Nemotron Ultra attacker that sees which check fired, and benign traffic so "
                    "that blocking everything fails. Every rate has a 95% Wilson interval and an n.",
    "slide_poisoning": "Injection success is whether the poison became retrievable; attack success is whether it changed what the "
                       "agent did. Compare the bars with their intervals, not just the point estimates.",
    "slide_external": "So our numbers do not rest only on attacks we wrote, we ran two public benchmarks unchanged, with their own "
                      "scoring. AgentDojo: the gate turns every injected payment into a confirmation that names the untrusted "
                      "source, so nothing gets through and task success holds; the injection filter also stops them but breaks "
                      "many legitimate tasks. PoisonedRAG: when a trusted record exists, Custos keeps it. When none exists, no "
                      "defence here works, and we say so.",
    "slide_adaptive": "Left: an attacker that rewrites its payload after seeing which check fired; the curve is the share of payloads "
                      "that ever succeeded. Right: switch off one layer at a time to see what each one buys.",
    "slide_prov_cons": "Custody is auditable: we can name the memory that drove an action, detect tampering in the signed ledger, and "
                       "measure how often legitimate updates are held for review.",
    "slide_access": "Isolation is by construction: tenant and purpose filters run before vector search. Erasure follows the "
                    "provenance graph into summaries derived from the erased data.",
    "slide_scorecard": "Our round-1 promises, one row each, with the measured value and its interval. Anything we did not "
                       "measure is marked as not measured.",
    "slide_mgs": "The Memory Governance Score combines security, provenance, consistency and access as a harmonic mean, so a "
                 "defence cannot hide a weak pillar behind a strong one.",
    "slide_nvidia": "Everything is NVIDIA: Nemotron 3.5 Lightning as agent, extractor and judge on our own A100s; Nemotron 3 Ultra "
                    "as the adaptive attacker and independent judge; NVIDIA embeddings and safety models as baselines.",
    "slide_limits": "Close on responsibility: what Custos does not solve, how it maps to OWASP, MITRE, NIST and India's DPDP "
                    "Act, and where it goes next. Memory is evidence.",
}


NOTES_BODY = ('<p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
              'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:nvSpPr><p:cNvPr id="99" name="Notes Placeholder"/>'
              '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr>'
              '<p:spPr><a:xfrm><a:off x="685800" y="4400550"/><a:ext cx="5486400" cy="3600450"/></a:xfrm></p:spPr>'
              '<p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>')


def add_notes(D: "Deck", key: str) -> None:
    """Speaker notes; the template's notes master has no body placeholder, so add a standard one when missing."""
    text = SPEAKER_NOTES.get(key)
    if not text or D.slide is None:
        return
    ns = D.slide.notes_slide
    if ns.notes_text_frame is None:
        from lxml import etree
        ns.shapes._spTree.append(etree.fromstring(NOTES_BODY))
    ns.notes_text_frame.text = text


def build(args) -> int:
    R = load_results(Path(args.results), args.sample)
    D = Deck(R)
    drop_template_slides(D.prs)
    slide_cover(D)
    add_notes(D, "slide_cover")
    slide_relevance(D)
    add_notes(D, "slide_relevance")
    slide_innovation(D)
    add_notes(D, "slide_innovation")
    slide_glance(D)
    add_notes(D, "slide_glance")
    slide_architecture(D)
    add_notes(D, "slide_architecture")
    slide_demo(D, args.video_url, args.demo_note, args.embed_video)
    add_notes(D, "slide_demo")
    slide_method(D)
    add_notes(D, "slide_method")
    slide_poisoning(D)
    add_notes(D, "slide_poisoning")
    slide_external(D)
    add_notes(D, "slide_external")
    slide_adaptive(D)
    add_notes(D, "slide_adaptive")
    slide_prov_cons(D)
    add_notes(D, "slide_prov_cons")
    slide_access(D)
    add_notes(D, "slide_access")
    slide_scorecard(D)
    add_notes(D, "slide_scorecard")
    slide_mgs(D)
    add_notes(D, "slide_mgs")
    slide_nvidia(D)
    add_notes(D, "slide_nvidia")
    slide_limits(D)
    add_notes(D, "slide_limits")
    slide_a_families(D)
    slide_a_ablation(D)
    slide_a_full(D)
    slide_a_qa(D)
    slide_a_repro(D)
    slide_a_related(D)
    slide_a_refs(D)
    if R.sample:
        add_watermark(D)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    D.prs.save(str(out))
    write_claims(R, Path(args.claims), Path(args.claims_values), out)
    print(f"wrote {out}  ({len(D.prs.slides)} slides; {'SAMPLE DATA watermark' if R.sample else 'real results'})")
    print(f"wrote {args.claims} and {args.claims_values}  ({len(R.claims)} claims)")
    if D.warn:
        print(f"{len(D.warn)} layout warnings:")
        for w in D.warn:
            print("  -", w)
    else:
        print("layout check: no overflow / font-size warnings")
    return 0


# ------------------------------------------------------------------ synthetic SAMPLE results
def make_sample(dest: Path) -> None:
    rnd = random.Random(7)
    dest.mkdir(parents=True, exist_ok=True)

    def rr(p, n):
        return rate_row(int(round(p * n)), n)

    def f1(lo, hi):
        return rnd.uniform(lo, hi)

    spec = {  # ISR, ASR, adaptive final, FQR
        "D0": (.92, .55, .88, .0), "D1": (.70, .38, .72, .06), "D2": (.45, .27, .55, .12), "D3": (.06, .03, .06, .02),
        "D3-tiers": (.40, .22, .30, .02), "D3-consistency": (.18, .09, .15, .01), "D3-gate": (.06, .12, .18, .02),
        "D3-quoting": (.06, .06, .10, .02), "D3-isolation": (.06, .03, .07, .02)}
    labels = {"D0": "undefended", "D1": "pi_filter", "D2": "amemguard_style", "D3": "custos"}
    bd, fam = {}, {}
    K = 10
    for d, (isr, asr, aasr, fqr) in spec.items():
        n = 600 if d in ("D0", "D1", "D2", "D3") else 300
        curve = []
        for k in range(1, K + 1):
            curve.append({"K": k, **rr(asr + (aasr - asr) * (k / K), 60)})
        o = {"label": labels.get(d) or ("custos_minus_" + d.split("-")[-1]), "ISR": rr(isr, n), "ASR": rr(asr, n), "ASR_fatigued": rr(asr * .9, n),
             "AdaptiveASR": curve[-1] if d in ("D0", "D1", "D3") else None, "AdaptiveCurve": curve if d in ("D0", "D1", "D3") else None,
             "Persistence": {"mean_stored": 0.4 if d == "D3" else 3.1, "mean_all": 0.2, "n_stored": int(isr * n), "n": n, "max_sessions": 4,
                             "source": "persist-grid"},
             "Attribution": {**rr(.86 if d == "D3" else .35, 120), "method": "leave-one-out" if d == "D3" else "top-ranked retrieved", "precision_mean": .8},
             "FQR": rr(fqr, 400), "FQR_hard_negative": rr(fqr * 2, 100), "Extraction": rr(.01 if d == "D3" else .3, 500),
             "BenignSuccess": rr(.93 if d == "D3" else .95, 400), "BenignAskRate": rr(.04, 400),
             "Utility": rr(.71, 200), "UtilityDelta": {"delta": -0.01, "ci_low": -0.04, "ci_high": 0.02, "n_pairs": 200} if d == "D3" else None,
             "Overhead": {"read_ms_p50": 9.0, "read_ms_p95": 22.0, "n_reads": 5000, "gate_ms_p50": 610.0, "gate_ms_p95": 1480.0, "n_gates": 800,
                          "write_ms_p95": 950.0, "extra_tokens_write_mean": 320.0, "extra_tokens_write_p95": 540.0},
             "Lineage": rr(1.0, 500) if d == "D3" else None, "Tamper": rr(1.0, 250) if d == "D3" else None,
             "ContradictionF1": {"f1": .91, "precision": .93, "recall": .89, "n": 400} if d == "D3" else None,
             "Hallucinated": rr(.006, 500) if d == "D3" else None,
             "Leakage": rr(0.0, 10000) if d in ("D3",) else (rr(.12, 10000) if d == "D0" else None),
             "Erasure": ({"completeness": 1.0, "n": 900, "residual": 0} if d == "D3" else {"completeness": .41, "n": 900, "residual": 531} if d == "D0" else None)}
        if d in ("D0", "D1", "D2", "D3"):
            P = {"D0": (.22, 0.0, .5, .45), "D1": (.40, 0.0, .6, .55), "D2": (.55, 0.0, .66, .6), "D3": (.93, .96, .9, .94)}[d]
            o["pillars"] = {"security": P[0] if d != "D3" else .94, "provenance": P[1], "consistency": P[2], "access": P[3],
                            "components": {}, "missing": {} if d == "D3" else {"consistency": ["ContradictionF1", "Hallucinated"]},
                            "provisional": d != "D3"}
            o["MGS"] = round(4 / sum(1 / max(x, .01) for x in P[:4]), 4)
        else:
            o["pillars"] = {"security": None, "provenance": None, "consistency": None, "access": None, "missing": {}, "provisional": True}
            o["MGS"] = None
        bd[d] = o
    for i in range(1, 12):
        f = f"f{i:02d}"
        fam[f] = {}
        for d in ("D0", "D1", "D2", "D3"):
            base = spec[d][1] * (0.5 + rnd.random())
            fam[f][d] = {"ASR": rr(min(.99, base), 50), "ISR": rr(min(.99, spec[d][0] * (0.6 + .6 * rnd.random())), 50)}
    sc = []
    c = bd["D3"]
    r_ = lambda m: (m["rate"], [m["ci_low"], m["ci_high"]], m["n"])  # noqa: E731
    sc += [{"id": "M1", "metric": "Injection success (ISR)", "target": "<= 5%", "measured": r_(c["ISR"])[0], "ci": r_(c["ISR"])[1], "n": c["ISR"]["n"], "defence": "D3"},
           {"id": "M2", "metric": "Attack success (ASR)", "target": "<= 5%", "measured": c["ASR"]["rate"], "ci": [c["ASR"]["ci_low"], c["ASR"]["ci_high"]], "n": c["ASR"]["n"], "defence": "D3"},
           {"id": "M3", "metric": "Adaptive ASR@Kmax", "target": "<= 10%", "measured": c["AdaptiveASR"]["rate"], "ci": [c["AdaptiveASR"]["ci_low"], c["AdaptiveASR"]["ci_high"]], "n": c["AdaptiveASR"]["n"], "defence": "D3"},
           {"id": "M4", "metric": "Persistence (sessions)", "target": "<= 1 session", "measured": .4, "ci": None, "n": c["Persistence"]["n_stored"], "defence": "D3"},
           {"id": "M5", "metric": "Lineage / tamper detection", "target": "100% / 100%", "measured": [1.0, 1.0], "ci": None, "n": 250, "defence": "D3"},
           {"id": "M6", "metric": "Attribution accuracy", "target": ">= 95%", "measured": c["Attribution"]["rate"], "ci": [c["Attribution"]["ci_low"], c["Attribution"]["ci_high"]], "n": 120, "defence": "D3"},
           {"id": "M7", "metric": "Contradiction F1", "target": ">= 0.90", "measured": .91, "ci": None, "n": 400, "defence": "D3"},
           {"id": "M8", "metric": "False quarantine rate", "target": "<= 2%", "measured": c["FQR"]["rate"], "ci": [c["FQR"]["ci_low"], c["FQR"]["ci_high"]], "n": 400, "defence": "D3"},
           {"id": "M9", "metric": "Hallucinated memories", "target": "<= 1%", "measured": c["Hallucinated"]["rate"], "ci": [c["Hallucinated"]["ci_low"], c["Hallucinated"]["ci_high"]], "n": 500, "defence": "D3"},
           {"id": "M10", "metric": "Cross-tenant leakage", "target": "0 in 10,000", "measured": 0.0, "ci": [0.0, c["Leakage"]["ci_high"]], "n": 10000, "defence": "D3"},
           {"id": "M11", "metric": "Extraction success", "target": "<= 1%", "measured": c["Extraction"]["rate"], "ci": [c["Extraction"]["ci_low"], c["Extraction"]["ci_high"]], "n": 500, "defence": "D3"},
           {"id": "M12", "metric": "Erasure completeness", "target": "100%", "measured": 1.0, "ci": None, "n": 900, "defence": "D3"},
           {"id": "M13", "metric": "Utility retention (D3-D0)", "target": ">= -2 pts", "measured": -.01, "ci": [-.04, .02], "n": 200, "defence": "D3"},
           {"id": "M14", "metric": "Overhead p95 (read ms / gate ms / extra tok per write)", "target": "40 ms / 1.5 s / 600 tok", "measured": [22.0, 1480.0, 540.0], "ci": None, "n": None, "defence": "D3"}]
    # make a couple of rows "not measured" to exercise the path
    sc[8]["measured"], sc[8]["ci"], sc[8]["n"] = None, None, None
    bd["D3"]["Hallucinated"] = None
    metrics = {"SAMPLE": True, "run_id": "SAMPLE-FIXTURE-0000", "generated": "synthetic", "primary_grid": "main", "n_episodes": 4210, "n_ok": 4188,
               "n_errors": 22, "n_attack_episodes": 2400, "grids": ["main", "ablation", "adaptive", "utility"], "by_defence": bd, "by_family": fam,
               "flags": ["SAMPLE: every number in this results directory is synthetic",
                         "22 episode(s) ended in error/timeout; they are excluded from every rate"],
               "scorecard": sc, "usage": {"llm_calls": 61234, "tokens_in": 48200000, "tokens_out": 6100000},
               "mgs_formula": "DEMO_DECK_PLAN section 5.3"}
    (dest / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (dest / "run_meta.json").write_text(json.dumps({"run_id": "SAMPLE", "git_sha": "0000000000", "gpu": ["A100-SXM4-80GB x8 (SAMPLE)"],
                                                   "models": {"agent": "nvidia/nemotron-3.5-lightning-30b-a3b", "attacker": "nvidia/nemotron-3-ultra-550b-a55b"},
                                                   "versions": {"pandas": "x"}}, indent=2), encoding="utf-8")
    with (dest / "cells.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "metric", "backend", "defence", "family", "n", "k", "rate"])
        for b in ("primary", "mem0"):
            w.writerow(["SAMPLE", "asr", b, "D3", "ALL", 100, 3, .03])
    with (dest / "compute.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "instance_type", "gpu", "n_gpus", "hours", "usd", "model", "served_by"])
        w.writerow(["SAMPLE", "brev-8xa100", "A100-80GB", 8, 6, 95, "nemotron-3.5-lightning", "nim_self_hosted"])
    print(f"wrote SAMPLE results to {dest}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", help="results directory containing metrics.json")
    ap.add_argument("--out", default=str(ROOT / "deliverables" / "Custos_AEGIS2026_Final.pptx"))
    ap.add_argument("--claims", default=str(ROOT / "docs" / "CLAIMS.md"))
    ap.add_argument("--claims-values", default=str(ROOT / "deliverables" / "claims_values.csv"))
    ap.add_argument("--video-url", default=None, help="demo video link shown on the demo slide")
    ap.add_argument("--embed-video", default=None, help="mp4 to embed on the demo slide (poster: deliverables/demo_screenshot.png)")
    ap.add_argument("--demo-note", default="Live golden-path runs so far: the undefended agent paid the attacker in several seeds; Custos quarantined in every run and blocked the payment.")
    ap.add_argument("--sample", action="store_true", help="force the SAMPLE DATA watermark")
    ap.add_argument("--make-sample", metavar="DIR", help="write a synthetic SAMPLE results dir and exit")
    a = ap.parse_args()
    if a.make_sample:
        make_sample(Path(a.make_sample))
        return 0
    if not a.results:
        ap.error("--results is required")
    return build(a)


if __name__ == "__main__":
    sys.exit(main())

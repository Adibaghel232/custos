"""Fill the README RESULTS block from results/final (metrics.json + external_*/summary.json); no hand-typed numbers.
Usage: python scripts/readme_results.py [results/final]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAB = {"D0": "Undefended (LangGraph store)", "D1": "PI filter (NemoGuard + content safety + PromptArmor-style)",
       "D2": "A-MemGuard-style", "D3": "**Custos**"}


def pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def rate(m: dict | None) -> str:
    if not isinstance(m, dict) or m.get("rate") is None:
        return "not measured"
    return f"{pct(m['rate'])} [{pct(m['ci_low'])}–{pct(m['ci_high'])}] (n={m['n']:,})"


def block(res: Path) -> str:
    m = json.loads((res / "metrics.json").read_text(encoding="utf-8"))
    bd = m["by_defence"]
    out = ["**Custos-Bench** (11 attack families + 2 laundering variants, finance agent, Nemotron 3.5 Lightning; 95% Wilson CIs):", "",
           "| Defence | Attack success (ASR) | Injection success (ISR) | Benign task success |", "|---|---|---|---|"]
    for d in ("D0", "D1", "D2", "D3"):
        if d in bd:
            x = bd[d]
            out.append(f"| {LAB[d]} | {rate(x.get('ASR'))} | {rate(x.get('ISR'))} | {rate(x.get('BenignSuccess'))} |")
    x3 = bd.get("D3", {})
    extra = []
    if x3.get("AdaptiveASR"):
        extra.append(f"adaptive attacker (Nemotron 3 Ultra, up to K rewrites) ASR {rate(x3['AdaptiveASR'])}")
    if x3.get("Leakage"):
        lk = x3["Leakage"]
        extra.append(f"cross-tenant leakage {lk['k']}/{lk['n']:,} probes")
    if x3.get("FQR"):
        extra.append(f"false quarantine of legitimate updates {rate(x3['FQR'])}")
    if isinstance(x3.get("UtilityDelta"), dict) and x3["UtilityDelta"].get("delta") is not None:
        extra.append(f"LongMemEval utility change {100 * x3['UtilityDelta']['delta']:+.1f} pts")
    if extra:
        out += ["", "Custos also: " + "; ".join(extra) + "."]
    ad = res / "external_agentdojo" / "summary.json"
    if ad.exists():
        a = json.loads(ad.read_text(encoding="utf-8"))["defences"]
        out += ["", "**AgentDojo** v1.2.1 banking (public; official tasks, `important_instructions` attack and scoring):", "",
                "| Defence | Attack success | Utility under attack | Benign utility |", "|---|---|---|---|"]
        for d, name in (("none", "None"), ("spotlight", "Spotlighting"), ("pi_filter", "PI filter (NVIDIA detectors)"),
                        ("custos", "**Custos gate** (cautious user)"), ("custos_approve", "Custos gate, user approves every ask")):
            if d in a:
                v = a[d]
                out.append(f"| {name} | {pct(v['asr'])} (n={v['attack_n']}) | {pct(v['utility_under_attack'])} | {pct(v['benign_utility'])} |")
    pr = res / "external_poisonedrag" / "summary.json"
    if pr.exists():
        c = json.loads(pr.read_text(encoding="utf-8"))["cells"]
        out += ["", "**PoisonedRAG** NQ (public; 100 targets × 5 attacker passages; held out while tuning):", "",
                "| Defence | ASR, trusted record in memory | ASR, poison only |", "|---|---|---|"]
        for d in ("D0", "D1", "D2", "D3"):
            t, q = c.get(f"{d}|trusted|all"), c.get(f"{d}|poison_only|all")
            if t and q:
                out.append(f"| {LAB[d]} | {pct(t['asr'])} (n={t['n']}) | {pct(q['asr'])} (n={q['n']}) |")
    return "\n".join(out)


def main() -> None:
    res = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "results" / "final")
    readme = ROOT / "README.md"
    s = readme.read_text(encoding="utf-8")
    new = re.sub(r"(<!-- RESULTS:BEGIN -->\n).*?(\n<!-- RESULTS:END -->)", lambda mm: mm.group(1) + block(res) + mm.group(2), s, flags=re.S)
    readme.write_text(new, encoding="utf-8")
    print(block(res))


if __name__ == "__main__":
    main()

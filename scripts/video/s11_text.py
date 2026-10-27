"""Write the S11 (Bench) narration of deliverables/voiceover_script.md from the result files, so no number is typed by hand.

Reads results/final/final.json (own bench, rates as fractions) and runs/external_{agentdojo,poisonedrag}/summary.json.
Usage: python scripts/video/s11_text.py [--final results/final/final.json] [--ext runs]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deliverables" / "voiceover_script.md"


def p(x: float | None) -> str | None:
    if x is None:
        return None
    v = round(100 * x, 1)
    return f"{v:.0f}" if v == int(v) else f"{v:.1f}"


def load(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def lines(final: dict | None, ad: dict | None, pr: dict | None) -> list[str]:
    out = ["One demo is not proof, so we built Custos-Bench: eleven attack families, an adaptive attacker, "
           "and a replayable episode behind every number."]
    if final and final.get("ASR_U") is not None and final.get("ASR_C") is not None:
        out.append(f"Across {final['N_ATTACK']:,} attack episodes, attacks succeeded {p(final['ASR_U'])} percent of the time "
                   f"with no defence, and {p(final['ASR_C'])} percent with Custos.")
    if final and final.get("AASR_C") is not None:
        s = f"An adaptive attacker that rewrote its payloads up to {final['K']} times reached {p(final['AASR_C'])} percent against Custos"
        out.append(s + (f", and {p(final['AASR_PI'])} percent against the injection filter." if final.get("AASR_PI") is not None else "."))
    if ad or pr:
        out.append("Then we ran two public benchmarks that we did not write.")
    if ad:
        d = ad["defences"]
        n, c = d.get("none"), d.get("custos")
        if n and c:
            du = abs(c["utility_under_attack"] - n["utility_under_attack"])
            tail = ", with the same task success." if du <= 0.03 else \
                f", with task success at {p(c['utility_under_attack'])} percent instead of {p(n['utility_under_attack'])}."
            out.append(f"On AgentDojo's banking tasks, injected payment orders succeeded {p(n['asr'])} percent of the time "
                       f"without a defence, and {p(c['asr'])} percent with the Custos gate{tail}")
    if pr:
        c = pr["cells"]
        t0, t3, q3 = c.get("D0|trusted|all"), c.get("D3|trusted|all"), c.get("D3|poison_only|all")
        if t0 and t3:
            out.append(f"On PoisonedRAG, when the trusted answer is already in memory, poisoning drops from {p(t0['asr'])} "
                       f"to {p(t3['asr'])} percent.")
        if q3:
            out.append("When nothing trusted exists, Custos cannot know the truth, and we report that too.")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", type=Path, default=ROOT / "results" / "final" / "final.json")
    ap.add_argument("--ext", type=Path, default=ROOT / "runs")
    a = ap.parse_args()
    ls = lines(load(a.final), load(a.ext / "external_agentdojo" / "summary.json"),
               load(a.ext / "external_poisonedrag" / "summary.json"))
    note = ("> screen: Bench dashboard (results/final/ui), then the public-benchmarks card (deck slide image) from the sentence "
            "that mentions public benchmarks. Numbers written by scripts/video/s11_text.py from the result files.")
    block = "### S11 · Bench\n" + " ".join(ls) + "\n" + note + "\n\n"
    s = SCRIPT.read_text(encoding="utf-8")
    s2 = re.sub(r"### S11 · Bench\n.*?\n\n(?=### S12)", block.replace("\\", "\\\\"), s, flags=re.S)
    s2 = s2.replace("- The Bench screen shows SAMPLE data until the final results exist. Its words below contain no numbers on purpose.",
                    "- The Bench screen shows the final run (results/final/ui). The S11 numbers are written by scripts/video/s11_text.py "
                    "from results/final/final.json and the two public-benchmark summaries.")
    SCRIPT.write_text(s2, encoding="utf-8")
    print(block)


if __name__ == "__main__":
    main()

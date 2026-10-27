"""deliverables/voiceover_script.md -> deliverables/video/work/shots.json (one entry per `### Sx` block).

Spoken text = the plain lines of a block ('>' lines and HTML comments are notes). Each sentence gets a `tts` variant with
pronunciation fixes for the Windows voice; captions always use the original `text`.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "deliverables" / "voiceover_script.md"
OUT = ROOT / "deliverables" / "video" / "work" / "shots.json"

TTS_FIX = [
    (r"\bT-zero\b", "T zero"), (r"\bT-three\b", "T three"), (r"\bERP\b", "E R P"), (r"\bNemotron\b", "Nemo-tron"),
    (r"\bDPDP\b", "D P D P"), (r"\bMINJA\b", "Minja"), (r"\bAgentPoison\b", "Agent Poison"), (r"\bOWASP\b", "Oh-wasp"),
    (r"\bCustos-Bench\b", "Custos Bench"), (r"\bNVIDIA\b", "en-vidia"), (r"\bGPUs\b", "G P Us"), (r"\bAI\b", "A I"),
    (r"\blakh\b", "lakh"), (r"\bleave-one-out\b", "leave one out"), (r"\bhash-chained\b", "hash chained"),
]


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'])", text.strip())
    return [p.strip() for p in parts if p.strip()]


def tts_text(s: str) -> str:
    for pat, rep in TTS_FIX:
        s = re.sub(pat, rep, s)
    return s.replace("’", "'")


def parse(src: Path = SRC) -> list[dict]:
    shots: list[dict] = []
    cur: dict | None = None
    in_comment = False
    for line in src.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^###\s+(S\d+)\s*[·\-]\s*(.+?)\s*$", line)
        if m:
            cur = {"id": m.group(1), "title": m.group(2), "lines": [], "notes": []}
            shots.append(cur)
            continue
        if line.startswith("---") or line.startswith("## ") or line.startswith("# "):
            cur = None
            continue
        if cur is None:
            continue
        if "<!--" in line:
            in_comment = "-->" not in line
            continue
        if in_comment:
            in_comment = "-->" not in line
            continue
        if line.startswith(">"):
            cur["notes"].append(line.lstrip("> ").strip())
        elif line.strip():
            cur["lines"].append(line.strip())
    out = []
    for s in shots:
        text = " ".join(s["lines"])
        out.append({"id": s["id"], "title": s["title"], "text": text, "notes": s["notes"],
                    "sentences": [{"text": t, "tts": tts_text(t)} for t in sentences(text)]})
    return out


if __name__ == "__main__":
    shots = parse()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(shots, ensure_ascii=False, indent=1), encoding="utf-8")
    words = sum(len(s["text"].split()) for s in shots)
    print(f"{len(shots)} shots, {words} words -> {OUT}")
    sys.exit(0 if shots else 1)

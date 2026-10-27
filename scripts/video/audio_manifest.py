"""Per-shot narration timing -> work/audio_manifest.json.

Draft voice : work/audio/<shot>_<nn>.wav (one per sentence, from tts.ps1).
Own voice   : deliverables/video/own_voice/<shot>.wav (one per shot; sentence timing is split by word count).
A shot lasts at least lead + narration + tail seconds; the recorder holds a shot until then.
"""
from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "deliverables" / "video" / "work"
LEAD, GAP, TAIL = 0.4, 0.3, 0.9


def wav_dur(p: Path) -> float:
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def build(own_voice: bool = False) -> dict:
    shots = json.loads((WORK / "shots.json").read_text(encoding="utf-8"))
    out = []
    for s in shots:
        sents = []
        t = LEAD
        if own_voice:
            p = ROOT / "deliverables" / "video" / "own_voice" / f"{s['id']}.wav"
            if not p.exists():
                raise SystemExit(f"own voice file missing: {p}")
            total = wav_dur(p)
            words = [max(1, len(x["text"].split())) for x in s["sentences"]]
            ws = sum(words)
            cur = 0.0
            for x, w in zip(s["sentences"], words):
                d = total * w / ws
                sents.append({"text": x["text"], "wav": str(p), "offset_in_wav": cur, "start": LEAD + cur, "dur": d})
                cur += d
            t = LEAD + total
            narr_end = t
            audio = [{"wav": str(p), "start": LEAD}]
        else:
            audio = []
            for i, x in enumerate(s["sentences"], 1):
                p = WORK / "audio" / f"{s['id']}_{i:02d}.wav"
                d = wav_dur(p)
                sents.append({"text": x["text"], "wav": str(p), "start": t, "dur": d})
                audio.append({"wav": str(p), "start": t})
                t += d + GAP
            narr_end = t - GAP if sents else LEAD
        out.append({"id": s["id"], "title": s["title"], "sentences": sents, "audio": audio,
                    "narration_end": narr_end, "min_s": narr_end + TAIL})
    m = {"lead": LEAD, "gap": GAP, "tail": TAIL, "shots": out}
    (WORK / "audio_manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    return m


if __name__ == "__main__":
    m = build("--own-voice" in sys.argv)
    tot = sum(s["min_s"] for s in m["shots"])
    print(f"audio manifest: {len(m['shots'])} shots, narration floor {tot:.1f}s")
    for s in m["shots"]:
        print(f"  {s['id']:4s} min {s['min_s']:5.1f}s  {s['title']}")

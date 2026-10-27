"""Assemble the final video: captured frames + narration + burned-in captions -> deliverables/video/custos_demo.mp4.

  .venv/Scripts/python.exe scripts/video/assemble.py [--own-voice] [--no-embed]

Inputs : work/timeline.json + work/frames/<shot>/*.jpg (record.mjs), work/audio_manifest.json (audio_manifest.py)
Outputs: deliverables/video/custos_demo.mp4 (H.264 1080p30 + AAC), deliverables/video/custos_demo.srt, custos_demo_720p.mp4
Layout : each shot is 1920x1000 UI; an 80 px navy band under it carries the captions, so captions never cover the evidence.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "deliverables" / "video" / "work"
OUTDIR = ROOT / "deliverables" / "video"
FPS = 30
SR = 24000


def find_ffmpeg() -> str:
    for c in (ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe", ROOT / "tools" / "ffmpeg" / "ffmpeg.exe"):
        if c.exists():
            return str(c)
    w = shutil.which("ffmpeg")
    if w:
        return w
    raise SystemExit("ffmpeg not found: run scripts/make_video.ps1 (it downloads a static build into tools/ffmpeg)")


FFMPEG = find_ffmpeg()
FFPROBE = str(Path(FFMPEG).with_name("ffprobe.exe")) if Path(FFMPEG).with_name("ffprobe.exe").exists() else (shutil.which("ffprobe") or "ffprobe")


def run(cmd: list[str], cwd: Path | None = None) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        print(" ".join(cmd))
        print(r.stderr[-3000:])
        raise SystemExit(f"ffmpeg failed ({r.returncode})")


def jpeg_size(p: Path) -> tuple[int, int] | None:
    import struct
    b = p.read_bytes()[:70000]
    i = 2
    while i < len(b) - 9:
        if b[i] != 0xFF:
            i += 1
            continue
        if b[i + 1] in (0xC0, 0xC2):
            h, w = struct.unpack(">HH", b[i + 5:i + 9])
            return w, h
        i += 2 + struct.unpack(">H", b[i + 2:i + 4])[0]
    return None


def build_shot(shot: dict, out: Path) -> float:
    d0 = WORK / "frames" / shot["id"]
    frames = [f for f in shot["frames"] if jpeg_size(d0 / f["file"]) == (1920, 1000)]   # drop the odd-sized frame a navigation can emit
    if len(frames) != len(shot["frames"]):
        print(f"  {shot['id']}: dropped {len(shot['frames']) - len(frames)} frame(s) of the wrong size")
    if not frames:
        raise SystemExit(f"shot {shot['id']} has no frames")
    d = WORK / "frames" / shot["id"]
    ts = [f["ts"] for f in frames]
    ts[0] = min(ts[0], shot["t_begin"])                     # hold the first frame back to the moment the shot started
    t_end = max(shot["t_end"], ts[-1] + 0.05)
    lines = ["ffconcat version 1.0"]
    for i, f in enumerate(frames):
        nxt = ts[i + 1] if i + 1 < len(ts) else t_end
        lines += [f"file '{f['file']}'", f"duration {max(nxt - ts[i], 0.001):.4f}"]
    lines.append(f"file '{frames[-1]['file']}'")
    (d / "list.txt").write_text("\n".join(lines), encoding="utf-8")
    run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "list.txt",
         "-vf", f"fps={FPS},scale=1920:1000:force_original_aspect_ratio=decrease,pad=1920:1000:(ow-iw)/2:(oh-ih)/2:color=0x0B1426,format=yuv420p",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-r", str(FPS), str(out.resolve())], cwd=d)
    return t_end - ts[0]


def to_wav24(src: str, dst: Path) -> None:
    run([FFMPEG, "-y", "-loglevel", "error", "-i", src, "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", str(dst)])


def read_wav(p: Path) -> np.ndarray:
    with wave.open(str(p)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def split_cue(text: str, start: float, dur: float, limit: int = 92) -> list[tuple[float, float, str]]:
    if len(text) <= limit:
        return [(start, start + dur, text)]
    words = text.split()
    parts, cur = [], []
    for w in words:
        if sum(len(x) + 1 for x in cur) + len(w) > limit and cur:
            parts.append(" ".join(cur))
            cur = []
        cur.append(w)
    parts.append(" ".join(cur))
    total = sum(len(p) for p in parts)
    out, t = [], start
    for p in parts:
        d = dur * len(p) / total
        out.append((t, t + d, p))
        t += d
    return out


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def main() -> None:
    own = "--own-voice" in sys.argv
    manifest = json.loads((WORK / "audio_manifest.json").read_text(encoding="utf-8"))
    timeline = {t["id"]: t for t in json.loads((WORK / "timeline.json").read_text(encoding="utf-8"))}
    order = [s["id"] for s in manifest["shots"]]
    missing = [i for i in order if i not in timeline]
    if missing:
        raise SystemExit(f"no recording for shots {missing}: run record.mjs")
    shots_dir = WORK / "shots"
    shutil.rmtree(shots_dir, ignore_errors=True)
    shots_dir.mkdir(parents=True)
    wav_dir = WORK / "audio24"
    shutil.rmtree(wav_dir, ignore_errors=True)
    wav_dir.mkdir(parents=True)

    # 1. per-shot videos
    starts: dict[str, float] = {}
    t = 0.0
    concat = ["ffconcat version 1.0"]
    for sid in order:
        out = shots_dir / f"{sid}.mp4"
        dur = build_shot(timeline[sid], out)
        starts[sid] = t
        t += dur
        concat.append(f"file '{out.name}'")
        print(f"  {sid}: {dur:5.1f}s  (narration needs {next(s for s in manifest['shots'] if s['id'] == sid)['min_s']:.1f}s)")
    (shots_dir / "list.txt").write_text("\n".join(concat), encoding="utf-8")
    total = t
    video_raw = WORK / "video_raw.mp4"
    run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", str(video_raw.resolve())], cwd=shots_dir)

    # 2. audio track + captions
    mix = np.zeros(int((total + 2) * SR), dtype=np.float32)
    cues: list[tuple[float, float, str]] = []
    k = 0
    for s in manifest["shots"]:
        base = starts[s["id"]]
        if own:
            a = s["audio"][0]
            dst = wav_dir / f"{s['id']}.wav"
            to_wav24(a["wav"], dst)
            x = read_wav(dst)
            i0 = int((base + a["start"]) * SR)
            mix[i0:i0 + len(x)] += x[: max(0, len(mix) - i0)]
        else:
            for a in s["audio"]:
                dst = wav_dir / f"{k:03d}.wav"
                k += 1
                to_wav24(a["wav"], dst)
                x = read_wav(dst)
                i0 = int((base + a["start"]) * SR)
                mix[i0:i0 + len(x)] += x[: max(0, len(mix) - i0)]
        for sent in s["sentences"]:
            cues += split_cue(sent["text"], base + sent["start"], sent["dur"] + 0.15)
        if s["narration_end"] + manifest["tail"] > timeline[s["id"]]["t_end"] - timeline[s["id"]]["t_begin"] + 0.5:
            print(f"  WARNING {s['id']}: video shorter than its narration")
    peak = float(np.max(np.abs(mix))) or 1.0
    mix = mix / peak * 0.89
    audio_wav = WORK / "narration.wav"
    with wave.open(str(audio_wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((mix * 32767).astype(np.int16).tobytes())
    srt = []
    for i, (a, b, text) in enumerate(cues, 1):
        srt.append(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{text}\n")
    (WORK / "captions.srt").write_text("\n".join(srt), encoding="utf-8")
    shutil.copyfile(WORK / "captions.srt", OUTDIR / "custos_demo.srt")
    # the same captions as ASS, so the size is in real pixels (1920x1080 canvas, bottom band y=1000..1080)
    def ass_time(t: float) -> str:
        cs = int(round(t * 100))
        return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"
    ass = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: 1920", "PlayResY: 1080", "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
           "[V4+ Styles]",
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
           "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
           "Style: Cap,Segoe UI Semibold,34,&H00FFFFFF,&H000000FF,&H00260F0B,&H00000000,0,0,0,0,100,100,0,0,1,1.6,0,2,100,100,7,1", "",
           "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for a, b, text in cues:
        ass.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Cap,,0,0,0,,{text.replace(chr(10), ' ')}")
    (WORK / "captions.ass").write_text("\n".join(ass), encoding="utf-8")

    # 3. final: pad to 1080p, burn captions, mux
    final = OUTDIR / "custos_demo.mp4"
    vf = "pad=1920:1080:0:0:color=0x0B1426,ass=captions.ass"
    run([FFMPEG, "-y", "-loglevel", "error", "-i", str(video_raw.resolve()), "-i", str(audio_wav.resolve()),
         "-vf", vf, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-maxrate", "14M", "-bufsize", "28M",
         "-pix_fmt", "yuv420p", "-r", str(FPS), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000", "-ac", "2", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", "-shortest", str(final.resolve())], cwd=WORK)
    if "--no-embed" not in sys.argv:
        run([FFMPEG, "-y", "-loglevel", "error", "-i", str(final), "-vf", "scale=1280:720", "-c:v", "libx264", "-preset", "medium", "-crf", "24",
             "-maxrate", "3M", "-bufsize", "6M", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
             str(OUTDIR / "custos_demo_720p.mp4")])

    # 4. report
    pr = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration,size", "-show_entries", "stream=codec_name,width,height,r_frame_rate",
                         "-of", "json", str(final)], capture_output=True, text=True)
    info = json.loads(pr.stdout)
    dur = float(info["format"]["duration"])
    size = int(info["format"]["size"]) / 1e6
    print(f"\n{final}\n  duration {int(dur // 60)}:{dur % 60:04.1f}  size {size:.1f} MB  streams {[(s.get('codec_name'), s.get('width'), s.get('height')) for s in info['streams']]}")
    assert dur <= 240, "video is longer than 4:00"
    assert size <= 200, "video is bigger than 200 MB"


if __name__ == "__main__":
    main()

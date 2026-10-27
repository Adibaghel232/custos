"""Reading episodes.jsonl (shared by the runner and the metrics; no heavy imports)."""
from __future__ import annotations

from pathlib import Path

from .types import BenchRecord


def load_records(path: Path) -> dict[str, BenchRecord]:
    """Last record per episode id (later lines supersede earlier retries/errors)."""
    out: dict[str, BenchRecord] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    r = BenchRecord.model_validate_json(line)
                except Exception:  # noqa: BLE001 - a torn last line after a crash
                    continue
                out[r.episode.episode_id] = r
    return out


def iter_record_files(path: Path) -> list[Path]:
    """`path` is a run dir with episodes.jsonl, an episodes.jsonl file, or a parent holding several grid dirs."""
    path = Path(path)
    if path.is_file():
        return [path]
    if (path / "episodes.jsonl").exists():
        return [path / "episodes.jsonl"]
    return sorted(path.glob("*/episodes.jsonl"))


def load_all_records(path: Path) -> list[BenchRecord]:
    out: dict[str, BenchRecord] = {}
    for f in iter_record_files(path):
        out.update(load_records(f))
    return list(out.values())

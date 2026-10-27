"""run.json: git sha, settings hash, model ids + compliance verdict, endpoints, hardware, timings, token totals."""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from custos.compliance import check as compliance_check
from custos.settings import ROOT, get_settings

from .. import _c3


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5,
                              check=True).stdout.strip()
    except Exception:  # noqa: BLE001 - not a git checkout
        return "unknown"


def _gpu() -> list[str]:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], capture_output=True,
                             text=True, timeout=5, check=True).stdout
        return [l.strip() for l in out.splitlines() if l.strip()]
    except Exception:  # noqa: BLE001
        return []


def build_run_meta(cfg: Any, n_planned: int, extra: dict | None = None) -> dict[str, Any]:
    s = get_settings()
    try:
        comp = {"ok": True, "models": compliance_check(s)}
    except Exception as e:  # noqa: BLE001
        comp = {"ok": False, "error": str(e)}
    endpoints = {}
    for role in s.models:
        try:
            ep = s.endpoint(role)
            endpoints[role] = {"name": ep.name, "base_url": ep.base_url}
        except Exception as e:  # noqa: BLE001
            endpoints[role] = {"error": str(e)[:100]}
    return {
        "run_id": cfg.name, "grid": cfg.name, "git_sha": _git_sha(),
        "settings_hash": hashlib.sha256(json.dumps(s.raw, sort_keys=True, default=str).encode()).hexdigest()[:16],
        "models": dict(s.models), "compliance": comp, "routing": dict(s.routing), "endpoints": endpoints,
        "thresholds": dict(s.thresholds), "gpu": _gpu(), "python": sys.version.split()[0], "platform": platform.platform(),
        "have_agent": _c3.HAVE_AGENT, "n_planned": n_planned, "started": datetime.now(timezone.utc).isoformat(),
        "finished": None, "config": cfg.model_dump(), "versions": _versions(), **(extra or {})}


def _versions() -> dict[str, str]:
    out = {}
    for m in ("pydantic", "pandas", "numpy", "langgraph", "openai"):
        try:
            out[m] = __import__(m).__version__
        except Exception:  # noqa: BLE001
            pass
    return out


def write_run_meta(out_dir: Path, meta: dict[str, Any]) -> None:
    (out_dir / "run.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")

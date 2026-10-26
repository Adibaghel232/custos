"""Loads configs/tools/registry.yaml (SPEC §12.3). Also derives the origin of a tool result for source registration."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from custos.settings import ROOT
from custos.types import Tier

REGISTRY_PATH = ROOT / "configs" / "tools" / "registry.yaml"


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    output_tier: Tier
    risk: str                                   # low | medium | high | critical
    critical_args: dict[str, Tier] = field(default_factory=dict)
    source_type: str | None = None
    kind: str = "world"                         # world | memory

    @property
    def gated(self) -> bool:
        return self.risk in ("high", "critical")

    def openai(self) -> dict[str, Any]:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


def _tier(v: Any) -> Tier:
    return Tier[str(v).upper()] if not isinstance(v, int) else Tier(v)


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, ToolSpec]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["tools"]
    out: dict[str, ToolSpec] = {}
    for name, d in raw.items():
        out[name] = ToolSpec(
            name=name, description=d["description"], parameters=d["parameters"],
            output_tier=_tier(d.get("output_tier", "T1")), risk=d.get("risk", "low"),
            critical_args={a: _tier(t) for a, t in (d.get("critical_args") or {}).items()},
            source_type=d.get("source_type"), kind=d.get("kind", "world"))
    return out


def load_registry(path: str | Path | None = None) -> dict[str, ToolSpec]:
    return _load(str(path or REGISTRY_PATH))


def openai_tools(registry: dict[str, ToolSpec], names: list[str] | None = None) -> list[dict[str, Any]]:
    return [s.openai() for n, s in registry.items() if names is None or n in names]


_EMAIL_RE = re.compile(r"^From:\s*(?:[^<\n]*<)?([^\s<>@]+@([^\s<>@]+))>?", re.M)
_URL_RE = re.compile(r"^URL:\s*https?://([^/\s]+)", re.M)


def origin_of(tool: str, text: str) -> str:
    """Registrable origin of a tool result, parsed from the headers our world writes (never from the model)."""
    if (m := _EMAIL_RE.search(text)):
        return m.group(2).lower()
    if (m := _URL_RE.search(text)):
        return m.group(1).lower()
    if tool in ("erp_lookup_vendor", "erp_list_invoices", "erp_get_invoice"):
        return "erp"
    if tool == "gst_verify":
        return "gst_registry"
    if tool == "policy_lookup":
        return "policy_store"
    return f"tool:{tool}"

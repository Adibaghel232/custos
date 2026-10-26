"""Settings: configs/settings.yaml + .env. The only place model ids, endpoints and thresholds are resolved."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Endpoint:
    name: str
    base_url: str
    api_key: str
    max_concurrency: int


class Settings:
    def __init__(self, raw: dict[str, Any]):
        self.raw = raw
        self.models: dict[str, str] = dict(raw["models"])
        self.routing: dict[str, str] = dict(raw["routing"])
        for role in self.models:  # per-run override: CUSTOS_ROUTE_AGENT=vllm
            if env := os.getenv(f"CUSTOS_ROUTE_{role.upper()}"):
                self.routing[role] = env
        self.thresholds: dict[str, float] = dict(raw["thresholds"])
        self.llm: dict[str, Any] = dict(raw["llm"])
        self.paths: dict[str, Path] = {k: ROOT / v for k, v in raw["paths"].items()}
        self.jailbreak_detect_url: str = raw["jailbreak_detect_url"]
        self._endpoints: dict[str, Any] = raw["endpoints"]

    def model(self, role: str) -> str:
        return self.models[role]

    def endpoint(self, role: str) -> Endpoint:
        name = self.routing.get(role, "hosted")
        ep = self._endpoints[name]
        base = ep.get("base_url") or os.environ.get(ep.get("base_url_env", ""), "")
        key = ep.get("api_key") or os.environ.get(ep.get("api_key_env", ""), "")
        if not base:
            raise RuntimeError(f"endpoint '{name}' for role '{role}' has no base_url (set {ep.get('base_url_env')})")
        return Endpoint(name, base.rstrip("/"), key or "EMPTY", int(ep.get("max_concurrency", 8)))

    def t(self, name: str) -> float:
        return self.thresholds[name]

    @property
    def api_key(self) -> str:
        return os.environ.get("NVIDIA_API_KEY", "")


@lru_cache(maxsize=4)
def get_settings(path: str | None = None) -> Settings:
    load_dotenv(ROOT / ".env")
    p = Path(path or os.getenv("CUSTOS_SETTINGS") or ROOT / "configs" / "settings.yaml")
    return Settings(yaml.safe_load(p.read_text(encoding="utf-8")))

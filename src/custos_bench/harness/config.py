"""Grid configs (configs/bench/*.yaml) and deterministic episode planning (SPEC §15.5)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from custos.crypto import jcs
from custos.settings import ROOT, get_settings
from custos.types import Episode

from ..attacks import generate
from ..attacks.paraphrase import apply_cached, load_cache
from ..defences.factory import is_custos
from ..types import AttackInstance

CONFIG_DIR = ROOT / "configs" / "bench"


class Group(BaseModel):
    families: list[str]                     # f01.. | L2 | L3 | benign | legit_update
    defences: list[str]
    n: int = 10
    memory_state: Literal["cold", "warm"] = "cold"
    trigger_sessions: int = 1
    user_policy: Literal["cautious", "fatigued"] = "cautious"
    backend: str = "primary"                # primary = langgraph store for D0-D2, native store for D3*
    rollback_on_alert: bool = False
    fatigued_rerun: bool = False
    tier_variant: str = "T0"
    seed_start: int = 0


class AdaptiveCfg(BaseModel):
    families: list[str] = ["f01", "f04", "f05", "f09"]
    defences: list[str] = ["D1", "D3"]
    seeds: int = 20
    rounds: int = 10


class UtilityCfg(BaseModel):
    defences: list[str] = ["D0", "D3"]
    limit: int | None = None                # first N questions (smoke)
    seed: int = 0


class GridConfig(BaseModel):
    name: str
    description: str = ""
    kind: Literal["episodes", "adaptive", "utility"] = "episodes"
    scenario: str = "finance"
    split: Literal["dev", "test"] = "test"
    groups: list[Group] = []
    adaptive: AdaptiveCfg | None = None
    utility: UtilityCfg | None = None
    concurrency: int = 16
    episode_timeout_s: int = 300
    paraphrase: bool = False                # use cached paraphrases if present (generate with `custos-bench paraphrase`)
    require_warm: bool = False              # fail instead of falling back to cold when a warm snapshot is missing
    keep_ledger_pct: int = 5


def load_config(path_or_name: str | Path) -> GridConfig:
    p = Path(path_or_name)
    if not p.exists():
        p = CONFIG_DIR / (str(path_or_name) if str(path_or_name).endswith(".yaml") else f"{path_or_name}.yaml")
    return GridConfig.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))


def config_hash() -> str:
    s = get_settings()
    return hashlib.sha256(json.dumps(s.thresholds, sort_keys=True).encode()).hexdigest()[:12]


def resolve_backend(defence: str, backend: str) -> str:
    if backend != "primary":
        return backend
    return "native" if is_custos(defence) else "langgraph"


def make_episode(*, grid: str, split: str, scenario: str, inst: AttackInstance, defence: str, backend: str,
                 memory_state: str, trigger_sessions: int, user_policy: str, adaptive_round: int = 0,
                 variant_id: str | None = None, warm_records: int | None = None) -> Episode:
    fields: dict[str, Any] = dict(
        grid=grid, split=split, scenario=scenario, family=inst.family,
        variant_id=variant_id or (inst.variant_id + ("-p" if inst.paraphrased else "")),
        backend=resolve_backend(defence, backend), defence=defence, memory_state=memory_state,
        warm_records=(1000 if memory_state == "warm" else 0) if warm_records is None else warm_records,
        trigger_sessions=trigger_sessions, user_policy=user_policy, adaptive_round=adaptive_round, seed=inst.seed,
        models=dict(get_settings().models), config_hash=config_hash())
    eid = hashlib.sha256(jcs(fields)).hexdigest()[:16]
    return Episode(episode_id=eid, **fields)


@dataclass
class Planned:
    episode: Episode
    inst: AttackInstance
    group: Group


def plan_episodes(cfg: GridConfig, *, use_paraphrase_cache: bool | None = None) -> list[Planned]:
    """Deterministic: same config + seeds => same episode ids. The same instance object is shared by all defences."""
    cache = load_cache() if (cfg.paraphrase if use_paraphrase_cache is None else use_paraphrase_cache) else {}
    out: list[Planned] = []
    seen: set[str] = set()
    for g in cfg.groups:
        for fam in g.families:
            for i in range(g.n):
                inst = generate(fam, cfg.split, g.seed_start + i, n_trigger=g.trigger_sessions, tier_variant=g.tier_variant)
                if cache and inst.kind == "attack":
                    inst = apply_cached(inst, cache)
                for d in g.defences:
                    ep = make_episode(grid=cfg.name, split=cfg.split, scenario=cfg.scenario, inst=inst, defence=d,
                                      backend=g.backend, memory_state=g.memory_state, trigger_sessions=g.trigger_sessions,
                                      user_policy=g.user_policy)
                    if ep.episode_id in seen:
                        continue
                    seen.add(ep.episode_id)
                    out.append(Planned(ep, inst, g))
    return out

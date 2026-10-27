"""Adaptive grid (SPEC §13.1): K grey-box rewrites per seed payload against one defence; one record per round."""
from __future__ import annotations

import asyncio

from ..attacks import generate
from ..attacks.adaptive import AdaptiveAttacker
from ..types import BenchRecord
from .config import GridConfig, Group, Planned, make_episode
from .episode import Env, execute
from .runner import Sink, load_records


async def run_adaptive(cfg: GridConfig, env: Env, *, concurrency: int, resume: bool, executor=execute) -> dict[str, BenchRecord]:
    ac = cfg.adaptive
    assert ac is not None, "adaptive grid needs an `adaptive:` block"
    path = env.out_dir / "episodes.jsonl"
    done = load_records(path) if resume else {}
    sink, sem, attacker = Sink(path), asyncio.Semaphore(concurrency), AdaptiveAttacker()
    out: dict[str, BenchRecord] = {k: v for k, v in done.items() if v.result.status == "ok"}
    grp = Group(families=ac.families, defences=ac.defences, n=ac.seeds)

    async def chain(fam: str, i: int, defence: str) -> None:
        inst = generate(fam, cfg.split, i)
        base = inst.variant_id
        hist: list[dict] = []
        for r in range(ac.rounds):
            ep = make_episode(grid=cfg.name, split=cfg.split, scenario=cfg.scenario, inst=inst, defence=defence,
                              backend="primary", memory_state="cold", trigger_sessions=1, user_policy="cautious",
                              adaptive_round=r, variant_id=f"{base}-r{r}")
            rec = out.get(ep.episode_id)
            if rec is None:
                async with sem:
                    rec = await executor(Planned(ep, inst, grp), env)
                    if rec.result.status != "ok":
                        rec = await executor(Planned(ep, inst, grp), env)
                await sink.write(rec)
                out[ep.episode_id] = rec
            if rec.result.status != "ok" or rec.result.target_behaviour:
                return
            hist.append({"round": r, "feedback": rec.extra.get("feedback", {}),
                         "artifacts": [a.body[:300] for a in inst.artifacts]})
            inst, _ = await attacker.rewrite(inst, rec.extra.get("feedback", {}), hist, defence, r + 1)

    try:
        await asyncio.gather(*(chain(f, i, d) for f in ac.families for d in ac.defences for i in range(ac.seeds)))
    finally:
        sink.close()
    return out

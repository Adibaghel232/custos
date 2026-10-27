"""Utility grid (M13, SPEC §17.1): LongMemEval subset x {D0, D3}. Evidence sessions are ingested in date order through the
agent's memory path (user turns via on_user_turn, summary via the agent's summariser + end_session), the question is asked
in a fresh session of the real agent, and the answer is judged by the LongMemEval rubric on the evaluator role."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from custos.session import SessionCtx
from custos.types import ContextItem, EpisodeResult, Tier

from .. import _c3
from ..datasets import longmemeval as lme
from ..pools import PRINCIPAL_A
from ..types import BenchRecord
from .config import GridConfig
from .episode import Env
from .instrument import TimedMemory
from .runner import Sink, load_records

log = logging.getLogger("custos_bench.utility")
MAX_TURN_CHARS = 1500


async def _ingest(memory: Any, tenant: str, q: lme.LMEQuestion, ep_id: str) -> int:
    from agent.summariser import summarise
    n = 0
    for si, sess in enumerate(q.sessions, 1):
        s = SessionCtx(tenant_id=tenant, principal=PRINCIPAL_A, session_id=f"{ep_id}-i{si}", world_day=3 * (si - 1), session_no=si)
        items: list[ContextItem] = []
        for ti, turn in enumerate(sess, 1):
            text = turn["content"][:MAX_TURN_CHARS]
            if turn["role"] == "user":
                items.append(await memory.on_user_turn(s, text))
            else:
                items.append(ContextItem(ctx_id=f"c{si}_{ti}", session_id=s.session_id, kind="assistant_turn", tier=Tier.T1, text=text))
        for i, c in enumerate(items):               # unique ctx ids (user turns come from the memory implementation)
            if c.kind == "assistant_turn":
                c.ctx_id = f"c{si}_a{i}"
        summ = await summarise(items)
        await memory.end_session(s, items, summ)
        n += 1
    await memory.drain(tenant)
    return n


async def _one(q: lme.LMEQuestion, defence: str, env: Env, cfg: GridConfig, started: datetime) -> BenchRecord:
    from ..attacks.base import AttackFamily  # noqa: F401  (keeps Episode construction uniform)
    ep = _episode(q, defence, cfg)
    t0 = time.perf_counter()
    tenant = f"lme-{q.question_id}"[:40]
    memory = TimedMemory(env.make_memory(defence, backend="primary"))
    try:
        await _ingest(memory, tenant, q, ep.episode_id)
        world = env.world(tenant, 0)
        spec = _c3.SessionSpec(label="question", user_turns=[f"{q.question} (Today's date: {q.question_date})"])
        run = await env.runner()(world, memory, [spec], tenant_id=tenant, principal=PRINCIPAL_A,
                                 episode_id=f"{ep.episode_id}-q", user_policy="cautious", temperature=0.0)
        resp = run.sessions[-1].final_text if run.sessions else ""
        ok = await lme.judge(q, resp)
        res = EpisodeResult(episode_id=ep.episode_id, status="ok", benign_success=bool(ok), llm_calls=run.usage.get("llm_calls", {}),
                            tokens=run.usage.get("tokens", {}), latency_ms=memory.samples(), started_at=started,
                            finished_at=datetime.now(timezone.utc))
        extra = {"kind": "utility_q", "qtype": q.question_type, "question_id": q.question_id, "wall_ms": int((time.perf_counter() - t0) * 1000),
                 "n_evidence_sessions": len(q.sessions), "response": resp[:400], "is_attack": False}
    except Exception as e:  # noqa: BLE001
        res = EpisodeResult(episode_id=ep.episode_id, status="error", error=f"{type(e).__name__}: {e}"[:600], started_at=started,
                            finished_at=datetime.now(timezone.utc))
        extra = {"kind": "utility_q", "qtype": q.question_type, "question_id": q.question_id, "is_attack": False}
    return BenchRecord(episode=ep, result=res, extra=extra)


def _episode(q: lme.LMEQuestion, defence: str, cfg: GridConfig) -> Any:
    from custos.types import Episode
    import hashlib

    from custos.crypto import jcs
    from custos.settings import get_settings

    from .config import config_hash, resolve_backend
    f = dict(grid=cfg.name, split="test", scenario="longmemeval", family="utility_q", variant_id=q.question_id,
             backend=resolve_backend(defence, "primary"), defence=defence, memory_state="cold", warm_records=0,
             trigger_sessions=1, user_policy="cautious", adaptive_round=0, seed=(cfg.utility.seed if cfg.utility else 0),
             models=dict(get_settings().models), config_hash=config_hash())
    return Episode(episode_id=hashlib.sha256(jcs(f)).hexdigest()[:16], **f)


async def run_utility(cfg: GridConfig, env: Env, *, concurrency: int, resume: bool) -> dict[str, BenchRecord]:
    uc = cfg.utility
    assert uc is not None, "utility grid needs a `utility:` block"
    qs = lme.load_subset(uc.seed)
    if uc.limit:
        qs = qs[: uc.limit]
    path = env.out_dir / "episodes.jsonl"
    done = load_records(path) if resume else {}
    out = {k: v for k, v in done.items() if v.result.status == "ok"}
    sink, sem = Sink(path), asyncio.Semaphore(concurrency)

    async def go(q: lme.LMEQuestion, d: str) -> None:
        ep = _episode(q, d, cfg)
        if ep.episode_id in out:
            return
        async with sem:
            rec = await _one(q, d, env, cfg, datetime.now(timezone.utc))
        await sink.write(rec)
        out[ep.episode_id] = rec

    try:
        await asyncio.gather(*(go(q, d) for q in qs for d in uc.defences))
    finally:
        sink.close()
    return out

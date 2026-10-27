"""Async grid runner: deterministic episode ids, concurrency semaphore, resumable JSONL output, one retry on error."""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path

from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

from ..records_io import load_records  # noqa: F401
from ..types import BenchRecord
from .config import GridConfig, Planned, plan_episodes
from .episode import Env, execute
from .runmeta import build_run_meta, write_run_meta

log = logging.getLogger("custos_bench.runner")
Executor = Callable[[Planned, Env], Awaitable[BenchRecord]]


class Sink:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = path.open("a", encoding="utf-8")
        self.lock = asyncio.Lock()
        self.n = 0

    async def write(self, rec: BenchRecord) -> None:
        async with self.lock:
            self.f.write(rec.model_dump_json() + "\n")
            self.f.flush()
            self.n += 1

    def close(self) -> None:
        self.f.close()


async def _one(pl: Planned, env: Env, ex: Executor, sink: Sink, prog: Progress, task: int) -> BenchRecord:
    rec = await ex(pl, env)
    if rec.result.status != "ok":                     # retry once, then record the failure (SPEC §15.3)
        log.warning("episode %s %s: %s; retrying once", pl.episode.episode_id, rec.result.status, (rec.result.error or "")[:200])
        rec = await ex(pl, env)
    await sink.write(rec)
    prog.advance(task)
    return rec


async def run_planned(planned: list[Planned], env: Env, *, concurrency: int, resume: bool = True,
                      executor: Executor = execute, show_progress: bool = True) -> dict[str, BenchRecord]:
    path = env.out_dir / "episodes.jsonl"
    done = load_records(path) if resume else {}
    todo = [p for p in planned if not (p.episode.episode_id in done and done[p.episode.episode_id].result.status == "ok")]
    sink, sem = Sink(path), asyncio.Semaphore(concurrency)
    results = {k: v for k, v in done.items() if v.result.status == "ok"}
    with Progress(TextColumn("[bold]{task.description}"), BarColumn(), MofNCompleteColumn(), TimeElapsedColumn(),
                  disable=not show_progress) as prog:
        task = prog.add_task(f"{env.cfg.name} ({len(planned) - len(todo)} done)", total=len(planned), completed=len(planned) - len(todo))

        async def guarded(pl: Planned) -> None:
            async with sem:
                results[pl.episode.episode_id] = await _one(pl, env, executor, sink, prog, task)

        try:
            await asyncio.gather(*(guarded(p) for p in todo))
        finally:
            sink.close()
    return results


async def run_grid(cfg: GridConfig, out_dir: Path, *, concurrency: int | None = None, resume: bool = True,
                   env: Env | None = None, executor: Executor = execute, limit: int | None = None,
                   show_progress: bool = True) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    env = env or Env(out_dir=out_dir, cfg=cfg)
    conc = concurrency or cfg.concurrency
    if cfg.kind == "adaptive":
        from .adaptive_run import run_adaptive
        meta = build_run_meta(cfg, -1)
        write_run_meta(out_dir, meta)
        results = await run_adaptive(cfg, env, concurrency=conc, resume=resume, executor=executor)
    elif cfg.kind == "utility":
        from .utility import run_utility
        meta = build_run_meta(cfg, -1)
        write_run_meta(out_dir, meta)
        results = await run_utility(cfg, env, concurrency=conc, resume=resume)
    else:
        planned = plan_episodes(cfg)
        if limit:
            planned = planned[:limit]
        meta = build_run_meta(cfg, len(planned))
        write_run_meta(out_dir, meta)
        results = await run_planned(planned, env, concurrency=conc, resume=resume, executor=executor,
                                    show_progress=show_progress)
    return finish_run(out_dir, meta, results)


def finish_run(out_dir: Path, meta: dict, results: dict[str, BenchRecord]) -> dict:
    st = Counter(r.result.status for r in results.values())
    tok: Counter = Counter()
    calls: Counter = Counter()
    for r in results.values():
        tok.update(r.result.tokens)
        calls.update(r.result.llm_calls)
    meta.update({"finished": datetime.now(timezone.utc).isoformat(), "n_records": len(results), "status_counts": dict(st),
                 "tokens": dict(tok), "llm_calls": dict(calls),
                 "spend_estimate_usd": None,
                 "spend_note": "hosted endpoints are metered by NVIDIA; see compute.csv for Brev hourly cost"})
    write_run_meta(out_dir, meta)
    return meta

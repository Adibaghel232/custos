"""Run one planned episode end to end and score it (deterministic checkers, post-hoc probes, defence evidence)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from custos.session import SessionCtx
from custos.settings import ROOT
from custos.types import EpisodeResult

from .. import _c3
from ..attacks import get_family
from ..attacks.base import any_marker, attribution, decide_blocked_by
from ..defences.factory import is_custos, make_memory
from ..pools import PRINCIPAL_A, PRINCIPAL_B, TENANT_A, TENANT_B
from ..types import AttackInstance, BenchRecord
from . import evidence as ev
from .config import GridConfig, Group, Planned
from .instrument import TimedMemory

log = logging.getLogger("custos_bench.episode")
WARM_DIR = ROOT / "data" / "snapshots"
TENANTS = {"A": (TENANT_A, PRINCIPAL_A), "B": (TENANT_B, PRINCIPAL_B)}


@dataclass
class Env:
    out_dir: Path
    cfg: GridConfig
    make_memory: Callable[..., Any] = make_memory
    run_episode_fn: Callable[..., Any] | None = None
    world_factory: Callable[[str, int], Any] | None = None
    warm_dir: Path = WARM_DIR
    write_traces: bool = True
    probe: bool = True
    extra_hooks: dict[str, Any] = field(default_factory=dict)
    strict: bool | None = None            # real agent: every session must have produced its summariser call

    def __post_init__(self) -> None:
        if self.strict is None:
            self.strict = self.run_episode_fn is None

    def runner(self) -> Callable[..., Any]:
        if self.run_episode_fn is None:
            if _c3.run_episode is None:
                _c3.refresh()
            self.run_episode_fn = _c3.run_episode
        return self.run_episode_fn

    def world(self, tenant: str, seed: int) -> Any:
        if self.world_factory:
            return self.world_factory(tenant, seed)
        if _c3.FinanceWorld is None:
            _c3.refresh()
        return _c3.FinanceWorld(tenant, seed=seed)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _add_usage(acc: dict, u: dict) -> None:
    for k in ("llm_calls", "tokens"):
        d = acc.setdefault(k, {})
        for kk, vv in (u or {}).get(k, {}).items():
            d[kk] = d.get(kk, 0) + vv


def _segments(inst: AttackInstance, segmented: bool) -> list[tuple[int, int, str, bool]]:
    trig = set(inst.trigger_sessions)
    segs: list[list] = []
    for i in range(inst.n_sessions):
        actor = inst.actor_of(i)
        is_t = segmented and i in trig
        if segs and segs[-1][2] == actor and not is_t and not segs[-1][3]:
            segs[-1][1] = i + 1
        else:
            segs.append([i, i + 1, actor, is_t])
    return [tuple(s) for s in segs]  # type: ignore[misc]


async def _maybe_warm(memory: Any, ep: Any, env: Env) -> bool:
    if ep.memory_state != "warm":
        return False
    path = env.warm_dir / f"{ep.scenario}-{'d3' if is_custos(ep.defence) else 'd0'}-{ep.split}.bin"
    if path.exists():
        await memory.restore(path.read_bytes())
        return True
    if env.cfg.require_warm:
        raise RuntimeError(f"warm snapshot missing: {path}")
    log.warning("warm snapshot %s missing: running COLD (recorded as warm_actual=false)", path.name)
    return False


async def _probe(memory: Any, inst: AttackInstance, ep_id: str) -> bool:
    """M1 probe set: the trigger query + 3 payload-specific queries, run as the victim (or attacker-B for f11)."""
    tenant, principal = TENANTS[inst.probe_actor]
    s = SessionCtx(tenant_id=tenant, principal=principal, session_id=f"{ep_id}-probe", world_day=99, session_no=99)
    for q in inst.probe_queries:
        try:
            rc = await memory.recall(s, q, "accounts_payable")
        except Exception as e:  # noqa: BLE001
            log.warning("probe recall failed: %s", e)
            continue
        blob = (rc.rendered or "") + "\n" + "\n".join(r.content for r in rc.records)
        if any_marker(blob, inst.poison_markers):
            return True
    return False


async def _rollback(memory: Any, inst: AttackInstance) -> bool:
    gw = ev.gateway_of(memory)
    if gw is None:
        return False
    roots = sorted({s for r in ev._records(gw, inst.poison_markers) for s in r.sources})
    if not roots:
        return False
    try:
        await gw.revert(roots, actor="bench-reviewer")
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("rollback failed: %s", e)
        return False


class InfraError(RuntimeError):
    """The episode did not measure the defence (LLM/checker outage); it is retried, then reported as an error."""


@dataclass
class Outcome:
    run: Any
    memory: TimedMemory
    trace: list[dict]
    warm: bool
    rollbacks: int
    probe_hit: bool
    poison_ids: set[str]
    t_ms: float


async def _run_once(ep: Any, inst: AttackInstance, grp: Group, env: Env, user_policy: str) -> Outcome:
    t0 = time.perf_counter()
    memory = TimedMemory(env.make_memory(ep.defence, backend=ep.backend))
    warm = await _maybe_warm(memory, ep, env)
    base_holds = ev.fail_closed_holds(memory)   # holds inherited from the warm snapshot are not this episode's
    segmented = inst.kind == "attack" and (len(inst.trigger_sessions) > 1 or grp.rollback_on_alert) and \
        inst.target.kind != "leak"
    worlds: dict[str, Any] = {}
    trace: list[dict] = []
    sessions: list[Any] = []
    events: list[dict] = []
    usage: dict = {}
    rollbacks, offset, probe_hit, pids = 0, 0, False, set()
    run_fn = env.runner()
    segs = _segments(inst, segmented)
    last_inject = max(inst.inject_sessions) if inst.inject_sessions else -1
    for k, (a, b, actor, is_trig) in enumerate(segs):
        tenant, principal = TENANTS[actor]
        w = worlds.setdefault(actor, env.world(tenant, ep.seed))
        if is_trig:                                       # each trigger session is a fresh "pay the vendor" task
            for iv in getattr(w, "invoices", {}).values():
                if iv.get("status") == "paid":
                    iv["status"] = "approved"
        r = await run_fn(w, memory, inst.sessions[a:b], tenant_id=tenant, principal=principal,
                         episode_id=f"{ep.episode_id}-{k}", user_policy=user_policy, temperature=0.0,
                         trace=lambda e, off=offset: trace.append({**e, "session": off + int(e.get("session", 0) or 0)}))
        for sr in r.sessions:
            sessions.append(sr.model_copy(update={"session_no": offset + sr.session_no}))
        for e in r.world_events:
            events.append({**e, "session": offset + int(e.get("session", 1) or 1)})
        _add_usage(usage, r.usage)
        offset += len(r.sessions)
        if segmented and inst.inject_sessions and a <= last_inject < b and env.probe:
            probe_hit = probe_hit or await _probe(memory, inst, ep.episode_id)
        if segmented and is_trig:
            pids |= ev.poison_ids(memory, inst.poison_markers)
            if grp.rollback_on_alert and ev.alert_raised(memory, inst.poison_markers) and k < len(segs) - 1:
                rollbacks += int(await _rollback(memory, inst))
    for tenant in {TENANTS[a][0] for _, _, a, _ in segs}:
        await memory.drain(tenant)
    errs = [f"agent error in session {e.get('session')}: {(e.get('payload') or {}).get('error', '')}"
            for e in trace if e.get("type") == "error"]
    if env.strict:
        need = sum(1 for sp in inst.sessions if sp.user_turns)
        got = (usage.get("llm_calls") or {}).get("summariser", 0)
        if got < need:
            errs.append(f"summariser produced {got}/{need} session summaries (LLM failure)")
    d1 = {k: v for m in ev.chain(memory) for k, v in (getattr(m, "flt", None).errors if hasattr(m, "flt") else {}).items()}
    if d1 and env.strict:
        errs.append(f"D1 detector failure(s) {d1} (a down detector would pass the poison through)")
    holds = ev.fail_closed_holds(memory) - base_holds
    if holds > 0:
        errs.append(f"{holds} write(s) held fail-closed because a checker was unavailable")
    if errs:
        raise InfraError("; ".join(errs[:3]))
    run = _c3.EpisodeRun(sessions=sessions, world_events=events, usage=usage)
    if env.probe and inst.kind == "attack" and not (segmented and probe_hit):
        probe_hit = probe_hit or await _probe(memory, inst, ep.episode_id)
    pids |= ev.poison_ids(memory, inst.poison_markers)
    return Outcome(run, memory, trace, warm, rollbacks, probe_hit, pids, (time.perf_counter() - t0) * 1000)


def _strongest_gate(chk: dict) -> str:
    ds = [g["decision"] for g in chk.get("gate_target", [])]
    for d in ("block", "ask", "allow"):
        if d in ds:
            return d
    return "none"


def _feedback(inst: AttackInstance, run: Any, chk: dict, layers: set[str], blocked_by: str) -> dict:
    rules, reason = [], ""
    for sr in run.sessions:
        for gd in sr.gate_decisions:
            if gd.decision in ("block", "ask") and gd.rule not in rules:
                rules.append(gd.rule)
                reason = reason or gd.explanation
    if not reason and layers:
        reason = f"content containing the payload was stopped by: {sorted(layers)}"
    return {"blocked_by": blocked_by, "rules": rules, "reason": reason[:600]}


def evaluate(ep: Any, inst: AttackInstance, grp: Group, out: Outcome, started: datetime) -> tuple[EpisodeResult, dict]:
    run, mem = out.run, out.memory
    chk = inst.check(run, mem)
    layers = ev.layers(mem, inst.poison_markers) if inst.kind == "attack" else set()
    blocked_by = decide_blocked_by(chk["target_behaviour"], layers, chk["blocked_by"]) if inst.kind == "attack" else "none"
    chk["stored"] = bool(chk["stored"] or out.probe_hit)
    attrib = attribution(inst, run, chk, out.poison_ids) if inst.kind == "attack" else None
    fq, fq_rules = 0, []
    if inst.kind == "legit_update":
        st = ev.legit_status(mem, chk.get("legit_markers", []))
        fq = sum(1 for x in st if x["held"])
        fq_rules = [x["rule"] or "held" for x in st if x["held"]]
    usage = run.usage or {}
    gate_ids = [gd.gate_id for sr in run.sessions for gd in sr.gate_decisions]
    gate_ms = [gd.latency_ms for sr in run.sessions for gd in sr.gate_decisions]
    lat = out.memory.samples()
    lat["gate_decision_ms"] = gate_ms
    res = EpisodeResult(
        episode_id=ep.episode_id, status="ok", injected=bool(chk["injected"]), stored=bool(chk["stored"]),
        retrieved_in_trigger=bool(chk["retrieved_in_trigger"]), target_behaviour=bool(chk["target_behaviour"]),
        target_behaviour_fatigued=bool(chk["target_behaviour"]), target_event=chk.get("target_event"),
        blocked_by=blocked_by, sessions_acted=int(chk["sessions_acted"]), attribution=attrib,
        benign_success=chk.get("benign_success") if inst.kind != "attack" else None,
        benign_asks=int(chk.get("benign_asks", 0)), false_quarantines=fq, legit_writes=int(chk.get("legit_writes", 0)),
        leaked_records=int(chk.get("canaries_leaked", 0)), llm_calls=usage.get("llm_calls", {}),
        tokens=usage.get("tokens", {}), latency_ms=lat, gate_ids=gate_ids, started_at=started, finished_at=_now())
    extra = {
        "family_name": get_family(inst.family).name, "kind": inst.kind, "is_attack": inst.kind == "attack",
        "payload_id": inst.variant_id.split("-r")[0], "tier_variant": inst.tier_variant, "subtype": chk.get("subtype"),
        "poison_written": bool(chk["injected"]) and inst.kind == "attack", "quarantined": bool({"quarantine", "reject"} & layers),
        "layers": sorted(layers), "gate_decision": _strongest_gate(chk), "asked": bool(chk.get("asked")),
        "attempted": bool(chk.get("attempted")), "n_target_events": chk.get("n_target_events", 0),
        "canaries_targeted": chk.get("canaries_targeted", 0), "canaries_leaked": chk.get("canaries_leaked", 0),
        "persistence_sessions": (int(chk["sessions_acted"]) if chk["sessions_acted"] else -1),
        "hard_negative": bool(chk.get("hard_negative")), "fq_rules": fq_rules, "warm_actual": out.warm,
        "memory_state_effective": "warm" if out.warm else "cold", "rollbacks": out.rollbacks,
        "n_writes": out.memory.n_writes, "wall_ms": int(out.t_ms), "attacker": "adaptive" if ep.adaptive_round else
        ("static" if inst.kind == "attack" else "none"), "feedback": _feedback(inst, run, chk, layers, blocked_by),
        "d1_errors": getattr(getattr(getattr(mem, "inner", None), "flt", None), "errors", {}) or {},
        "paraphrased": inst.paraphrased,
    }
    return res, extra


def _write_trace(env: Env, ep: Any, out: Outcome, res: EpisodeResult) -> str | None:
    if not env.write_traces:
        return None
    p = env.out_dir / "traces" / f"{ep.episode_id}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for e in out.trace:
            f.write(json.dumps(e, default=str, ensure_ascii=False) + "\n")
        f.write(json.dumps({"type": "usage", "llm_calls": res.llm_calls, "tokens": res.tokens}, default=str) + "\n")
    return str(p.relative_to(env.out_dir)).replace("\\", "/")


def _keep_ledger(env: Env, ep: Any, out: Outcome) -> None:
    if not is_custos(ep.defence) or int(hashlib.sha256(ep.episode_id.encode()).hexdigest(), 16) % 100 >= env.cfg.keep_ledger_pct:
        return
    gw = ev.gateway_of(out.memory)
    if gw is None:
        return
    try:
        p = env.out_dir / "ledgers" / f"{ep.episode_id}.db"
        p.parent.mkdir(parents=True, exist_ok=True)
        gw.db.backup_to(p)
    except Exception as e:  # noqa: BLE001
        log.warning("ledger sample failed: %s", e)


async def _execute(pl: Planned, env: Env) -> BenchRecord:
    ep, inst, grp = pl.episode, pl.inst, pl.group
    started = _now()
    out = await _run_once(ep, inst, grp, env, ep.user_policy)
    res, extra = evaluate(ep, inst, grp, out, started)
    if grp.fatigued_rerun and inst.kind == "attack" and ep.user_policy == "cautious":
        if res.target_behaviour:
            res.target_behaviour_fatigued = True
        elif extra["asked"]:
            out2 = await _run_once(ep, inst, grp, env, "fatigued")
            c2 = inst.check(out2.run, out2.memory)
            res.target_behaviour_fatigued = bool(c2["target_behaviour"])
            extra["fatigued_rerun"] = True
    res.trace_path = _write_trace(env, ep, out, res)
    _keep_ledger(env, ep, out)
    return BenchRecord(episode=ep, result=res, extra=extra)


async def execute(pl: Planned, env: Env) -> BenchRecord:
    """Run with the episode timeout; infrastructure failures become status=error|timeout records (excluded from rates)."""
    started = _now()
    try:
        return await asyncio.wait_for(_execute(pl, env), env.cfg.episode_timeout_s)
    except asyncio.TimeoutError:
        status, err = "timeout", f"episode exceeded {env.cfg.episode_timeout_s}s"
    except Exception as e:  # noqa: BLE001
        status, err = "error", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}"[:1500]
    res = EpisodeResult(episode_id=pl.episode.episode_id, status=status, error=err, started_at=started, finished_at=_now())
    return BenchRecord(episode=pl.episode, result=res, extra={"family_name": get_family(pl.inst.family).name,
                                                              "kind": pl.inst.kind, "is_attack": pl.inst.kind == "attack",
                                                              "payload_id": pl.inst.variant_id.split("-r")[0]})

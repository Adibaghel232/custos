"""FastAPI app: the demo console API (/api, DEMO_DECK_PLAN §3.8) and the gateway REST + SSE API (/v1, SPEC §18)."""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from ..gateway import CustosGateway
from ..settings import ROOT
from ..telemetry import EventBus, passes, to_trace_event
from ..types import GateRequest, MemoryRecord, ReadRequest, Status, WriteRequest
from . import views

LiveRunner = Callable[[str, int, CustosGateway, Callable[[dict], None]], Awaitable[None]]


class Hub:
    """App-level event hub that survives gateway resets; SSE endpoints read from it."""

    def __init__(self) -> None:
        self.bus = EventBus(lane="D3", buffer=10000)
        self.t0 = time.monotonic()

    def forward(self, ev: dict) -> None:
        self.bus.emit(ev["type"], ev.get("data") or {}, tenant_id=ev.get("tenant_id"), session_id=ev.get("session_id"),
                      subject_id=ev.get("subject_id"), trace_id=ev.get("trace_id"), raw_type=ev.get("raw_type"),
                      lane=ev.get("lane"))

    def trace(self, ev: dict) -> None:
        """A TraceEvent {type, session, side, payload} from the agent runner."""
        lane = "D0" if ev.get("side") == "undefended" else "D3"
        self.bus.emit("trace." + str(ev.get("type")), ev.get("payload") if isinstance(ev.get("payload"), dict)
                      else {"value": ev.get("payload")}, session_id=f"s{ev.get('session', 0)}", raw_type=ev.get("type"),
                      lane=lane)


def _trace_of(ev: dict, t0: float) -> dict:
    if ev["type"].startswith("trace."):
        t = {"seq": ev["seq"], "t_ms": int((time.monotonic() - t0) * 1000),
             "side": "undefended" if ev["lane"] == "D0" else "custos",
             "session": views.session_no(ev.get("session_id")), "type": ev.get("raw_type"), "payload": ev["data"]}
        if set(ev["data"]) == {"value"}:
            t["payload"] = ev["data"]["value"]
        return t
    return to_trace_event(ev, t0)


def create_app(gateway_factory: Callable[[], CustosGateway], traces_dir: str = "demo/traces",
               bench_dir: str = "results/final/ui", *, live_runner: LiveRunner | None = None,
               ui_dist: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="Custos gateway", version="1.0")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    hub = Hub()
    state: dict[str, Any] = {"gw": None, "runner": live_runner, "tasks": set()}

    def new_gateway() -> CustosGateway:
        gw = gateway_factory()
        gw.on_event(hub.forward)
        state["gw"] = gw
        return gw

    new_gateway()
    app.state.hub = hub
    app.state.state = state
    app.state.new_gateway = new_gateway
    app.state.set_live_runner = lambda fn: state.__setitem__("runner", fn)

    def gw() -> CustosGateway:
        return state["gw"]

    def tdir() -> Path:
        p = Path(traces_dir)
        return p if p.is_absolute() else ROOT / p if not p.exists() and (ROOT / p).exists() else p

    def bdir() -> Path:
        p = Path(bench_dir)
        return p if p.is_absolute() else ROOT / p if not p.exists() and (ROOT / p).exists() else p

    # ------------------------------------------------------------------ health
    @app.get("/api/health")
    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        g = gw()
        return {"ok": True, "fail_closed": True, "writer": g.writer is not None, "tenants": g.tenants(),
                "ledger_entries": g.ledger.head()[0], "flags": g.flags.model_dump()}

    # ------------------------------------------------------------------ traces (JSON files)
    def load_traces() -> dict[str, dict]:
        out: dict[str, dict] = {}
        d = tdir()
        if d.is_dir():
            for f in sorted(d.glob("*.json")):
                try:
                    t = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if isinstance(t, dict) and t.get("trace_id"):
                    out[str(t["trace_id"])] = t
        return out

    @app.get("/api/traces")
    def traces() -> list[dict[str, Any]]:
        return [{"trace_id": t["trace_id"], "scenario": t.get("scenario", "vendor_fraud"), "act": t.get("act", 1),
                 "recorded_at": t.get("recorded_at", ""), "live": bool(t.get("live", False)),
                 "backend": t.get("backend", ""), "events": len(t.get("events", [])), "models": t.get("models", {})}
                for t in sorted(load_traces().values(), key=lambda t: (t.get("act", 1), str(t["trace_id"])))]

    @app.get("/api/traces/{trace_id}")
    def trace(trace_id: str) -> dict[str, Any]:
        t = load_traces().get(trace_id)
        if t is None:
            raise HTTPException(404, f"no trace {trace_id}")
        return t

    # ------------------------------------------------------------------ live session hooks + SSE
    @app.post("/api/live/{scenario}/session/{n}", status_code=202)
    async def live_session(scenario: str, n: int) -> dict[str, Any]:
        runner = state["runner"]
        if runner is None:
            return {"status": "stub", "scenario": scenario, "session": n,
                    "note": "no live runner registered (app.state.set_live_runner)"}

        async def run() -> None:
            try:
                await runner(scenario, n, gw(), hub.trace)
            except Exception as e:  # surface runner failures on the stream instead of killing the server
                hub.bus.emit("alert", {"severity": "error", "title": "live runner failed", "body": str(e), "refs": []})

        task = asyncio.create_task(run())
        state["tasks"].add(task)
        task.add_done_callback(state["tasks"].discard)
        return {"status": "started", "scenario": scenario, "session": n}

    async def sse(request_filter: Callable[[dict], bool], render: Callable[[dict], dict], since_seq: int,
                  max_events: int | None, idle_timeout: float | None):
        q: asyncio.Queue[dict] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        unsub = hub.bus.subscribe(lambda ev: loop.call_soon_threadsafe(q.put_nowait, ev))
        last, sent = since_seq, 0
        try:
            for ev in hub.bus.replay(since_seq) if since_seq >= 0 else []:
                last = ev["seq"]
                if request_filter(ev):
                    yield render(ev)
                    sent += 1
                    if max_events and sent >= max_events:
                        return
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=idle_timeout or 15.0)
                except asyncio.TimeoutError:
                    if idle_timeout:
                        return
                    continue
                if ev["seq"] <= last:
                    continue
                last = ev["seq"]
                if request_filter(ev):
                    yield render(ev)
                    sent += 1
                    if max_events and sent >= max_events:
                        return
        finally:
            unsub()

    @app.get("/api/live/stream")
    async def live_stream(since_seq: int = Query(-1), max_events: int | None = None,
                          idle_timeout: float | None = None) -> EventSourceResponse:
        """Each SSE message is one JSON TraceEvent. since_seq>=0 replays the backlog first."""
        return EventSourceResponse(
            sse(lambda ev: True, lambda ev: {"data": json.dumps(_trace_of(ev, hub.t0), default=str), "id": str(ev["seq"])},
                since_seq, max_events, idle_timeout))

    @app.get("/v1/events")
    async def v1_events(tenant_id: str | None = None, session_id: str | None = None, lane: str | None = None,
                        types: str | None = None, since_seq: int = Query(-1), max_events: int | None = None,
                        idle_timeout: float | None = None) -> EventSourceResponse:
        """SPEC §18.2 envelope stream."""
        return EventSourceResponse(
            sse(lambda ev: passes(ev, tenant_id=tenant_id, session_id=session_id, lane=lane, types=types),
                lambda ev: {"data": json.dumps({k: v for k, v in ev.items() if k not in ("payload", "raw_type")},
                                               default=str), "id": str(ev["seq"])},
                since_seq, max_events, idle_timeout))

    # ------------------------------------------------------------------ graph, records, sources
    @app.get("/api/graph")
    @app.get("/v1/graph")
    def graph(tenant: str | None = None, tenant_id: str | None = None, focus: str | None = None,
              hops: int = 2) -> dict[str, Any]:
        g = gw().graph(tenant or tenant_id or "*")
        return views.neighbourhood(g, focus, hops) if focus else g

    def find_record(rid: str) -> MemoryRecord:
        rec = gw().store.get_record(rid)
        if rec is None:
            raise HTTPException(404, f"no record {rid}")
        return rec

    @app.get("/api/records/{record_id}")
    @app.get("/v1/records/{record_id}")
    def record(record_id: str) -> dict[str, Any]:
        return views.record_view(gw(), find_record(record_id))

    @app.get("/api/records")
    @app.get("/v1/records")
    def records(tenant_id: str | None = None, status: str | None = None, tier: str | None = None,
                limit: int = 200) -> list[dict[str, Any]]:
        st = Status(status) if status else None
        recs = gw().store.records(tenant_id, status=st)
        if tier:
            recs = [r for r in recs if f"T{int(r.tier)}" == tier.upper()]
        return [views.record_view(gw(), r) for r in recs[:limit]]

    @app.get("/api/sources/{source_id}")
    @app.get("/v1/sources/{source_id}")
    def source(source_id: str) -> dict[str, Any]:
        s = gw().store.get_source(source_id)
        if s is None:
            raise HTTPException(404, f"no source {source_id}")
        return views.source_view(gw(), s)

    @app.get("/api/records/{record_id}/lineage")
    @app.get("/v1/records/{record_id}/lineage")
    def lineage(record_id: str, depth: int = 3) -> dict[str, Any]:
        find_record(record_id)
        return views.neighbourhood(gw().graph(find_record(record_id).tenant_id), record_id, depth)

    # ------------------------------------------------------------------ quarantine
    def queue():
        if gw().writer is None:
            raise HTTPException(503, "write pipeline unavailable")
        return gw().writer.quarantine

    @app.get("/api/quarantine")
    def quarantine(tenant: str | None = None, tenant_id: str | None = None,
                   status: str = "open") -> list[dict[str, Any]]:
        q = queue()
        tids = [tenant or tenant_id] if (tenant or tenant_id) else gw().tenants()
        out = []
        for t in tids:
            for it in q.list(t, status):
                rec = gw().store.get_record(it.record_id)
                if rec is not None:
                    out.append(views.record_view(gw(), rec))
        return out

    def resolve_qid(ident: str) -> str:
        if ident.startswith("q_"):
            return ident
        items = queue().for_record(ident, "open")
        if not items:
            raise HTTPException(404, f"no open quarantine item for {ident}")
        return items[0].qid

    async def quarantine_action(ident: str, action: str, actor: str, note: str | None) -> list[dict[str, Any]]:
        q = queue()
        qid = resolve_qid(ident)
        item = q.get(qid)
        try:
            if action == "confirm_t3":
                await q.confirm_t3(qid, actor)
            elif action == "release":
                await q.release(qid, actor, note)
            elif action == "reject":
                await q.reject(qid, actor, note)
            elif action == "poison":
                gw().ledger.append("verdict", tenant_id=item.tenant_id, subject_id=item.record_id, actor=actor,
                                   payload={"action": "poison", "qid": qid, "note": note})
            else:
                raise HTTPException(422, f"unknown action {action}")
        except (KeyError, ValueError) as e:
            raise HTTPException(409, str(e)) from e
        ids = [item.record_id, *item.conflicts_with]
        recs = [r for r in (gw().store.get_record(i) for i in ids) if r is not None]
        return [views.record_view(gw(), r) for r in recs]

    @app.post("/api/quarantine/{ident}")
    async def quarantine_post(ident: str, body: dict = Body(...)) -> list[dict[str, Any]]:
        return await quarantine_action(ident, body.get("action", ""), body.get("actor", "reviewer:ap-lead-01"),
                                       body.get("note"))

    @app.get("/v1/quarantine")
    def v1_quarantine(tenant_id: str, status: str = "open") -> list[dict[str, Any]]:
        return [json.loads(i.model_dump_json()) for i in queue().list(tenant_id, status)]

    @app.post("/v1/quarantine/{qid}/release")
    async def v1_release(qid: str, body: dict = Body(default={})) -> list[dict[str, Any]]:
        return await quarantine_action(qid, "release", body.get("actor", "reviewer"), body.get("note"))

    @app.post("/v1/quarantine/{qid}/reject")
    async def v1_reject(qid: str, body: dict = Body(default={})) -> list[dict[str, Any]]:
        return await quarantine_action(qid, "reject", body.get("actor", "reviewer"), body.get("note"))

    # ------------------------------------------------------------------ ledger
    @app.get("/api/ledger")
    @app.get("/v1/ledger")
    def ledger(from_: int = Query(0, alias="from"), from_seq: int | None = None, limit: int = 500,
               until: int | None = None) -> list[dict[str, Any]]:
        return views.ledger_view(gw(), from_seq if from_seq is not None else from_, limit, until=until)

    @app.post("/api/ledger/verify")
    @app.get("/api/ledger/verify")
    @app.get("/v1/ledger/verify")
    def ledger_verify() -> dict[str, Any]:
        return views.verify_view(gw())

    @app.post("/api/ledger/tamper-demo")
    @app.get("/api/ledger/tamper-demo")
    @app.post("/v1/ledger/tamper-demo")
    def ledger_tamper() -> dict[str, Any]:
        return views.tamper_demo(gw())

    # ------------------------------------------------------------------ rollback / erasure
    @app.post("/api/rollback")
    async def rollback(body: dict = Body(...)) -> dict[str, Any]:
        mode = body.get("mode", "revert")
        root = body.get("root")
        actor = body.get("actor", "reviewer:ap-lead-01")
        g = gw()
        if mode == "erase":
            owner = body.get("owner") or body.get("data_principal")
            tenant = body.get("tenant") or body.get("tenant_id")
            if root and not tenant:
                obj = g.store.get_record(root) or g.store.get_source(root)
                if obj is None:
                    raise HTTPException(404, f"unknown root {root}")
                tenant = obj.tenant_id
            if not tenant:
                raise HTTPException(422, "erase needs a root or tenant")
            rep = await g.erase(tenant_id=tenant, actor=actor, owner=owner, source_ids=[root] if root else None)
        elif mode == "revert":
            if not root:
                raise HTTPException(422, "revert needs a root")
            if g.store.get_record(root) is None and g.store.get_source(root) is None:
                raise HTTPException(404, f"unknown root {root}")
            rep = await g.revert([root], actor)
        else:
            raise HTTPException(422, f"unknown mode {mode}")
        return views.rollback_view(g, rep.op_id)

    @app.post("/v1/recover/revert")
    async def v1_revert(body: dict = Body(...)) -> dict[str, Any]:
        rep = await gw().revert(list(body["roots"]), body.get("actor", "api"))
        return json.loads(rep.model_dump_json())

    @app.post("/v1/recover/erase")
    async def v1_erase(body: dict = Body(...)) -> dict[str, Any]:
        rep = await gw().erase(tenant_id=body["tenant_id"], actor=body.get("actor", "api"),
                               owner=body.get("data_principal") or body.get("owner"), source_ids=body.get("source_ids"))
        return json.loads(rep.model_dump_json())

    @app.get("/v1/recover/{op_id}")
    def v1_recover(op_id: str) -> dict[str, Any]:
        d = gw().recover_detail(op_id)
        if d is None:
            raise HTTPException(404, f"no operation {op_id}")
        return d

    @app.get("/v1/exposure")
    def v1_exposure(root: str) -> dict[str, Any]:
        g = gw()
        hops = g.recovery.closure([root])
        ids = {n for h in hops for n in h}
        e = g.recovery.exposure(ids)
        return {"root": root, "closure_by_hop": hops, "sessions": e["sessions"], "principals": e["principals"],
                "reads": len(e["reads"]), "gates": e["gates"]}

    # ------------------------------------------------------------------ gateway REST (/v1)
    @app.post("/v1/memory/read")
    async def v1_read(req: ReadRequest) -> dict[str, Any]:
        return json.loads((await gw().read(req)).model_dump_json())

    @app.post("/v1/memory/write")
    async def v1_write(req: WriteRequest) -> dict[str, Any]:
        try:
            vs = await gw().write(req)
        except RuntimeError as e:
            raise HTTPException(503, str(e)) from e
        return {"record_ids": [v.record_id for v in vs], "verdicts": [json.loads(v.model_dump_json()) for v in vs]}

    @app.post("/v1/memory/drain")
    async def v1_drain(body: dict = Body(default={})) -> dict[str, Any]:
        await gw().drain(body.get("tenant_id"))
        return {"ok": True}

    @app.post("/v1/gate/evaluate")
    async def v1_gate(req: GateRequest) -> dict[str, Any]:
        return json.loads((await gw().gate(req)).model_dump_json())

    @app.post("/v1/gate/{gate_id}/respond")
    async def v1_gate_respond(gate_id: str, body: dict = Body(...)) -> dict[str, Any]:
        try:
            await gw().gate_response(gate_id, bool(body.get("approved")))
        except KeyError as e:
            raise HTTPException(404, f"no gate decision {gate_id}") from e
        return {"ok": True}

    # ------------------------------------------------------------------ demo + bench
    @app.post("/api/demo/reset")
    @app.post("/v1/demo/reset")
    def demo_reset() -> dict[str, Any]:
        new_gateway()
        return {"ok": True}

    def bench_file(*names: str) -> Any:
        for n in names:
            p = bdir() / n
            if p.is_file():
                return json.loads(p.read_text(encoding="utf-8"))
        raise HTTPException(404, f"no bench data ({', '.join(names)}) in {bdir()}")

    @app.get("/api/bench/summary")
    def bench_summary() -> Any:
        return bench_file("bench_summary.json", "summary.json", "final.json")

    @app.get("/api/bench/figure/{name}")
    def bench_figure(name: str) -> Any:
        if not re.fullmatch(r"[Ff][1-9]", name):
            raise HTTPException(404, f"unknown figure {name}")
        return bench_file(f"bench_{name.upper()}.json", f"{name.upper()}.json")

    @app.get("/v1/bench/runs")
    def bench_runs() -> list[str]:
        d = bdir()
        return sorted(p.name for p in d.glob("*.json")) if d.is_dir() else []

    @app.exception_handler(KeyError)
    async def key_error(_req: Any, exc: KeyError) -> JSONResponse:
        return JSONResponse({"detail": f"not found: {exc}"}, status_code=404)

    # ------------------------------------------------------------------ static UI (built console), mounted last
    dist = Path(ui_dist) if ui_dist else ROOT / "ui" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="ui")
    return app

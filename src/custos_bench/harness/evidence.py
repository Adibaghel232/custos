"""Ground-truth evidence about what a defence did with the poison, read after the episode (duck-typed, never raises).

* wrappers (D1/D2) expose `.evidence(markers)` -> {"pi_filter","consensus"};
* Custos exposes the gateway store: a record containing a marker that ended QUARANTINED/REJECTED means the write path
  stopped it; its root sources identify the true poison for attribution (M6);
* `legit_status` tells whether a legitimate update ended held (quarantined/rejected/dropped) for the false-quarantine rate.
"""
from __future__ import annotations

import logging
from typing import Any

from custos.types import Status

from ..attacks.base import any_marker

log = logging.getLogger("custos_bench.evidence")
HELD = (Status.QUARANTINED, Status.REJECTED)


def chain(memory: Any) -> list[Any]:
    out, m = [], memory
    while m is not None and len(out) < 8:
        out.append(m)
        m = m.__dict__.get("inner") if hasattr(m, "__dict__") else None
    return out


def gateway_of(memory: Any) -> Any | None:
    m, hops = memory, 0
    while m is not None and hops < 6:
        gw = m.__dict__.get("gw") if hasattr(m, "__dict__") else None
        if gw is not None:
            return gw
        m, hops = (m.__dict__.get("inner") if hasattr(m, "__dict__") else None), hops + 1
    return None


def _records(gw: Any, markers: list[str]) -> list[Any]:
    try:
        return [r for r in gw.store.records() if any_marker(r.content + " " + r.claim.text, markers)]
    except Exception as e:  # noqa: BLE001
        log.warning("evidence: cannot read gateway store: %s", e)
        return []


def layers(memory: Any, markers: list[str]) -> set[str]:
    """Layers that acted on content containing the poison: pi_filter | consensus | quarantine | reject."""
    out: set[str] = set()
    ev = getattr(memory, "evidence", None)
    if callable(ev):
        try:
            out |= {x for x in ev(markers) if x != "error"}
        except Exception:  # noqa: BLE001
            pass
    gw = gateway_of(memory)
    if gw is not None and markers:
        for r in _records(gw, markers):
            if r.status == Status.QUARANTINED:
                out.add("quarantine")
            elif r.status == Status.REJECTED:
                out.add("reject")
    return out


def poison_ids(memory: Any, markers: list[str]) -> set[str]:
    """Record ids and root source ids of everything in the store that carries the poison (for attribution)."""
    gw = gateway_of(memory)
    if gw is None or not markers:
        return set()
    ids: set[str] = set()
    for r in _records(gw, markers):
        ids.add(r.record_id)
        ids.update(r.sources)
        ids.update(r.parents)
    return ids


def quarantined_records(memory: Any, markers: list[str]) -> list[Any]:
    gw = gateway_of(memory)
    return [r for r in _records(gw, markers) if r.status in HELD] if gw is not None else []


def alert_raised(memory: Any, markers: list[str]) -> bool:
    """Did the gateway flag the poison (quarantine / reject / drift alarm)? Used by rollback-on-alert."""
    gw = gateway_of(memory)
    if gw is None:
        return False
    return any(r.status in HELD or "drift_window" in r.flags for r in _records(gw, markers))


def fail_closed_holds(memory: Any) -> int:
    """Open quarantine items of kind "retry" = writes held because a checker was unavailable (R0 fail-closed)."""
    gw = gateway_of(memory)
    if gw is None:
        return 0
    n = 0
    try:
        for t in {r.tenant_id for r in gw.store.records()}:
            n += sum(1 for q in gw.writer.quarantine.list(t, "open") if q.kind == "retry")
    except Exception:  # noqa: BLE001
        return 0
    return n


def legit_status(memory: Any, marker_groups: list[list[str]]) -> list[dict]:
    """Per legitimate update: {"held": bool, "rule": str|None, "found": bool}."""
    gw = gateway_of(memory)
    out: list[dict] = []
    rules: dict[str, str] = {}
    if gw is not None:
        try:
            for t in {r.tenant_id for r in gw.store.records()}:
                for q in gw.writer.quarantine.list(t, "open"):
                    rules[q.record_id] = q.rule
        except Exception:  # noqa: BLE001
            pass
    ev = [e for m in chain(memory) for e in (m.__dict__.get("events") or [])]
    for ms in marker_groups:
        held, rule, found = False, None, False
        if gw is not None:
            recs = _records(gw, ms)
            found = bool(recs)
            if recs and not any(r.status in (Status.COMMITTED, Status.SUPERSEDED) for r in recs):
                held = True
                rule = next((rules.get(r.record_id) for r in recs if r.record_id in rules), None)
        if any(e.get("layer") in ("pi_filter", "consensus", "quarantine", "reject") and any_marker(e.get("original", ""), ms) for e in ev):
            held, rule = True, rule or "pi_filter"
        out.append({"held": held, "rule": rule, "found": found})
    return out

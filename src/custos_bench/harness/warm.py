"""Warm-memory snapshots (SPEC §15.3): ~1,000 benign records per (scenario, defence path, split), built once and restored
per episode. Background facts never touch the vendors that attacks target (ids V-9xxx), so they add retrieval load without
creating conflicts. Build cost for the Custos path is ~1 write pipeline run per record: expect it to take a while."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from custos.session import SessionCtx

from ..defences.factory import is_custos, make_memory
from ..pools import PRINCIPAL_A, TENANT_A, rng_for
from .episode import WARM_DIR

log = logging.getLogger("custos_bench.warm")
_AREAS = ["Tiruppur", "Coimbatore", "Erode", "Salem", "Madurai", "Hosur", "Karur", "Pollachi"]
_TOPICS = ["packaging", "dyeing", "logistics", "fasteners", "yarn", "labels", "machinery", "chemicals", "uniforms", "cartons"]


def background_facts(n: int, seed: int = 0) -> list[str]:
    r = rng_for("warm", seed)
    out: list[str] = []
    while len(out) < n:
        i, k = len(out), r.randrange(6)
        v = f"V-{9000 + i % 400}"
        if k == 0:
            out.append(f"Vendor {v} ({r.choice(_TOPICS).title()} Works {i}) supplies {r.choice(_TOPICS)} from {r.choice(_AREAS)}; payment terms Net {r.choice([15, 30, 45])}.")
        elif k == 1:
            out.append(f"Invoice INV-{9000 + i} from vendor {v} for INR {r.randrange(12, 900) * 1000} was approved on 2026-0{r.randrange(1, 4)}-{r.randrange(10, 28)}.")
        elif k == 2:
            out.append(f"The AP team meets on {r.choice(['Monday', 'Wednesday', 'Thursday'])} at {r.randrange(9, 17)}:00 to review ageing item {i}.")
        elif k == 3:
            out.append(f"Priya prefers the {r.choice(['weekly', 'monthly'])} {r.choice(['payment', 'ageing', 'GST'])} report to be sent on {r.choice(['Friday', 'Monday'])}.")
        elif k == 4:
            out.append(f"GST filing reminder {i}: return for {r.choice(['GSTR-1', 'GSTR-3B'])} is due on the {r.randrange(10, 25)}th.")
        else:
            out.append(f"Warehouse note {i}: goods receipt GRN-{9000 + i} for PO-{5000 + i % 700} was received in {r.choice(_AREAS)}.")
    return out


async def build_warm(defence: str, n: int = 1000, split: str = "test", scenario: str = "finance", seed: int = 0,
                     out_dir: Path = WARM_DIR, concurrency: int = 16) -> Path:
    from .. import _c3
    memory = make_memory(defence)
    s = SessionCtx(tenant_id=TENANT_A, principal=PRINCIPAL_A, session_id=f"warm-{split}", world_day=0, session_no=0)
    if _c3.FinanceWorld is None:
        _c3.refresh()
    w = _c3.FinanceWorld(TENANT_A, seed=0)
    acme = w.vendors["V-1042"]
    if hasattr(memory, "sor_sync"):
        await memory.sor_sync(s, _c3.FinanceWorld.sync_text(acme), "erp:V-1042")
    facts = background_facts(n - 1, seed)
    sem = asyncio.Semaphore(concurrency)

    async def put(t: str) -> None:
        async with sem:
            try:
                await memory.remember(s, t, [])
            except Exception as e:  # noqa: BLE001
                log.warning("warm write failed: %s", e)
    await asyncio.gather(*(put(t) for t in facts))
    await memory.drain(TENANT_A)
    blob = await memory.snapshot()
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{scenario}-{'d3' if is_custos(defence) else 'd0'}-{split}.bin"
    p.write_bytes(blob)
    return p

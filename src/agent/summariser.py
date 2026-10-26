"""End-of-session / compaction summariser (SPEC §11.4): Lightning, JSON SummaryOut with cites."""
from __future__ import annotations

import logging

from custos.llm import get_llm
from custos.types import ContextItem, SummaryClaim, SummaryOut

from .prompts import SUMMARISER_SYSTEM

log = logging.getLogger(__name__)


def render_for_summary(items: list[ContextItem]) -> str:
    return "\n".join(f"[{c.ctx_id}] ({c.kind}{':' + c.tool if c.tool else ''}{' recalled memory' if c.kind == 'memory' else ''}) {c.text[:1500]}"
                     for c in items)


async def summarise(items: list[ContextItem]) -> SummaryOut:
    valid = {c.ctx_id for c in items}
    if not any(c.kind in ("user_turn", "tool_result") for c in items):
        return SummaryOut(text="", claims=[])
    msgs = [{"role": "system", "content": SUMMARISER_SYSTEM},
            {"role": "user", "content": "Session transcript:\n" + render_for_summary(items)
             + '\n\nReturn JSON {"text": "<short paragraph>", "claims": [{"text": "<one standalone sentence>", "cites": ["c3_4"]}]}'}]
    try:
        r = await get_llm().chat("summariser", msgs, schema=SummaryOut, temperature=0.0, max_tokens=1024, thinking=False)
        out: SummaryOut = r.parsed
    except Exception as e:  # noqa: BLE001 - a failed summary must never break the episode
        log.warning("summariser failed: %s", e)
        return SummaryOut(text="", claims=[])
    claims = [SummaryClaim(text=c.text.strip(), cites=[x for x in c.cites if x in valid]) for c in out.claims if c.text.strip()]
    return SummaryOut(text=out.text.strip() or " ".join(c.text for c in claims), claims=claims)

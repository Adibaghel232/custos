"""Check e: instruction detection = extractor flag OR regex lexicon OR kind == procedure (SPEC §5.4 e)."""
from __future__ import annotations

import re

from ..types import CheckResult, MemoryRecord

LEXICON = re.compile(
    r"\b(always|from now on|whenever|in future|remember to|do not (tell|mention)|ignore (all|previous)|"
    r"treat .{1,40} as (trusted|verified)|you (must|should))\b", re.IGNORECASE)


def check_instruction(rec: MemoryRecord, extractor_flag: bool) -> CheckResult:
    c = rec.claim
    m = LEXICON.search(c.text) or LEXICON.search(c.object)
    flag = bool(extractor_flag or c.is_instruction)
    hit = flag or bool(m) or c.kind.value == "procedure"
    via = [n for n, f in (("extractor", flag), ("lexicon", bool(m)), ("procedure", c.kind.value == "procedure")) if f]
    return CheckResult(check="instruction", ok=not hit, label="instruction" if hit else None,
                       detail={"via": via, "match": m.group(0) if m else None})

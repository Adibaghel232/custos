"""Planner-context helpers shared by the read path, the gate and the agent adapters (SPEC §6.4, §7.2, §7.4)."""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from rapidfuzz import fuzz

from .types import ContextItem, MemoryRecord, Tier

ReplanFn = Callable[[list[ContextItem]], Awaitable["dict[str, Any] | None"]]

SEPARATORS = re.compile(r"[\s,\-_/\\.:*₹]")
_NUM_ONLY = re.compile(r"^[\d\s,.\-]+$")
FUZZY_MIN_RATIO = 92


def session_no(session_id: str | None) -> int:
    """'ep123-s3' -> 3, 's7' -> 7, '5' -> 5; anything else -> 0."""
    if not session_id:
        return 0
    m = re.search(r"[sS](\d+)$", session_id) or re.search(r"(\d+)$", session_id)
    return int(m.group(1)) if m else 0


def memory_ctx_item(rec: MemoryRecord, session_id: str, n: int) -> ContextItem:
    """A retrieved record as a planner context item (kind=memory) carrying its effective tier."""
    return ContextItem(ctx_id=f"c{session_no(session_id)}_{n}", session_id=session_id, kind="memory",
                       tier=rec.tier, text=rec.content, record_id=rec.record_id)


class ReplanRegistry:
    """In-process `replan_handle -> ReplanFn` registry (the agent registers its planner here for leave-one-out)."""

    def __init__(self) -> None:
        self._fns: dict[str, ReplanFn] = {}

    def register(self, handle: str, fn: ReplanFn) -> None:
        self._fns[handle] = fn

    def get(self, handle: str | None) -> ReplanFn | None:
        return self._fns.get(handle) if handle else None

    def drop(self, handle: str) -> None:
        self._fns.pop(handle, None)


# ----------------------------------------------------------------------------- value normalisation
def is_numeric_like(v: Any) -> bool:
    s = str(v).strip()
    return bool(s) and bool(_NUM_ONLY.match(s.replace("₹", "").replace("INR", "").replace("Rs", "").strip())) \
        and any(c.isdigit() for c in s)


def canon_number(v: Any) -> str:
    """'₹85,000.00' -> '85000'; account-like digit strings lose their spaces/dashes."""
    s = re.sub(r"(?i)inr|rs\.?|₹", "", str(v)).strip()
    digits = re.sub(r"[\s,\-]", "", s)
    try:
        d = Decimal(digits)
        if d == d.to_integral_value():
            return str(int(d))
        return format(d.normalize(), "f")
    except (InvalidOperation, ValueError):
        return digits


def norm_value(v: Any) -> str:
    """Canonical comparable form of an argument/claim value."""
    if is_numeric_like(v):
        return canon_number(v)
    return SEPARATORS.sub("", str(v).strip().lower())


def _num_text(text: str) -> str:
    t = re.sub(r"(?<=\d),(?=\d)", "", text)          # 85,000 -> 85000
    t = re.sub(r"(?<=\d)[ \-](?=\d)", "", t)         # 5020 0012 3456 78 -> 50200012345678
    return t


def text_contains(text: str, value: Any) -> bool:
    """Does `text` contain `value` (formatting-insensitive, boundary-safe for numbers)?"""
    nv = norm_value(value)
    if not nv:
        return False
    if is_numeric_like(value):
        return re.search(rf"(?<![\d.]){re.escape(nv)}(?![\d])", _num_text(text)) is not None
    if len(nv) < 4:
        return re.search(rf"(?<![\w]){re.escape(str(value).strip().lower())}(?![\w])", text.lower()) is not None
    return nv in SEPARATORS.sub("", text.lower())


def _tokens_like(text: str, value: Any) -> list[str]:
    if is_numeric_like(value):
        t = _num_text(text)
        return [canon_number(x) for x in re.findall(r"\d[\d.]{3,}\d|\d{4,}", t)]
    if "@" in str(value):
        return [x.lower() for x in re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)]
    return [SEPARATORS.sub("", x.lower()) for x in re.findall(r"[\w@.\-/]{4,}", text)]


def fuzzy_contains(text: str, value: Any, min_ratio: int = FUZZY_MIN_RATIO) -> bool:
    """rapidfuzz fallback for same-type tokens (typos, reformatting). Only for values >= 6 chars."""
    nv = norm_value(value)
    if len(nv) < 6:
        return False
    for tok in _tokens_like(text, value):
        nt = norm_value(tok)
        if abs(len(nt) - len(nv)) <= 2 and fuzz.ratio(nv, nt) >= min_ratio:
            return True
    return False


def tier_label(t: Tier | int | None) -> str | None:
    return None if t is None else f"T{int(t)}"


def mask_value(v: Any, keep: int = 4) -> str:
    """Display form for long identifiers in explanations (the raw value stays in the args)."""
    s = str(v)
    if len(s) > keep + 3 and re.search(r"\d{6,}", s):
        return "****" + s[-keep:]
    return s

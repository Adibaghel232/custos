"""Timing wrapper around any MemoryInterface (latency samples for M14, write counts for tokens-per-write)."""
from __future__ import annotations

import time
from typing import Any

from ..defences.base import DelegatingMemory


class TimedMemory(DelegatingMemory):
    name = "timed"

    def __init__(self, inner: Any):
        super().__init__(inner)
        self.read_ms: list[float] = []
        self.gate_ms: list[float] = []
        self.write_ms: list[float] = []
        self.n_writes = 0

    async def recall(self, s, query, purpose):
        t = time.perf_counter()
        try:
            return await self.inner.recall(s, query, purpose)
        finally:
            self.read_ms.append((time.perf_counter() - t) * 1000)

    async def before_tool_call(self, s, req):
        t = time.perf_counter()
        try:
            return await self.inner.before_tool_call(s, req)
        finally:
            self.gate_ms.append((time.perf_counter() - t) * 1000)

    async def remember(self, s, text, cites):
        t = time.perf_counter()
        try:
            return await self.inner.remember(s, text, cites)
        finally:
            self.n_writes += 1
            self.write_ms.append((time.perf_counter() - t) * 1000)

    async def end_session(self, s, transcript, summary):
        t = time.perf_counter()
        try:
            return await self.inner.end_session(s, transcript, summary)
        finally:
            self.n_writes += max(1, len(summary.claims))
            self.write_ms.append((time.perf_counter() - t) * 1000)

    def samples(self) -> dict[str, list[float]]:
        return {"read_ms": self.read_ms, "gate_ms": self.gate_ms, "write_ms": self.write_ms}

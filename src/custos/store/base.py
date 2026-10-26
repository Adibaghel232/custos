"""Store interfaces (SPEC §10.1)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

import numpy as np
from pydantic import BaseModel


class BackendItem(BaseModel):
    content: str
    metadata: dict[str, Any] = {}


class BackendHit(BaseModel):
    backend_ref: str
    content: str
    metadata: dict[str, Any] = {}
    score: float = 0.0


class MemoryBackend(Protocol):
    """An external memory store the agent sees (LangGraph store, Mem0, ...)."""
    name: str

    async def put(self, ns: tuple[str, ...], item: BackendItem) -> str: ...
    async def search(self, ns: tuple[str, ...], query: str, k: int,
                     filters: dict[str, Any] | None = None) -> list[BackendHit]: ...
    async def delete(self, ns: tuple[str, ...], ref: str) -> None: ...
    async def list_items(self, ns_prefix: tuple[str, ...]) -> list[BackendHit]: ...
    async def native_write(self, ns: tuple[str, ...], text: str, meta: dict[str, Any]) -> list[str]: ...
    async def reset(self, ns_prefix: tuple[str, ...]) -> None: ...


class VectorIndex(Protocol):
    def upsert(self, tenant: str, rid: str, vec: np.ndarray, meta: dict[str, Any]) -> None: ...
    def search(self, tenant: str, vec: np.ndarray, k: int,
               mask: Callable[[dict[str, Any]], bool] | None = None) -> list[tuple[str, float]]: ...
    def delete(self, tenant: str, rid: str) -> None: ...
    def snapshot(self) -> bytes: ...
    def restore(self, blob: bytes) -> None: ...

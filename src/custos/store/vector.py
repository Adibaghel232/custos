"""Per-tenant exact-cosine index. Other tenants' rows are never scored; the metadata mask is applied
BEFORE similarity, so filtered-out records can't reach top-k (isolation by construction)."""
from __future__ import annotations

import io
import pickle
from collections.abc import Callable
from typing import Any

import numpy as np


class _Partition:
    def __init__(self, dims: int):
        self.ids: list[str] = []
        self.pos: dict[str, int] = {}
        self.meta: list[dict[str, Any]] = []
        self.alive: list[bool] = []
        self.mat = np.zeros((0, dims), np.float32)


class NumpyIndex:
    def __init__(self) -> None:
        self.parts: dict[str, _Partition] = {}

    def upsert(self, tenant: str, rid: str, vec: np.ndarray, meta: dict[str, Any]) -> None:
        v = np.asarray(vec, np.float32).reshape(-1)
        p = self.parts.get(tenant)
        if p is None:
            p = self.parts[tenant] = _Partition(v.shape[0])
        if rid in p.pos:
            i = p.pos[rid]
            p.mat[i] = v
            p.meta[i] = dict(meta)
            p.alive[i] = True
            return
        p.pos[rid] = len(p.ids)
        p.ids.append(rid)
        p.meta.append(dict(meta))
        p.alive.append(True)
        p.mat = np.vstack([p.mat, v[None, :]])

    def update_meta(self, tenant: str, rid: str, **kv: Any) -> None:
        p = self.parts.get(tenant)
        if p and rid in p.pos:
            p.meta[p.pos[rid]].update(kv)

    def search(self, tenant: str, vec: np.ndarray, k: int,
               mask: Callable[[dict[str, Any]], bool] | None = None) -> list[tuple[str, float]]:
        p = self.parts.get(tenant)
        if p is None or not p.ids:
            return []
        rows = [i for i in range(len(p.ids)) if p.alive[i] and (mask is None or mask(p.meta[i]))]
        if not rows:
            return []
        scores = p.mat[rows] @ np.asarray(vec, np.float32).reshape(-1)
        order = np.argsort(-scores)[:k]
        return [(p.ids[rows[j]], float(scores[j])) for j in order]

    def delete(self, tenant: str, rid: str) -> None:
        p = self.parts.get(tenant)
        if p and rid in p.pos:
            p.alive[p.pos[rid]] = False

    def count(self, tenant: str) -> int:
        p = self.parts.get(tenant)
        return sum(p.alive) if p else 0

    def snapshot(self) -> bytes:
        buf = io.BytesIO()
        pickle.dump({t: (p.ids, p.meta, p.alive, p.mat) for t, p in self.parts.items()}, buf)
        return buf.getvalue()

    def restore(self, blob: bytes) -> None:
        data = pickle.loads(blob)
        self.parts = {}
        for t, (ids, meta, alive, mat) in data.items():
            p = _Partition(mat.shape[1])
            p.ids, p.meta, p.alive, p.mat = list(ids), list(meta), list(alive), mat
            p.pos = {r: i for i, r in enumerate(p.ids)}
            self.parts[t] = p

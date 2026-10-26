"""Embeddings: NVIDIA Nemotron embed model (hosted or NIM) with a sqlite cache, plus an offline HashEmbedder.

Vectors are float32 and L2-normalised, so cosine = dot product.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import sqlite3
import threading
from typing import Literal, Protocol

import numpy as np

from .settings import ROOT, Settings, get_settings

InputType = Literal["query", "passage"]


class Embedder(Protocol):
    dims: int

    async def embed(self, texts: list[str], input_type: InputType = "passage") -> np.ndarray: ...


def _normalise(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return (m / n).astype(np.float32)


class NvidiaEmbedder:
    batch = 64

    def __init__(self, settings: Settings | None = None):
        from openai import AsyncOpenAI

        self.s = settings or get_settings()
        self.model = self.s.model("embed")
        ep = self.s.endpoint("embed")
        self.client = AsyncOpenAI(base_url=ep.base_url, api_key=ep.api_key, timeout=60, max_retries=5)
        self.sem = asyncio.Semaphore(ep.max_concurrency)
        path = ROOT / self.s.llm["embed_cache_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS emb (k TEXT PRIMARY KEY, v BLOB)")
        self._lock = threading.Lock()
        self.dims = 0

    def _key(self, text: str, input_type: str) -> str:
        return hashlib.sha256(f"{self.model}|{input_type}|{text}".encode()).hexdigest()

    async def embed(self, texts: list[str], input_type: InputType = "passage") -> np.ndarray:
        keys = [self._key(t, input_type) for t in texts]
        found: dict[str, np.ndarray] = {}
        with self._lock:
            for k in set(keys):
                row = self._db.execute("SELECT v FROM emb WHERE k=?", (k,)).fetchone()
                if row:
                    found[k] = np.frombuffer(row[0], dtype=np.float32)
        todo = [(k, t) for k, t in dict(zip(keys, texts)).items() if k not in found]
        for i in range(0, len(todo), self.batch):
            chunk = todo[i:i + self.batch]
            async with self.sem:
                resp = await self.client.embeddings.create(
                    model=self.model, input=[t for _, t in chunk], encoding_format="float",
                    extra_body={"input_type": input_type, "truncate": "END"},
                )
            vecs = _normalise(np.array([d.embedding for d in resp.data], dtype=np.float32))
            with self._lock:
                for (k, _), v in zip(chunk, vecs):
                    found[k] = v
                    self._db.execute("INSERT OR REPLACE INTO emb (k, v) VALUES (?, ?)", (k, v.tobytes()))
        out = np.stack([found[k] for k in keys]) if keys else np.zeros((0, self.dims or 1), np.float32)
        self.dims = out.shape[1] if len(out) else self.dims
        return out


class HashEmbedder:
    """Deterministic offline embedder (word + char-trigram hashing). Similar texts → similar vectors."""

    def __init__(self, dims: int = 384):
        self.dims = dims

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dims, np.float32)
        t = text.lower()
        feats = re.findall(r"\w+", t) + [t[i:i + 3] for i in range(max(0, len(t) - 2))]
        for f in feats:
            h = int(hashlib.md5(f.encode()).hexdigest(), 16)
            v[h % self.dims] += 1.0 if (h >> 64) & 1 else -1.0
        return v

    async def embed(self, texts: list[str], input_type: InputType = "passage") -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dims), np.float32)
        return _normalise(np.stack([self._vec(t) for t in texts]))


_emb: Embedder | None = None


def get_embedder() -> Embedder:
    global _emb
    if _emb is None:
        _emb = HashEmbedder() if os.getenv("CUSTOS_FAKE") == "1" else NvidiaEmbedder()
    return _emb


def set_embedder(e: Embedder | None) -> None:
    global _emb
    _emb = e

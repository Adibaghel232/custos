"""Content-addressed raw evidence (emails, tool outputs, user turns). Never retrieved by the agent."""
from __future__ import annotations

from pathlib import Path

from .crypto import sha256_hex


class BlobStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, sha: str) -> Path:
        return self.root / sha[:2] / sha

    def put(self, data: bytes | str) -> tuple[str, str]:
        """Store bytes; return (sha256 hex, uri)."""
        b = data.encode() if isinstance(data, str) else data
        sha = sha256_hex(b)
        p = self._path(sha)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b)
        return sha, f"blobs/{sha}"

    def get(self, sha: str) -> bytes | None:
        p = self._path(sha)
        return p.read_bytes() if p.exists() else None

    def text(self, sha: str) -> str | None:
        b = self.get(sha)
        return b.decode("utf-8", "replace") if b is not None else None

    def verify(self, sha: str) -> bool:
        b = self.get(sha)
        return b is not None and sha256_hex(b) == sha

    def delete(self, sha: str) -> bool:
        """Erasure: remove the content; the hash stays in the ledger as a tombstone."""
        p = self._path(sha)
        if p.exists():
            p.unlink()
            return True
        return False

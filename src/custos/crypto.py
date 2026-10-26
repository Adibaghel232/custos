"""Canonical JSON, hashing and Ed25519 signing for the custody ledger and record signatures."""
from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import orjson
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def _canon(x: Any) -> Any:
    """Make a payload deterministic: floats -> 4-dp strings, datetimes -> RFC 3339 UTC ms, enums -> values."""
    if isinstance(x, float):
        return f"{x:.4f}"
    if isinstance(x, datetime):
        if x.tzinfo is None:
            x = x.replace(tzinfo=timezone.utc)
        return x.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(x, Enum):
        return _canon(x.value)
    if isinstance(x, dict):
        return {str(k): _canon(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        seq = sorted(x) if isinstance(x, set) else x
        return [_canon(v) for v in seq]
    if hasattr(x, "model_dump"):
        return _canon(x.model_dump(mode="python"))
    return x


def jcs(obj: Any) -> bytes:
    """Canonical JSON bytes (sorted keys, no floats)."""
    return orjson.dumps(_canon(obj), option=orjson.OPT_SORT_KEYS)


def sha256_hex(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ts_str(dt: datetime) -> str:
    return _canon(dt)


class Signer:
    """Ed25519 signer. key_id = sha256(raw public key)[:16]."""

    def __init__(self, private_key: Ed25519PrivateKey):
        self._sk = private_key
        raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.public_key_hex = raw.hex()
        self.key_id = sha256_hex(raw)[:16]

    @classmethod
    def generate(cls) -> "Signer":
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def load_or_create(cls, path: str | Path | None = None) -> "Signer":
        p = Path(path or os.getenv("CUSTOS_SIGNING_KEY_PATH") or "infra/keys/gateway_ed25519.pem")
        if p.exists():
            sk = serialization.load_pem_private_key(p.read_bytes(), password=None)
            assert isinstance(sk, Ed25519PrivateKey)
            return cls(sk)
        p.parent.mkdir(parents=True, exist_ok=True)
        s = cls.generate()
        p.write_bytes(s._sk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
        return s

    def sign_hex(self, digest_hex: str) -> str:
        return base64.b64encode(self._sk.sign(bytes.fromhex(digest_hex))).decode()

    @staticmethod
    def verify(public_key_hex: str, digest_hex: str, signature_b64: str) -> bool:
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex)).verify(
                base64.b64decode(signature_b64), bytes.fromhex(digest_hex))
            return True
        except Exception:
            return False

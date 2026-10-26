"""Shared offline fakes for every workstream's tests (SPEC §19)."""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import numpy as np

from custos.embed import HashEmbedder
from custos.llm import FakeLLM

Rule = tuple[str, str, Any]   # (role or "*", regex over the last user message, response or fn(match, messages, tools, schema))


class RegexFakeLLM(FakeLLM):
    """FakeLLM driven by ordered regex rules; first match wins. Unmatched calls raise (so tests stay explicit)."""

    def __init__(self, rules: list[Rule], fallback: Callable | None = None):
        super().__init__(default=self._dispatch)
        self.rules = [(role, re.compile(pat, re.S | re.I), resp) for role, pat, resp in rules]
        self.fallback = fallback
        self._role = "*"

    async def chat(self, role, messages, **kw):
        self._role = role
        return await super().chat(role, messages, **kw)

    def _dispatch(self, messages, tools, schema):
        last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "") or ""
        for role, pat, resp in self.rules:
            if role in ("*", self._role) and (m := pat.search(last)):
                return resp(m, messages, tools, schema) if callable(resp) else resp
        if self.fallback:
            return self.fallback(messages, tools, schema)
        raise AssertionError(f"RegexFakeLLM: no rule for role={self._role!r}: {last[:200]!r}")


class FakeEmbed(HashEmbedder):
    """HashEmbedder with controllable similarity: texts mapped to the same alias get identical vectors."""

    def __init__(self, aliases: dict[str, str] | None = None, dims: int = 384):
        super().__init__(dims)
        self.aliases = aliases or {}

    async def embed(self, texts, input_type="passage") -> np.ndarray:
        return await super().embed([self.aliases.get(t, t) for t in texts], input_type)

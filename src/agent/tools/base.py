"""Result of a world tool call: text for the planner plus the provenance facts the gateway needs."""
from __future__ import annotations

from dataclasses import dataclass

from custos.types import Tier


@dataclass
class ToolResult:
    text: str
    tier: Tier | None = None        # None => the registry's output_tier; mixed-tier results carry the minimum
    origin: str | None = None       # sender domain / url host / system name of the (first) source of the content
    ok: bool = True                 # False => the call failed validation and had no effect

    def __str__(self) -> str:
        return self.text

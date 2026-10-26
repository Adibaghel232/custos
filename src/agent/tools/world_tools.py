"""Thin async wrappers so the tool node can do `await tools[name].ainvoke(args)` (SPEC §7.5)."""
from __future__ import annotations

from typing import Any

from .base import ToolResult
from .registry import ToolSpec


class WorldTool:
    def __init__(self, name: str, world: Any):
        self.name, self.world = name, world

    async def ainvoke(self, args: dict[str, Any]) -> ToolResult:
        return self.world.call(self.name, args)


def build_tools(world: Any, registry: dict[str, ToolSpec]) -> dict[str, WorldTool]:
    return {n: WorldTool(n, world) for n, s in registry.items() if s.kind == "world"}

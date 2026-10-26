"""Provenance DAG over source and record ids, stored in `provenance_edges(from_id=child, to_id=parent, type)`.

Taint flows parent → child along TAINT_EDGES; `closure_by_hop` is what rollback and erasure walk.
"""
from __future__ import annotations

from collections.abc import Iterable

from .db import DB

EDGE_TYPES = ("derived_from", "cites", "supersedes", "conflicts_with", "corroborates", "read_by")
TAINT_EDGES = ("derived_from", "cites")


class Provenance:
    def __init__(self, db: DB):
        self.db = db

    def add(self, child: str, parent: str, type: str = "derived_from") -> None:
        assert type in EDGE_TYPES, type
        if child != parent:
            self.db.execute("INSERT OR IGNORE INTO provenance_edges (from_id, to_id, type) VALUES (?,?,?)",
                            (child, parent, type))

    def add_many(self, child: str, parents: Iterable[str], type: str = "derived_from") -> None:
        for p in parents:
            self.add(child, p, type)

    def _q(self, col_out: str, col_in: str, node: str, types: Iterable[str]) -> list[str]:
        ts = tuple(types)
        rows = self.db.query(
            f"SELECT {col_out} FROM provenance_edges WHERE {col_in}=? AND type IN ({','.join('?' * len(ts))})",
            (node, *ts))
        return [r[0] for r in rows]

    def parents(self, node: str, types: Iterable[str] = TAINT_EDGES) -> list[str]:
        return self._q("to_id", "from_id", node, types)

    def children(self, node: str, types: Iterable[str] = TAINT_EDGES) -> list[str]:
        return self._q("from_id", "to_id", node, types)

    def ancestors(self, node: str, types: Iterable[str] = TAINT_EDGES) -> set[str]:
        seen: set[str] = set()
        stack = [node]
        while stack:
            for p in self.parents(stack.pop(), types):
                if p not in seen:
                    seen.add(p)
                    stack.append(p)
        return seen

    def closure_by_hop(self, roots: Iterable[str], types: Iterable[str] = TAINT_EDGES) -> list[list[str]]:
        """BFS from roots over descendants: [[roots], [hop-1 children], ...]; each node appears once."""
        types = tuple(types)
        frontier = list(dict.fromkeys(roots))
        seen = set(frontier)
        hops = [frontier]
        while frontier:
            nxt = []
            for n in frontier:
                for c in self.children(n, types):
                    if c not in seen:
                        seen.add(c)
                        nxt.append(c)
            if nxt:
                hops.append(nxt)
            frontier = nxt
        return hops

    def descendants(self, roots: Iterable[str], types: Iterable[str] = TAINT_EDGES) -> set[str]:
        hops = self.closure_by_hop(roots, types)
        return {n for hop in hops[1:] for n in hop}

    def edges(self, nodes: set[str] | None = None) -> list[tuple[str, str, str]]:
        rows = self.db.query("SELECT from_id, to_id, type FROM provenance_edges")
        out = [(r[0], r[1], r[2]) for r in rows]
        return [e for e in out if nodes is None or (e[0] in nodes or e[1] in nodes)]

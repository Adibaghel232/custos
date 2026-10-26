// Pure provenance-graph helpers (no DOM): taint closure, neighbourhood queries, graph derivation.
// Shared by the UI (fixture-mode rollback synthesis, graph focus) and the fixture generator.
import type { GraphEdge, GraphNode, MemoryRecord } from '../types';

/** parent id -> child ids, from MemoryRecord.parents. */
export function childrenMap(records: Array<Pick<MemoryRecord, 'id' | 'parents'>>): Map<string, string[]> {
  const m = new Map<string, string[]>();
  for (const r of records) {
    for (const p of r.parents) {
      const list = m.get(p);
      if (list) { if (!list.includes(r.id)) list.push(r.id); } else m.set(p, [r.id]);
    }
  }
  return m;
}

/** Breadth-first taint closure. Hop 0 is the root; a node is listed at its shortest distance. */
export function taintClosureByHop(root: string, children: Map<string, string[]>): string[][] {
  const seen = new Set<string>([root]);
  const hops: string[][] = [[root]];
  let frontier = [root];
  while (frontier.length) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const c of children.get(id) ?? []) {
        if (!seen.has(c)) { seen.add(c); next.push(c); }
      }
    }
    if (next.length) hops.push(next.sort());
    frontier = next;
  }
  return hops;
}

/** Undirected neighbourhood of `focus` within `hops` edges. read_by edges only count when `includeReads`. */
export function neighbourhood(
  nodes: GraphNode[],
  edges: GraphEdge[],
  focus: string,
  hops: number,
  opts: { includeReads?: boolean; extraSeeds?: string[] } = {},
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const usable = edges.filter((e) => opts.includeReads || e.kind !== 'read_by');
  const adj = new Map<string, string[]>();
  const link = (a: string, b: string) => { (adj.get(a) ?? adj.set(a, []).get(a)!).push(b); };
  for (const e of usable) { link(e.source, e.target); link(e.target, e.source); }
  const dist = new Map<string, number>([[focus, 0]]);
  let frontier = [focus];
  for (let h = 1; h <= hops; h++) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const n of adj.get(id) ?? []) {
        if (!dist.has(n)) { dist.set(n, h); next.push(n); }
      }
    }
    frontier = next;
  }
  // Context seeds (e.g. the T3 record a check compared against) come with their direct upstream sources.
  for (const s of opts.extraSeeds ?? []) {
    if (!dist.has(s)) dist.set(s, hops);
    for (const e of usable) {
      if (e.kind === 'derived_from' && e.target === s && !dist.has(e.source)) dist.set(e.source, hops);
    }
  }
  const keep = new Set(dist.keys());
  return {
    nodes: nodes.filter((n) => keep.has(n.id)),
    edges: usable.filter((e) => keep.has(e.source) && keep.has(e.target)),
  };
}

export function recordShortLabel(r: Pick<MemoryRecord, 'id' | 'text' | 'kind' | 'claims'>): string {
  if (r.kind === 'summary') return r.text.length > 34 ? r.text.slice(0, 33) + '…' : r.text;
  const c = r.claims?.[0];
  if (c) return `${c.s}.${c.r} = ${c.o}`;
  return r.text.length > 38 ? r.text.slice(0, 37) + '…' : r.text;
}

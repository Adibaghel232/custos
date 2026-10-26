// Builds the custody-graph model: GET /api/graph (or the fixture snapshot) unioned with what the replay
// has written so far, gated by the replay clock, with the rollback animation layered on top.
import { useEffect, useMemo, useState } from 'react';
import type { GraphEdge, GraphNode, MemoryRecord, RecordStatus } from '../../types';
import { getGraph } from '../../api';
import type { GraphResponse } from '../../api';
import { neighbourhood, recordShortLabel } from '../../lib/graph';
import { useData, useReplay } from '../../state/app';
import { useUi } from '../../state/ui';

export interface ViewNode extends GraphNode {
  /** present but invisible until the rollback animation reveals it */
  ghost?: boolean;
  /** closure hop for the rollback animation (0 = root) */
  hop?: number;
  rederived?: boolean;
}

export interface GraphModel {
  nodes: ViewNode[];
  edges: GraphEdge[];
  focus: string | null;
  loading: boolean;
}

const labelOf = (r: MemoryRecord): string => {
  if (r.id === 'm_17') return 'Acme → Acme Payments Ltd';
  if (r.kind === 'summary') return r.id === 'm_31' ? 'summary · vendor emails wk 12 (clean)' : r.id === 'm_25' ? 'note · vendor changes this month' : 'summary · vendor emails wk 12';
  const c = r.claims?.[0];
  return c ? `${c.s}.${c.r} ${c.o}` : recordShortLabel(r);
};

export function useGraphModel(): GraphModel {
  const { replay } = useReplay();
  const { trace, markFixture } = useData();
  const { ui } = useUi();
  const [snapshot, setSnapshot] = useState<GraphResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const custos = replay.sides.custos.records;

  // default focus: the decisive memory of the latest blocked/asked call, else a held record, else everything
  const defaultFocus = useMemo<string | null>(() => {
    for (let i = replay.gates.length - 1; i >= 0; i--) {
      const g = replay.gates[i].gate;
      const d = g.loo?.decisive[0];
      if (g.decision !== 'allow' && d) return d;
    }
    const held = Object.values(custos).find((r) => r.status === 'quarantined');
    return held ? held.id : null;
  }, [replay.gates, custos]);
  const focus = ui.focusId ?? defaultFocus;

  useEffect(() => {
    if (!trace) return;
    let dead = false;
    setLoading(true);
    getGraph({ tenant: 'northstar-demo' })
      .then((res) => { if (!dead) { setSnapshot(res.data); markFixture(res.source); } })
      .catch(() => { if (!dead) setSnapshot(null); })
      .finally(() => { if (!dead) setLoading(false); });
    return () => { dead = true; };
  }, [trace, ui.storeVersion, markFixture]);

  return useMemo<GraphModel>(() => {
    const nodes = new Map<string, ViewNode>();
    const edges = new Map<string, GraphEdge>();
    const cur = replay.currentSession;
    const addEdge = (kind: GraphEdge['kind'], source: string, target: string) => {
      const id = `${kind}:${source}->${target}`;
      if (!edges.has(id)) edges.set(id, { id, source, target, kind });
    };

    // 1. store snapshot (API or fixture), gated by the replay clock
    if (snapshot) {
      for (const n of snapshot.nodes) {
        const isRec = n.kind === 'record' || n.kind === 'summary';
        if (isRec) {
          if (!(n.id in custos) && replay.futureRecordIds.has(n.id)) continue; // not written yet
        } else if ((n.session ?? 0) > cur) continue;
        nodes.set(n.id, { ...n });
      }
      for (const e of snapshot.edges) edges.set(e.id, e);
    }

    // 2. what the replay has written (wins on status / label)
    for (const r of Object.values(custos)) {
      nodes.set(r.id, { id: r.id, kind: r.kind === 'summary' ? 'summary' : 'record', label: labelOf(r), tier: r.tier, status: r.status, session: r.session, rederived: r.id === 'm_31' || r.source.ref.startsWith('rederive:') });
      if (r.source.type !== 'derived') {
        if (!nodes.has(r.source.ref)) {
          const src = snapshot?.nodes.find((n) => n.id === r.source.ref);
          nodes.set(r.source.ref, src ? { ...src } : { id: r.source.ref, kind: 'source', label: r.source.from ?? r.source.ref, tier: r.tier, status: 'n/a', session: r.session });
        }
        addEdge('derived_from', r.source.ref, r.id);
      }
      for (const p of r.parents) {
        if (!nodes.has(p)) {
          const src = snapshot?.nodes.find((n) => n.id === p);
          if (src) nodes.set(p, { ...src });
        }
        if (nodes.has(p)) addEdge('derived_from', p, r.id);
      }
      if (r.conflicts_with) addEdge('conflicts_with', r.id, r.conflicts_with.id);
    }
    for (const rb of replay.rollbacks) for (const pair of rb.rederived) if (nodes.has(pair.new) && nodes.has(pair.old)) addEdge('supersedes', pair.new, pair.old);

    // 3. read edges + session nodes
    for (const rd of replay.reads) {
      const sid = `sess_${rd.session}`;
      if (!nodes.has(sid)) nodes.set(sid, { id: sid, kind: 'session', label: `S${rd.session}`, tier: null, status: 'n/a', session: rd.session });
      for (const id of rd.returned) if (nodes.has(id)) addEdge('read_by', id, sid);
    }

    // 4. rollback animation overlay
    const rb = ui.rollback;
    if (rb?.result && rb.phase !== 'done' && rb.phase !== 'loading') {
      const status: RecordStatus = rb.result.mode === 'erase' ? 'erased' : 'reverted';
      rb.result.closure_by_hop.forEach((ids, hop) => {
        for (const id of ids) {
          const n = nodes.get(id);
          if (!n) continue;
          nodes.set(id, { ...n, hop, status: hop < rb.hop ? status : n.status });
        }
      });
      for (const rec of rb.extras?.records ?? []) {
        if (!nodes.has(rec.id)) {
          nodes.set(rec.id, { id: rec.id, kind: 'summary', label: labelOf(rec), tier: rec.tier, status: rec.status, session: rec.session, ghost: !rb.rederiveShown, rederived: true });
          for (const p of rec.parents) if (nodes.has(p)) addEdge('derived_from', p, rec.id);
        }
      }
      for (const pair of rb.result.rederived) if (nodes.has(pair.new) && nodes.has(pair.old)) addEdge('supersedes', pair.new, pair.old);
    } else {
      // keep hop numbers on committed rollbacks so styles stay stable
      for (const r of replay.rollbacks) r.closure_by_hop.forEach((ids, hop) => ids.forEach((id) => { const n = nodes.get(id); if (n) nodes.set(id, { ...n, hop }); }));
    }

    // 5. focus + hops (context seeds: the records the focus was checked against)
    let outNodes = [...nodes.values()];
    let outEdges = [...edges.values()].filter((e) => nodes.has(e.source) && nodes.has(e.target));
    if (focus && nodes.has(focus)) {
      const fr = custos[focus];
      const seeds = (fr?.checks ?? []).map((c) => c.against).filter((x): x is string => !!x && nodes.has(x));
      if (fr?.conflicts_with && nodes.has(fr.conflicts_with.id)) seeds.push(fr.conflicts_with.id);
      // the memories the gate compared the focus against (e.g. the T3 invoice behind the amount)
      for (const g of replay.gates) {
        if (g.gate.loo?.decisive.includes(focus)) for (const a of g.gate.args) for (const s of a.sources) if (s.record_id !== focus && nodes.has(s.record_id)) seeds.push(s.record_id);
      }
      const sub = neighbourhood(outNodes, outEdges, focus, ui.hops, { includeReads: ui.showReads, extraSeeds: seeds });
      // rederived ghosts and their lineage stay with the graph so the layout does not jump
      const keep = new Set(sub.nodes.map((n) => n.id));
      for (const n of outNodes) if (n.rederived && !keep.has(n.id)) { keep.add(n.id); }
      outNodes = outNodes.filter((n) => keep.has(n.id));
      outEdges = outEdges.filter((e) => keep.has(e.source) && keep.has(e.target) && (ui.showReads || e.kind !== 'read_by'));
    } else {
      outEdges = outEdges.filter((e) => ui.showReads || e.kind !== 'read_by');
      if (!ui.showReads) outNodes = outNodes.filter((n) => n.kind !== 'session');
    }
    return { nodes: outNodes, edges: outEdges, focus: focus && nodes.has(focus) ? focus : null, loading };
  }, [snapshot, replay, custos, ui.rollback, ui.hops, ui.showReads, focus, loading]);
}

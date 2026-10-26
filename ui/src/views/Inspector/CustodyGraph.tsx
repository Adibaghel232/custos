import { memo, useEffect, useMemo } from 'react';
import { Background, BackgroundVariant, Controls, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, useReactFlow } from '@xyflow/react';
import type { Edge, Node, NodeProps } from '@xyflow/react';
import dagre from '@dagrejs/dagre';
import { Database, Mail, Maximize, Sigma, User } from 'lucide-react';
import type { GraphEdge, RecordStatus, Tier } from '../../types';
import { useUi } from '../../state/ui';
import { useActions } from '../../state/actions';
import { cx } from '../../lib/format';
import { STATUS_META } from '../../lib/tiers';
import { StatusIcon, TierChip, Toggle, Segmented } from '../../components/ui';
import { useTokens } from '../../lib/tokens';
import { useGraphModel } from './useGraphModel';
import type { ViewNode } from './useGraphModel';

const REC_W = 252;
const REC_H = 92;
const SRC_W = 236;
const SRC_H = 66;
const SESS_W = 76;
const SESS_H = 40;

const sizeOf = (n: ViewNode) => (n.kind === 'session' ? { w: SESS_W, h: SESS_H } : n.kind === 'source' ? { w: SRC_W, h: SRC_H } : { w: REC_W, h: REC_H });

interface RFData extends Record<string, unknown> {
  n: ViewNode;
  selected: boolean;
  focused: boolean;
}

const tierVar = (t: Tier | null) => (t ? `var(--${t.toLowerCase()})` : 'var(--faint)');

function statusBox(status: RecordStatus | 'n/a'): string {
  switch (status) {
    case 'quarantined': return 'border-2 border-dashed border-warn';
    case 'rejected': return 'border-2 border-danger';
    case 'superseded': return 'border border-border opacity-45';
    case 'reverted': return 'border-2 border-reverted';
    case 'erased': return 'border-2 border-dotted border-reverted opacity-80';
    case 'pending': return 'border-2 border-dotted border-info';
    default: return 'border border-muted/55';
  }
}

const RecordNode = memo(function RecordNode({ data }: NodeProps<Node<RFData>>) {
  const { n, selected, focused } = data;
  const dead = n.status === 'reverted' || n.status === 'erased';
  const tier = n.tier;
  const bg = dead ? 'rgb(var(--panel-2))' : `color-mix(in srgb, rgb(${tierVar(tier)}) 17%, rgb(var(--panel)))`;
  return (
    <div
      className={cx(
        'relative overflow-hidden rounded-xl pl-3.5 pr-3 py-2 transition-all duration-500',
        statusBox(n.status), selected && 'ring-2 ring-info ring-offset-2 ring-offset-bg', focused && !selected && 'ring-1 ring-muted',
        n.ghost && 'pointer-events-none scale-90 opacity-0', n.rederived && !n.ghost && 'animate-pulse-ring',
      )}
      style={{ width: REC_W, height: REC_H, background: bg }}
    >
      <span aria-hidden className={cx('absolute inset-y-0 left-0 w-2', tier === 'T0' && !dead ? 'hazard' : '')} style={tier === 'T0' && !dead ? undefined : { background: dead ? 'rgb(var(--reverted))' : `rgb(${tierVar(tier)})` }} />
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      <div className="flex items-center gap-2">
        <span className="mono text-sm font-bold text-ink">{n.id}</span>
        {tier && <TierChip tier={tier} size="xs" />}
        {n.kind === 'summary' && <Sigma size={15} className="text-muted" aria-label="summary" />}
        <span className="ml-auto flex items-center gap-1">
          {n.status !== 'committed' && n.status !== 'n/a' && <span className="text-[0.6875rem] font-extrabold tracking-wide text-ink/90">{STATUS_META[n.status].short}</span>}
          {n.status !== 'n/a' && <StatusIcon status={n.status} size={17} />}
        </span>
      </div>
      <div className={cx('label-clip mt-1 text-sm leading-snug', dead ? 'text-revertedink' : n.status === 'superseded' ? 'strike text-muted' : 'text-ink')}>{n.status === 'erased' ? '[erased]' : n.label}</div>
      <div className="absolute bottom-1 right-2.5 text-[0.6875rem] font-semibold text-faint">{n.session ? `S${n.session}` : 'pre-existing'}</div>
    </div>
  );
});

const SourceNode = memo(function SourceNode({ data }: NodeProps<Node<RFData>>) {
  const { n, selected } = data;
  const Icon = n.id.startsWith('erp') ? Database : n.id.startsWith('t_') ? User : Mail;
  const tier = n.tier;
  return (
    <div
      className={cx('relative flex items-center gap-2.5 rounded-lg border border-dashed border-muted/60 bg-panel2 px-3 py-2', selected && 'ring-2 ring-info')}
      style={{ width: SRC_W, height: SRC_H }}
    >
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      <Icon size={20} className="shrink-0 text-muted" strokeWidth={2.1} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold text-ink">{n.label}</div>
        <div className="mono text-xs text-faint">source · {n.id}</div>
      </div>
      {tier && <TierChip tier={tier} size="xs" />}
    </div>
  );
});

const SessionNode = memo(function SessionNode({ data }: NodeProps<Node<RFData>>) {
  return (
    <div className="grid place-items-center rounded-full border border-info/70 bg-info/12 text-sm font-bold text-ink" style={{ width: SESS_W, height: SESS_H }}>
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      ◦{data.n.label}
    </div>
  );
});

const nodeTypes = { rec: RecordNode, src: SourceNode, sess: SessionNode };

function layout(nodes: ViewNode[], edges: GraphEdge[]): Map<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'LR', nodesep: 34, ranksep: 96, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of nodes) { const s = sizeOf(n); g.setNode(n.id, { width: s.w, height: s.h }); }
  for (const e of edges) g.setEdge(e.source, e.target);
  dagre.layout(g);
  const out = new Map<string, { x: number; y: number }>();
  for (const n of nodes) {
    const p = g.node(n.id);
    const s = sizeOf(n);
    out.set(n.id, { x: p.x - s.w / 2, y: p.y - s.h / 2 });
  }
  return out;
}

function Inner() {
  const model = useGraphModel();
  const { ui, dispatch } = useUi();
  const { openRecord } = useActions();
  const t = useTokens();
  const rf = useReactFlow();

  // layout only depends on structure, so status changes do not move nodes
  const structKey = model.nodes.map((n) => n.id).sort().join(',') + '|' + model.edges.map((e) => e.id).sort().join(',');
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const positions = useMemo(() => layout(model.nodes, model.edges), [structKey]);

  const rfNodes = useMemo<Node<RFData>[]>(
    () => model.nodes.map((n) => ({
      id: n.id,
      type: n.kind === 'session' ? 'sess' : n.kind === 'source' ? 'src' : 'rec',
      position: positions.get(n.id) ?? { x: 0, y: 0 },
      // explicit sizes: the nodes are controlled (no onNodesChange), so React Flow cannot write measured sizes back
      width: sizeOf(n).w,
      height: sizeOf(n).h,
      data: { n, selected: ui.selectedId === n.id, focused: model.focus === n.id },
      draggable: true,
      selectable: true,
    })),
    [model.nodes, model.focus, positions, ui.selectedId],
  );

  const rfEdges = useMemo<Edge[]>(
    () => model.edges.map((e) => {
      const base = { id: e.id, source: e.source, target: e.target };
      const hidden = model.nodes.find((n) => n.id === e.target)?.ghost || model.nodes.find((n) => n.id === e.source)?.ghost;
      const style = { opacity: hidden ? 0 : 1, transition: 'opacity 500ms' };
      switch (e.kind) {
        case 'derived_from': return { ...base, type: 'default', style: { ...style, stroke: t.muted, strokeWidth: 2 }, markerEnd: { type: MarkerType.ArrowClosed, color: t.muted, width: 18, height: 18 } };
        case 'conflicts_with': return { ...base, type: 'default', style: { ...style, stroke: t.danger, strokeWidth: 2.5, strokeDasharray: '7 5' }, markerEnd: { type: MarkerType.ArrowClosed, color: t.danger, width: 18, height: 18 }, label: 'conflicts', labelStyle: { fill: t['danger-ink'], fontWeight: 700, fontSize: 13 }, labelBgStyle: { fill: t.panel } };
        case 'supersedes': return { ...base, type: 'default', style: { ...style, stroke: t.faint, strokeWidth: 2 }, markerEnd: { type: MarkerType.ArrowClosed, color: t.faint, width: 16, height: 16 }, label: 'supersedes', labelStyle: { fill: t.faint, fontSize: 12 }, labelBgStyle: { fill: t.panel } };
        default: return { ...base, type: 'default', style: { ...style, stroke: t.info, strokeWidth: 1.2, strokeDasharray: '2 5' } };
      }
    }),
    [model.edges, model.nodes, t],
  );

  // refit on structural change or when asked (F / the Fit button)
  useEffect(() => {
    const h = window.setTimeout(() => void rf.fitView({ padding: { top: '24px', bottom: '70px', left: '28px', right: '28px' }, duration: ui.motionReduced ? 0 : 450, maxZoom: 1.15 }), 80);
    return () => window.clearTimeout(h);
  }, [structKey, ui.fitNonce, rf, ui.motionReduced]);

  return (
    <div className="relative h-full">
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        minZoom={0.2}
        maxZoom={1.6}
        nodesConnectable={false}
        elementsSelectable
        zoomOnDoubleClick={false}
        proOptions={{ hideAttribution: false }}
        onNodeClick={(_, node) => { const n = (node.data as RFData).n; if (n.kind !== 'session') dispatch({ type: 'select', id: n.id }); }}
        onNodeDoubleClick={(_, node) => { const n = (node.data as RFData).n; if (n.kind === 'record' || n.kind === 'summary') { dispatch({ type: 'hops', hops: 2 }); openRecord(n.id, { goto: false }); } }}
        onPaneClick={() => dispatch({ type: 'select', id: null })}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.5} color={t.border} />
        <Controls showInteractive={false} position="bottom-right" fitViewOptions={{ padding: 0.18 }} />
      </ReactFlow>
      {model.nodes.length === 0 && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center text-center text-muted">
          <div><div className="text-lg font-semibold text-ink/80">Nothing in the store yet</div><div className="mt-1 text-sm">The graph fills in as the replay writes records.</div></div>
        </div>
      )}
      <Legend />
    </div>
  );
}

function Legend() {
  return (
    <div className="pointer-events-none absolute bottom-2.5 left-3 flex max-w-[calc(100%-6rem)] flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-border bg-panel/90 px-3 py-1.5 text-xs text-muted backdrop-blur-sm">
      <span className="flex items-center gap-1"><Database size={14} /> source</span>
      <span className="flex items-center gap-1.5">{(['T4', 'T3', 'T2', 'T1', 'T0'] as Tier[]).map((x) => <TierChip key={x} tier={x} size="xs" />)}</span>
      <span className="flex items-center gap-1"><StatusIcon status="quarantined" size={14} /> held</span>
      <span className="flex items-center gap-1"><StatusIcon status="rejected" size={14} /> rejected</span>
      <span className="flex items-center gap-1"><StatusIcon status="superseded" size={14} /> superseded</span>
      <span className="flex items-center gap-1"><StatusIcon status="reverted" size={14} /> reverted</span>
    </div>
  );
}

export function CustodyGraph() {
  const { ui, dispatch } = useUi();
  const model = useGraphModel();
  return (
    <div className="card flex h-full min-h-0 flex-col overflow-hidden">
      <div className="flex min-h-[3rem] flex-wrap items-center gap-x-4 gap-y-1 border-b border-border px-4 py-2">
        <div>
          <div className="caps !text-[0.8125rem] text-ink/90">Custody graph</div>
          <div className="text-xs text-muted">tenant northstar-demo · focus <span className="mono font-semibold text-ink">{model.focus ?? 'all'}</span> · {ui.hops} hop{ui.hops > 1 ? 's' : ''}</div>
        </div>
        <div className="ml-auto flex items-center gap-3">
          {model.focus && (
            <Segmented<number> label="Hops" size="sm" value={ui.hops} onChange={(h) => dispatch({ type: 'hops', hops: h })} options={[1, 2, 3].map((h) => ({ value: h, label: `${h} hop${h > 1 ? 's' : ''}` }))} />
          )}
          {ui.focusId && (
            <button type="button" className="btn !px-2.5 !py-1" onClick={() => dispatch({ type: 'focus', id: null })} title="Show the default focus again">Reset focus</button>
          )}
          <Toggle checked={ui.showReads} onChange={(on) => dispatch({ type: 'reads', on })} label="reads" hint="Show which sessions read each memory (read_by edges)" />
          <button type="button" className="btn !px-2.5 !py-1" onClick={() => dispatch({ type: 'fit' })} title="Fit the graph (F)"><Maximize size={17} /> fit <span className="kbd">F</span></button>
        </div>
      </div>
      <div className="min-h-0 flex-1">
        <ReactFlowProvider>
          <Inner />
        </ReactFlowProvider>
      </div>
    </div>
  );
}


import { ChevronDown, ChevronRight } from 'lucide-react';
import type { Side } from '../../types';
import type { LogItem, ReplayState, SessionLog } from '../../state/replay';
import { cx } from '../../lib/format';
import { GateRow, InboundCard, MemoryUpdateView, MessageRow, PipelineView, ReadRow, SessionOutcomeBlock, ToolRow } from './parts';

/** One-line description of a collapsed session. */
function summarize(log: SessionLog): { text: string; tags: string[] } {
  const msg = log.items.find((i) => i.kind === 'message' && i.payload.role === 'user');
  const inbound = log.items.find((i) => i.kind === 'inbound');
  let text = '';
  if (msg && msg.kind === 'message') text = `“${msg.payload.text}”`;
  else if (inbound && inbound.kind === 'inbound') text = inbound.payload.kind === 'email' ? `✉ ${inbound.payload.from}` : (inbound.payload.subject ?? inbound.payload.from);
  const tags: string[] = [];
  const writes = log.items.filter((i) => (i.kind === 'pipeline' && i.row.written) || i.kind === 'write').length;
  if (writes) tags.push(`${writes} write${writes > 1 ? 's' : ''}`);
  const held = log.items.filter((i) => i.kind === 'pipeline' && i.row.decision?.verdict === 'quarantine').length;
  if (held) tags.push(`${held} held`);
  const tools = log.items.filter((i) => i.kind === 'tool').length;
  if (tools) tags.push(`${tools} tool call${tools > 1 ? 's' : ''}`);
  return { text, tags };
}

export function SessionCard({
  side, log, expanded, onToggle, replay, onOpenRecord, onOpenGate,
}: {
  side: Side;
  log: SessionLog;
  expanded: boolean;
  onToggle: () => void;
  replay: ReplayState;
  onOpenRecord: (id: string) => void;
  onOpenGate: () => void;
}) {
  const state = replay.sides[side];
  const verdict = state.outcome?.verdict;
  const Chevron = expanded ? ChevronDown : ChevronRight;

  const header = (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={expanded}
      className={cx('flex w-full items-center gap-2.5 text-left', expanded ? 'pb-2' : 'py-0.5')}
    >
      <Chevron size={18} className="shrink-0 text-faint" />
      <span className="rounded-md bg-bg/70 px-2 py-0.5 text-sm font-extrabold tabular-nums text-ink">S{log.n}</span>
      <span className={cx('min-w-0 flex-1 truncate', expanded ? 'text-base font-semibold text-ink' : 'text-sm text-muted')}>
        {log.label || `Session ${log.n}`}
        {!expanded && (() => { const s = summarize(log); return s.text ? <span className="text-faint"> · {s.text}</span> : null; })()}
      </span>
      {!expanded && summarize(log).tags.map((t) => <span key={t} className="chip !py-0">{t}</span>)}
    </button>
  );

  if (!expanded) return <div className="rounded-lg border border-border/60 bg-panel/60 px-3 py-1.5">{header}</div>;

  const tierFor = (ref: string) => {
    const row = log.items.find((i) => i.kind === 'pipeline' && i.row.stamp?.source.ref === ref);
    return row && row.kind === 'pipeline' ? row.row.stamp?.tier : undefined;
  };

  const renderItem = (it: LogItem) => {
    switch (it.kind) {
      case 'inbound': return <InboundCard key={it.key} p={it.payload} side={side} tier={side === 'custos' ? tierFor(it.payload.ref) : undefined} />;
      case 'message': return <MessageRow key={it.key} p={it.payload} />;
      case 'pipeline':
        return side === 'custos'
          ? <PipelineView key={it.key} row={it.row} rec={state.records[it.row.recordId]} records={state.records} onOpen={onOpenRecord} />
          : <MemoryUpdateView key={it.key} row={it.row} rec={state.records[it.row.recordId]} records={state.records} />;
      case 'write': {
        const rec = state.records[it.recordId];
        return rec ? <div key={it.key} className="animate-fade-in text-sm text-muted">memory updated: <span className="font-semibold text-ink">{rec.text}</span></div> : null;
      }
      case 'read': return <ReadRow key={it.key} p={it.payload} side={side} onOpen={onOpenRecord} />;
      case 'tool': return <ToolRow key={it.key} c={it.call} verdict={verdict} />;
      case 'gate': return <GateRow key={it.key} g={it.gate} onOpen={onOpenGate} />;
      case 'outcome': return <SessionOutcomeBlock key={it.key} side={side} replay={replay} />;
    }
  };

  return (
    <section className="rounded-xl border border-border bg-panel p-3.5 shadow-card" aria-label={`Session ${log.n}`}>
      {header}
      <div className="space-y-2.5">{log.items.map(renderItem)}</div>
    </section>
  );
}

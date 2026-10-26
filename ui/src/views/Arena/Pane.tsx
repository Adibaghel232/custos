import { useEffect, useLayoutEffect, useRef } from 'react';
import { Brain, ShieldCheck, ShieldOff } from 'lucide-react';
import type { MemoryRecord, Side } from '../../types';
import { useData, useReplay } from '../../state/app';
import { useUi } from '../../state/ui';
import { useActions } from '../../state/actions';
import { cx, formatINR } from '../../lib/format';
import { memoryLabel, SOURCE_LABEL, TOOLS } from '../../lib/labels';
import { prettyModel } from '../../components/Shell';
import { StatusIcon, TierChip, Tip } from '../../components/ui';
import { SessionCard } from './SessionCard';

function Badges({ side }: { side: Side }) {
  const { trace } = useData();
  const model = trace?.models.agent;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="chip !text-ink" title={model}>{prettyModel(model)}</span>
      <span className="chip !text-ink">{side === 'custos' ? 'Mem0 + Custos Gateway' : `${trace?.backend === 'mem0' || !trace ? 'Mem0' : trace.backend}`}</span>
      <Tip
        content={<ul className="space-y-1">{TOOLS.map((t) => <li key={t.name}><code className="mono font-semibold">{t.name}</code><span className="text-muted"> · {t.note}</span></li>)}</ul>}
      >
        <span className="chip cursor-help !text-ink">
          {TOOLS.map((t) => t.short + (t.risk === 'high' ? '*' : '')).join(' · ')}
        </span>
      </Tip>
    </div>
  );
}

function MemoryRow({ rec, side }: { rec: MemoryRecord; side: Side }) {
  const { openRecord } = useActions();
  const dead = rec.status === 'superseded' || rec.status === 'reverted' || rec.status === 'erased' || rec.status === 'rejected';
  const clickable = side === 'custos';
  return (
    <li
      className={cx('animate-flash flex items-center gap-2.5 rounded-lg px-2.5 py-1.5', clickable && 'cursor-pointer hover:bg-panel2/80')}
      onClick={clickable ? () => openRecord(rec.id) : undefined}
      title={clickable ? `Open ${rec.id} in the Inspector` : rec.text}
    >
      {side === 'custos' && rec.tier ? <TierChip tier={rec.tier} size="sm" /> : <Brain size={17} className="shrink-0 text-faint" />}
      <span className={cx('min-w-0 flex-1 truncate text-[0.9375rem]', dead ? 'text-faint line-through' : 'text-ink')}>{rec.status === 'erased' ? '[erased]' : memoryLabel(rec)}</span>
      <span className="shrink-0 text-xs text-muted">{rec.kind === 'summary' ? 'derived' : `from ${SOURCE_LABEL[rec.source.type]}`}</span>
      {(side === 'custos' || dead) && <StatusIcon status={rec.status} size={17} />}
      {rec.status === 'quarantined' && <span className="text-xs font-extrabold tracking-wide text-warn">HELD</span>}
    </li>
  );
}

function MemoryPanel({ side }: { side: Side }) {
  const { replay } = useReplay();
  const s = replay.sides[side];
  const recs = s.order.map((id) => s.records[id]);
  const visible = side === 'undefended' ? recs.filter((r) => r.status !== 'superseded') : recs;
  const committed = recs.filter((r) => r.status === 'committed').length;
  const held = recs.filter((r) => r.status === 'quarantined').length;
  const reverted = recs.filter((r) => r.status === 'reverted' || r.status === 'erased').length;
  return (
    <div className="flex min-h-0 flex-col rounded-xl border border-border bg-panel">
      <div className="flex items-center gap-3 border-b border-border px-3.5 py-2">
        <Brain size={18} className="text-muted" />
        <span className="caps !text-[0.8125rem] text-ink/90">Memory</span>
        <span className="ml-auto text-sm text-muted">
          {side === 'custos'
            ? <>{committed} committed{held > 0 && <> · <span className="font-bold text-warn">{held} held</span></>}{reverted > 0 && <> · {reverted} reverted</>}</>
            : <>{visible.length} {visible.length === 1 ? 'entry' : 'entries'}</>}
        </span>
      </div>
      <ul className="max-h-[15rem] min-h-[7.5rem] flex-1 overflow-y-auto p-1.5 2xl:max-h-[17rem]">
        {visible.length === 0 && <li className="px-3 py-4 text-sm text-faint">Nothing stored yet.</li>}
        {visible.map((r) => <MemoryRow key={`${r.id}|${r.status}|${r.text}`} rec={r} side={side} />)}
      </ul>
    </div>
  );
}

function OutcomeBar({ side }: { side: Side }) {
  const { replay } = useReplay();
  const o = replay.sides[side].outcome;
  const held = Object.values(replay.sides[side].records).filter((r) => r.status === 'quarantined').length;
  const moved = o ? (typeof o.money_moved === 'number' ? o.money_moved : o.money_moved ? 1 : 0) : 0;
  let body = <span className="text-faint">waiting for session 9</span>;
  let tone = 'border-border bg-panel';
  if (o) {
    if (o.verdict === 'attacker_paid') {
      tone = 'border-danger bg-danger/14';
      body = <span className="font-bold text-dangerink">✗ Attacker paid {formatINR(moved)}</span>;
    } else if (o.verdict === 'verified_paid') {
      tone = 'border-ok/70 bg-ok/10';
      body = <span className="font-bold text-ok">✓ Verified account paid{held ? ` · ${held} claim${held > 1 ? 's' : ''} held` : ''}</span>;
    } else {
      tone = 'border-warn/70 bg-warn/10';
      body = <span className="font-bold text-warn">⛔ Payment blocked · 0 payments executed</span>;
    }
  }
  return (
    <div className={cx('flex items-center gap-3 rounded-xl border px-4 py-3 text-lg transition-colors duration-500', tone)}>
      <span className="caps !text-[0.8125rem]">Outcome</span>
      {body}
    </div>
  );
}

function Stream({ side }: { side: Side }) {
  const { replay } = useReplay();
  const { ui, dispatch } = useUi();
  const { openRecord, openGate } = useActions();
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const logs = replay.sides[side].sessions;
  const last = replay.lastEvent?.seq ?? 0;

  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 90;
  };
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTo({ top: el.scrollHeight, behavior: ui.motionReduced ? 'auto' : 'smooth' });
  }, [last, logs.length, ui.motionReduced]);
  useEffect(() => { if (replay.cursor < 0) stick.current = true; }, [replay.cursor]);

  return (
    <div ref={ref} onScroll={onScroll} className="min-h-0 flex-1 space-y-2.5 overflow-y-auto pr-1" aria-live="polite" aria-label={`${side} activity`}>
      {logs.length === 0 && (
        <div className="grid h-full place-items-center rounded-xl border border-dashed border-border text-center text-muted">
          <div>
            <div className="text-lg font-semibold text-ink/80">Both panes are empty</div>
            <div className="mt-1 text-sm">Press <span className="kbd">N</span> to start session 1, or jump with the session chips above.</div>
          </div>
        </div>
      )}
      {logs.map((log) => {
        const key = `${side}:${log.n}`;
        const expanded = ui.expanded[key] ?? log.n === replay.currentSession;
        return (
          <SessionCard
            key={log.n}
            side={side}
            log={log}
            expanded={expanded}
            onToggle={() => dispatch({ type: 'expand', key, value: !expanded })}
            replay={replay}
            onOpenRecord={(id) => openRecord(id)}
            onOpenGate={openGate}
          />
        );
      })}
    </div>
  );
}

export function Pane({ side }: { side: Side }) {
  const custos = side === 'custos';
  return (
    <section
      className={cx('flex min-h-0 min-w-0 flex-col gap-3 rounded-2xl border bg-bg/40 p-3.5', custos ? 'border-ok/45' : 'border-danger/45')}
      aria-label={custos ? 'Custos pane' : 'Undefended pane'}
    >
      <header className={cx('rounded-xl border-l-4 bg-panel px-4 py-3', custos ? 'border-l-ok' : 'border-l-danger')}>
        <div className="flex items-center gap-3">
          {custos ? <ShieldCheck size={28} className="text-ok" strokeWidth={2.2} /> : <ShieldOff size={28} className="text-dangerink" strokeWidth={2.2} />}
          <h2 className={cx('text-2xl font-extrabold tracking-[0.08em]', custos ? 'text-ok' : 'text-dangerink')}>{custos ? 'CUSTOS' : 'UNDEFENDED'}</h2>
          <span className="ml-auto text-sm text-muted">{custos ? 'memory through the Custos gateway' : 'memory written straight to the store'}</span>
        </div>
        <div className="mt-2"><Badges side={side} /></div>
      </header>
      <Stream side={side} />
      <MemoryPanel side={side} />
      <OutcomeBar side={side} />
    </section>
  );
}

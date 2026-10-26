import {
  Bot, Brain, ChevronRight, Database, FileText, Globe, Mail, OctagonX, Search, ShieldAlert, ShieldCheck, ShieldQuestion, TriangleAlert, User, Wrench, Check, Pause, Users,
} from 'lucide-react';
import type { ReactNode } from 'react';
import type { MemoryRecord, Side } from '../../types';
import type { InboundPayload, MemoryReadPayload, MessagePayload } from '../../tracePayloads';
import { checkSeverity } from '../../tracePayloads';
import type { PipelineRow, ReplayState, ToolCallState } from '../../state/replay';
import type { GateDecision } from '../../types';
import { cx, formatINR, formatMs } from '../../lib/format';
import { formatCall, memoryLabel, SOURCE_LABEL } from '../../lib/labels';
import { TierChip } from '../../components/ui';

// ---- inbound -----------------------------------------------------------------------------------

const INBOUND_ICON = { email: Mail, erp_sync: Database, web: Globe, file: FileText, peer_agent: Users } as const;

/** Email bodies: drop blank lines so the card stays compact. */
const collapseBlank = (t: string): string => t.split(/\r?\n/).filter((l) => l.trim() !== '').join('\n');

export function InboundCard({ p, tier, side }: { p: InboundPayload; tier?: MemoryRecord['tier']; side: Side }) {
  const Icon = INBOUND_ICON[p.kind];
  const isMail = p.kind === 'email';
  return (
    <div className={cx('animate-drop-in rounded-xl border bg-panel2 p-3.5', isMail ? 'border-t0/55' : 'border-border')}>
      <div className="flex items-center gap-2.5">
        <Icon size={20} className={isMail ? 'text-t0' : 'text-muted'} strokeWidth={2.1} />
        <span className="min-w-0 flex-1 truncate text-base font-semibold text-ink">{p.from}</span>
        {side === 'custos' && tier && <TierChip tier={tier} size="md" className="animate-pop-in" />}
      </div>
      {p.subject && <div className="mt-1.5 text-sm font-semibold text-ink/95">{p.subject}</div>}
      <p className="mt-1 line-clamp-4 whitespace-pre-line text-sm leading-relaxed text-muted">{collapseBlank(p.text)}</p>
    </div>
  );
}

// ---- messages ----------------------------------------------------------------------------------

export function MessageRow({ p }: { p: MessagePayload }) {
  const user = p.role === 'user';
  return (
    <div className="animate-fade-in flex items-start gap-2.5">
      <span className={cx('mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full', user ? 'bg-info/20 text-info' : 'bg-panel2 text-muted')}>
        {user ? <User size={16} strokeWidth={2.3} /> : <Bot size={16} strokeWidth={2.3} />}
      </span>
      <p className={cx('min-w-0 flex-1 text-base leading-snug', user ? 'font-semibold text-ink' : 'text-ink/90')}>{p.text}</p>
    </div>
  );
}

// ---- Custos pipeline ---------------------------------------------------------------------------

function PChip({ lit, tone = 'neutral', children, title }: { lit: boolean; tone?: 'neutral' | 'ok' | 'warn' | 'danger' | 'amber'; children: ReactNode; title?: string }) {
  const tones = {
    neutral: 'border-border bg-panel text-ink',
    ok: 'border-ok/70 bg-ok/14 text-ink',
    warn: 'border-warn/80 bg-warn/16 text-ink',
    danger: 'border-danger/80 bg-danger/16 text-ink',
    amber: 'border-t0/80 bg-t0/16 text-ink',
  } as const;
  return (
    <span
      title={title}
      className={cx(
        'inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg border px-2.5 py-1 text-sm font-semibold transition-all duration-300',
        lit ? cx(tones[tone], 'scale-100 opacity-100') : 'scale-[0.97] border-border/60 bg-transparent text-faint opacity-60',
      )}
    >
      {children}
    </span>
  );
}

const Arrow = () => <ChevronRight size={16} className="shrink-0 text-faint" aria-hidden />;

export function PipelineView({ row, rec, records, onOpen }: { row: PipelineRow; rec?: MemoryRecord; records: Record<string, MemoryRecord>; onOpen: (id: string) => void }) {
  const flagged = row.checks.find((c) => checkSeverity(c) === 'flag');
  const d = row.decision;
  const verdictTone = !d ? 'neutral' : d.verdict === 'quarantine' ? 'warn' : d.verdict === 'reject' ? 'danger' : row.stamp?.tier === 'T0' ? 'amber' : 'ok';
  const verdictLabel = !d ? 'Verdict' : d.verdict === 'quarantine' ? 'QUARANTINE' : d.verdict === 'reject' ? 'REJECT' : d.verdict === 'version' ? 'VERSION' : row.stamp?.tier === 'T0' ? 'COMMITTED · T0' : `COMMIT · ${row.stamp?.tier ?? ''}`;
  const against = d?.conflicts_with ?? undefined;
  const againstRec = against ? records[against.id] : undefined;
  const checkNli = row.checks.find((c) => c.check === 'nli');
  const clickable = !!d;
  return (
    <div
      className={cx('animate-fade-in rounded-xl border border-border/80 bg-panel2/70 p-3', clickable && 'cursor-pointer hover:border-muted/60')}
      onClick={clickable ? () => onOpen(row.recordId) : undefined}
      role={clickable ? 'button' : undefined}
      title={clickable ? `Open ${row.recordId} in the Inspector` : undefined}
    >
      <div className="mb-2 flex items-center gap-2">
        <span className="mono rounded bg-bg/70 px-1.5 py-0.5 text-xs text-muted">{row.recordId}</span>
        {rec && <span className="min-w-0 flex-1 truncate text-sm text-ink/90">{memoryLabel(rec)}</span>}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <PChip lit={!!row.stamp}>
          Stamp {row.stamp && <TierChip tier={row.stamp.tier} size="xs" />} {row.stamp && <Check size={15} className="text-ok" strokeWidth={3} />}
        </PChip>
        <Arrow />
        <PChip lit={!!row.claims}>Extract {row.claims && <><span className="text-muted">{row.claims.length}</span><Check size={15} className="text-ok" strokeWidth={3} /></>}</PChip>
        <Arrow />
        <PChip lit={row.checks.length > 0} tone={flagged && d ? 'warn' : 'neutral'}>
          Check
          {row.checks.length > 0 && (flagged ? <TriangleAlert size={15} className="text-warn" strokeWidth={2.8} /> : d ? <Check size={15} className="text-ok" strokeWidth={3} /> : <span className="text-muted">{row.checks.length}</span>)}
        </PChip>
        <Arrow />
        <PChip lit={!!d} tone={verdictTone}>
          {d?.verdict === 'quarantine' && <Pause size={15} className="text-warn" strokeWidth={3} />}
          {verdictLabel}
        </PChip>
      </div>
      {against && d?.verdict === 'quarantine' && (
        <p className="animate-fade-in mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-ink">
          <TriangleAlert size={16} className="shrink-0 text-warn" strokeWidth={2.6} />
          <span>contradicts {againstRec ? SOURCE_LABEL[againstRec.source.type] : 'record'} <span className="mono text-xs">{against.id}</span></span>
          <TierChip tier={against.tier} size="xs" />
          {checkNli?.score !== undefined && <span className="text-muted">· NLI {checkNli.score.toFixed(2)}</span>}
        </p>
      )}
      {d && d.verdict !== 'quarantine' && flagged && (
        <p className="animate-fade-in mt-2 flex items-center gap-2 text-sm text-ink"><TriangleAlert size={16} className="text-warn" />{flagged.result}</p>
      )}
      {d && !flagged && row.checks.find((c) => c.check === 'instruction' && /data only/i.test(c.result)) && (
        <p className="animate-fade-in mt-2 text-sm text-muted">No conflict found. Standing instruction stored as quoted data only.</p>
      )}
    </div>
  );
}

/** Undefended side: Mem0 just extracts and writes; there is nothing to stamp or check. */
export function MemoryUpdateView({ row, rec, records }: { row: PipelineRow; rec?: MemoryRecord; records: Record<string, MemoryRecord> }) {
  const d = row.decision;
  const replaced = rec && d?.verdict === 'version' ? Object.values(records).find((r) => r.status === 'superseded' && r.claims?.[0] && rec.claims?.[0] && r.claims[0].s === rec.claims[0].s && r.claims[0].r === rec.claims[0].r) : undefined;
  return (
    <div className="animate-fade-in rounded-xl border border-border/80 bg-panel2/70 p-3">
      <div className="flex items-center gap-2.5 text-sm font-semibold text-muted">
        <Brain size={18} className={rec ? 'text-ink' : 'text-faint'} />
        {rec ? <span className="text-ink">memory updated</span> : <span>Mem0 extracting…</span>}
        {d && <span className="rounded bg-bg/70 px-1.5 py-0.5 text-xs font-medium">{d.verdict === 'version' ? 'UPDATE' : 'ADD'}</span>}
      </div>
      {rec && <p className="mt-1.5 pl-[1.9rem] text-base font-semibold text-ink">{memoryLabel(rec)}</p>}
      {replaced && <p className="mt-0.5 pl-[1.9rem] text-sm text-muted">replaced {replaced.claims?.[0]?.o} <span className="text-faint">(newest plausible write wins)</span></p>}
    </div>
  );
}

// ---- reads, tools, gate ----------------------------------------------------------------------------

export function ReadRow({ p, side, onOpen }: { p: MemoryReadPayload; side: Side; onOpen: (id: string) => void }) {
  return (
    <div className="animate-fade-in flex flex-wrap items-center gap-x-2 gap-y-1.5 text-sm text-muted">
      <Search size={17} className="shrink-0 text-faint" />
      <span>recalled <span className="font-semibold text-ink">{p.returned.length}</span></span>
      {p.returned.map((r) =>
        side === 'custos' ? (
          <button key={r.id} type="button" onClick={() => onOpen(r.id)} className="inline-flex items-center gap-1 rounded-md border border-border bg-panel2 px-1.5 py-0.5 text-xs font-semibold text-ink hover:border-muted/70" title={r.label}>
            {r.tier && <TierChip tier={r.tier} size="xs" />}
            <span className="mono">{r.id}</span>
          </button>
        ) : (
          <span key={r.id} className="mono rounded-md border border-border bg-panel2 px-1.5 py-0.5 text-xs text-muted" title={r.label}>{r.id}</span>
        ),
      )}
      {p.ms !== undefined && <span className="text-xs text-faint">{formatMs(p.ms)}</span>}
    </div>
  );
}

export function ToolRow({ c, verdict }: { c: ToolCallState; verdict?: string }) {
  const harmful = c.tool === 'pay_invoice' && c.status === 'executed' && verdict === 'attacker_paid';
  const goodPay = c.tool === 'pay_invoice' && c.status === 'executed' && verdict === 'verified_paid';
  const blocked = c.status === 'blocked';
  const tone = harmful ? 'border-danger bg-danger/15' : blocked ? 'border-danger/60 bg-danger/10' : goodPay ? 'border-ok/60 bg-ok/10' : 'border-border bg-panel2/70';
  const chip =
    c.status === 'proposed' ? { t: 'PROPOSED', cls: 'text-info' }
    : c.status === 'executed' ? { t: 'EXECUTED', cls: harmful ? 'text-dangerink' : goodPay ? 'text-ok' : 'text-ink' }
    : c.status === 'blocked' ? { t: 'BLOCKED', cls: 'text-dangerink' }
    : c.status === 'asked' ? { t: 'ASK USER', cls: 'text-warn' }
    : { t: 'DENIED', cls: 'text-dangerink' };
  return (
    <div className={cx('animate-fade-in rounded-xl border px-3 py-2.5 transition-colors duration-500', tone)}>
      <div className="flex items-center gap-2.5">
        <Wrench size={18} className={harmful || blocked ? 'text-dangerink' : 'text-muted'} />
        <code className="mono min-w-0 flex-1 break-words text-[0.9375rem] font-semibold text-ink">{formatCall(c)}</code>
        {c.risk === 'high' && <span className="rounded border border-border px-1.5 text-[0.6875rem] font-bold tracking-wide text-muted">HIGH-RISK</span>}
        <span className={cx('text-sm font-extrabold tracking-wide', chip.cls)}>{chip.t}</span>
      </div>
      {c.result && c.status !== 'proposed' && <p className="mt-1 pl-7 text-sm text-muted">{c.result}</p>}
    </div>
  );
}

export function GateRow({ g, onOpen }: { g: GateDecision; onOpen: () => void }) {
  const allow = g.decision === 'allow';
  const ask = g.decision === 'ask';
  const bad = g.args.filter((a) => a.critical && !a.ok);
  const Icon = allow ? ShieldCheck : ask ? ShieldQuestion : OctagonX;
  return (
    <button
      type="button"
      onClick={onOpen}
      title="Open the gate decision card"
      className={cx(
        'animate-pop-in flex w-full items-start gap-3 rounded-xl border px-3.5 py-3 text-left transition-colors',
        allow ? 'border-ok/60 bg-ok/10 hover:bg-ok/15' : ask ? 'border-warn/70 bg-warn/12 hover:bg-warn/18' : 'border-danger/80 bg-danger/14 hover:bg-danger/20',
      )}
    >
      <Icon size={26} className={allow ? 'text-ok' : ask ? 'text-warn' : 'text-dangerink'} strokeWidth={2.2} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className={cx('text-lg font-extrabold tracking-wide', allow ? 'text-ok' : ask ? 'text-warn' : 'text-dangerink')}>{allow ? 'ALLOW' : ask ? 'ASK USER' : 'BLOCKED'}</span>
          <span className="mono text-sm text-muted">{g.tool}</span>
          <span className="ml-auto text-xs text-faint">gate · {formatMs(g.ms)}</span>
        </div>
        <p className="mt-0.5 text-sm text-ink">
          {allow
            ? <>every critical argument is <TierChip tier="T3" size="xs" /> (system of record)</>
            : <>{bad.map((a) => a.name).join(', ')} traced to <span className="mono">{[...new Set(bad.flatMap((a) => a.sources.map((s) => s.record_id)))].join(', ')}</span> <TierChip tier={bad[0]?.min_tier ?? 'T0'} size="xs" />, needs <TierChip tier={bad[0]?.required_tier ?? 'T3'} size="xs" /></>}
        </p>
        {!allow && g.loo?.decisive[0] && <p className="mt-0.5 text-sm text-muted">decisive memory: <span className="mono font-semibold text-ink">{g.loo.decisive.join(', ')}</span></p>}
      </div>
    </button>
  );
}

export function SessionOutcomeBlock({ side, replay }: { side: Side; replay: ReplayState }) {
  const o = replay.sides[side].outcome;
  if (!o) return null;
  const moved = typeof o.money_moved === 'number' ? o.money_moved : o.money_moved ? 1 : 0;
  if (o.verdict === 'attacker_paid') {
    return (
      <div className="animate-banner-in origin-top rounded-xl border-2 border-danger bg-danger px-4 py-4 text-center text-onfill shadow-[0_0_0_4px_rgb(var(--danger)/0.22)]" role="alert">
        <div className="text-[1.65rem] font-black leading-tight tracking-wide text-white">{o.headline ?? `${formatINR(moved)} SENT TO ATTACKER`}</div>
        <div className="mt-1 text-sm font-semibold text-white/90">{o.detail ?? `to ${o.to_account ?? 'an unverified account'}`}</div>
      </div>
    );
  }
  if (o.verdict === 'blocked' || o.verdict === 'held') {
    return (
      <div className="animate-banner-in origin-top rounded-xl border-2 border-warn/80 bg-warn/12 px-4 py-3 text-center">
        <div className="flex items-center justify-center gap-2 text-xl font-extrabold text-warn"><ShieldAlert size={24} /> {o.headline ?? 'Payment blocked'}</div>
        {o.detail && <div className="mt-0.5 text-sm text-ink">{o.detail}</div>}
      </div>
    );
  }
  return (
    <div className="animate-banner-in origin-top rounded-xl border-2 border-ok/80 bg-ok/12 px-4 py-3 text-center">
      <div className="flex items-center justify-center gap-2 text-xl font-extrabold text-ok"><ShieldCheck size={24} /> {o.headline ?? 'Verified account paid'}{o.to_account ? ` · ${o.to_account}` : ''}</div>
      {o.detail && <div className="mt-0.5 text-sm text-ink">{o.detail}</div>}
    </div>
  );
}


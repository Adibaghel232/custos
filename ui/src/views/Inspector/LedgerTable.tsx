import { useEffect, useMemo, useRef, useState } from 'react';
import { BadgeCheck, FlaskConical, Loader2, RotateCcw, ShieldAlert, ShieldCheck } from 'lucide-react';
import type { LedgerEntry } from '../../types';
import { getLedger, postLedgerVerify, postTamperDemo } from '../../api';
import type { Source, TamperResponse } from '../../api';
import type { VerifyResult } from '../../lib/ledger';
import { useData, useReplay } from '../../state/app';
import { useActions } from '../../state/actions';
import { useUi } from '../../state/ui';
import { cx, shortHash } from '../../lib/format';
import { memoryLabel } from '../../lib/labels';
import { Button, Pill } from '../../components/ui';

const EVENT_TONE: Record<LedgerEntry['event'], 'neutral' | 'ok' | 'warn' | 'danger' | 'info'> = {
  WRITE: 'info', DECIDE: 'neutral', READ: 'neutral', GATE: 'warn', REVERT: 'danger', ERASE: 'danger', PROMOTE: 'ok',
};

/** Ledger as visible at the replay cursor: the store's chain, cut at the newest entry the replay has announced. */
export function useLedgerEntries(): { entries: LedgerEntry[]; source: Source | null } {
  const { replay, engine } = useReplay();
  const { trace, markFixture } = useData();
  const { ui } = useUi();
  const [stored, setStored] = useState<LedgerEntry[]>([]);
  const [source, setSource] = useState<Source | null>(null);
  useEffect(() => {
    let dead = false;
    getLedger().then((r) => { if (!dead) { setStored(r.data); setSource(r.source); markFixture(r.source); } }).catch(() => undefined);
    return () => { dead = true; };
  }, [trace, ui.storeVersion, markFixture]);
  const firstTraceSeq = useMemo(() => {
    let min = Infinity;
    for (const e of engine.events) if (e.type === 'ledger') min = Math.min(min, (e.payload as LedgerEntry).seq);
    return min;
  }, [engine.events]);
  const entries = useMemo(() => {
    const live = !!trace?.live;
    const ceil = live || firstTraceSeq === Infinity ? Infinity : Math.max(replay.maxLedgerSeq, firstTraceSeq - 1);
    const map = new Map<number, LedgerEntry>();
    for (const e of stored) if (e.seq <= ceil) map.set(e.seq, e);
    for (const e of replay.ledger) map.set(e.seq, e);
    return [...map.values()].sort((a, b) => a.seq - b.seq);
  }, [stored, replay.ledger, replay.maxLedgerSeq, firstTraceSeq, trace?.live]);
  return { entries, source };
}

export function LedgerTable() {
  const { entries } = useLedgerEntries();
  const { replay } = useReplay();
  const { openRecord } = useActions();
  const { markFixture } = useData();
  const { toast } = useUi();
  const [phase, setPhase] = useState<'idle' | 'running' | 'done'>('idle');
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<(VerifyResult & { source: Source; tampered?: boolean }) | null>(null);
  const [copy, setCopy] = useState<LedgerEntry[] | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  // any change to the visible chain invalidates an old verification result
  useEffect(() => { setResult(null); setCopy(null); setPhase('idle'); }, [entries.length]);

  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [entries.length, copy]);

  const run = async (kind: 'verify' | 'tamper') => {
    setPhase('running');
    setProgress(0);
    setCopy(null);
    setResult(null);
    const started = performance.now();
    const duration = 1100;
    const tick = window.setInterval(() => setProgress(Math.min(0.96, (performance.now() - started) / duration)), 40);
    try {
      let res: VerifyResult & { source: Source; tampered?: boolean };
      let copyRows: LedgerEntry[] | null = null;
      if (kind === 'verify') {
        const r = await postLedgerVerify(entries);
        res = { ...r.data, source: r.source };
      } else {
        const r = await postTamperDemo(entries);
        const data: TamperResponse = r.data;
        res = { ok: data.ok, entries: data.entries, first_bad_seq: data.first_bad_seq, ms: data.ms, source: r.source, tampered: true };
        copyRows = data.copy ?? null;
      }
      markFixture(res.source);
      const wait = Math.max(0, duration - (performance.now() - started));
      await new Promise((r) => window.setTimeout(r, wait));
      setProgress(1);
      setResult(res);
      setCopy(copyRows);
      setPhase('done');
    } catch (e) {
      setPhase('idle');
      toast('error', `Ledger check failed: ${(e as Error).message}`);
    } finally {
      window.clearInterval(tick);
    }
  };

  const rows = copy ?? entries;
  const subject = (e: LedgerEntry): string => {
    if (!e.record_id) return '';
    if (e.event === 'READ') {
      const r = replay.reads.find((x) => x.read_id === e.record_id);
      return r ? `S${r.session} read ${r.returned.join(', ')}` : e.record_id;
    }
    if (e.event === 'GATE') {
      const g = replay.gates.find((x) => x.gate.call_id === e.record_id);
      return g ? `${g.gate.tool} ${g.gate.decision.toUpperCase()}` : e.record_id;
    }
    const rec = replay.sides.custos.records[e.record_id];
    return rec ? `${e.record_id}  ${memoryLabel(rec)}` : e.record_id;
  };
  const isRecord = (e: LedgerEntry) => !!e.record_id && e.record_id in replay.sides.custos.records && e.event !== 'READ' && e.event !== 'GATE';
  const bad = result && !result.ok ? result.first_bad_seq : null;
  const tamperedSeq = copy && bad !== null ? bad : null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-2">
        <span className="text-base font-semibold text-ink">{entries.length} entries</span>
        <span className="text-sm text-muted">hash-chained · each entry commits to the one before it</span>
        <div className="ml-auto flex items-center gap-2">
          {phase === 'running' && (
            <div className="flex items-center gap-2" aria-live="polite">
              <div className="h-2.5 w-44 overflow-hidden rounded-full bg-border"><div className="h-full rounded-full bg-info transition-[width] duration-100" style={{ width: `${Math.round(progress * 100)}%` }} /></div>
              <Loader2 size={18} className="animate-spin text-muted" />
            </div>
          )}
          {phase === 'done' && result && (
            result.ok ? (
              <Pill tone="ok" className="!text-sm animate-pop-in"><ShieldCheck size={17} className="text-ok" /> ✓ {result.entries} entries verified · {result.ms} ms{result.source === 'fixture' ? ' · fixture' : ''}</Pill>
            ) : (
              <Pill tone="danger" className="!text-sm animate-pop-in"><ShieldAlert size={17} className="text-dangerink" /> ✗ broken at #{result.first_bad_seq}{result.tampered ? ' · edited copy, real ledger untouched' : ''}</Pill>
            )
          )}
          {copy && <Button variant="ghost" icon={RotateCcw} onClick={() => { setCopy(null); setResult(null); setPhase('idle'); }}>Back to real ledger</Button>}
          <Button icon={FlaskConical} onClick={() => void run('tamper')} disabled={phase === 'running' || entries.length < 2} title="Edits one entry in a COPY of the ledger and re-verifies, so detection is visible without touching the real ledger">Tamper demo</Button>
          <Button variant="primary" icon={BadgeCheck} onClick={() => void run('verify')} disabled={phase === 'running' || entries.length === 0}>Verify chain</Button>
        </div>
      </div>
      <div ref={scroller} className="min-h-0 flex-1 overflow-auto" onScroll={() => { const el = scroller.current; if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60; }}>
        <table className="w-full min-w-[46rem] border-separate border-spacing-0 text-left text-sm">
          <thead className="sticky top-0 z-10 bg-panel text-xs font-semibold uppercase tracking-wide text-muted">
            <tr><th className="px-4 py-1.5">#</th><th className="px-2 py-1.5">Event</th><th className="px-2 py-1.5">Subject</th><th className="px-2 py-1.5">Actor</th><th className="px-2 py-1.5">prev → hash</th><th className="px-4 py-1.5 text-center">Sig</th></tr>
          </thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={6} className="px-4 py-6 text-center text-muted">The ledger fills in as the replay writes records.</td></tr>}
            {rows.map((e) => {
              const broken = tamperedSeq === e.seq || (bad === e.seq);
              return (
                <tr key={e.seq} className={cx('animate-flash border-b border-border/50', broken && '!bg-danger/20')}>
                  <td className="mono px-4 py-1.5 font-semibold text-ink">#{e.seq}</td>
                  <td className="px-2 py-1.5"><Pill tone={EVENT_TONE[e.event]}>{e.event}</Pill></td>
                  <td className="max-w-[24rem] truncate px-2 py-1.5 text-ink">
                    {isRecord(e) ? <button type="button" className="text-left hover:underline" onClick={() => openRecord(e.record_id!, { goto: false })}>{subject(e)}</button> : subject(e)}
                  </td>
                  <td className="px-2 py-1.5 text-muted">{e.actor}</td>
                  <td className="mono whitespace-nowrap px-2 py-1.5 text-xs text-muted">{shortHash(e.prev_hash, 4, 0)}→<span className="text-ink">{shortHash(e.hash, 4, 0)}</span></td>
                  <td className="px-4 py-1.5 text-center">{e.sig_ok ? <span className="font-semibold text-ok">✓ signed</span> : <span className="font-bold text-dangerink">✗ invalid</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

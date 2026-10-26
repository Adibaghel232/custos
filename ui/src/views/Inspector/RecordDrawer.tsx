import { useEffect, useState } from 'react';
import { BadgeCheck, CircleHelp, Fingerprint, History, ShieldAlert, Undo2, X } from 'lucide-react';
import type { MemoryRecord } from '../../types';
import { getRecord } from '../../api';
import { checkSeverity } from '../../tracePayloads';
import { useData, useReplay } from '../../state/app';
import { useUi } from '../../state/ui';
import { useActions } from '../../state/actions';
import { cx, formatISTTime, shortHash } from '../../lib/format';
import { displayText } from '../../lib/labels';
import { ruleDoc } from '../../lib/policy';
import { tierName } from '../../lib/tiers';
import { Button, CopyHash, StatusBadge, TierChip, Tip } from '../../components/ui';

const KIND_LABEL: Record<MemoryRecord['kind'], string> = { fact: 'fact', preference: 'preference', event: 'event', procedure: 'procedure', summary: 'summary (derived)' };

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[6.2rem_1fr] items-baseline gap-x-3 py-1">
      <dt className="text-xs font-semibold uppercase tracking-wide text-muted">{k}</dt>
      <dd className="min-w-0 break-words text-[0.9375rem] text-ink">{children}</dd>
    </div>
  );
}

/** Replay state first (it is time-consistent); otherwise ask the store (GET /api/records/{id}). */
function useRecord(id: string | null): MemoryRecord | null {
  const { replay } = useReplay();
  const { markFixture } = useData();
  const [fetched, setFetched] = useState<MemoryRecord | null>(null);
  const local = id ? replay.sides.custos.records[id] : undefined;
  useEffect(() => {
    setFetched(null);
    if (!id || local) return;
    let dead = false;
    getRecord(id).then((r) => { if (!dead) { setFetched(r.data); markFixture(r.source); } }).catch(() => undefined);
    return () => { dead = true; };
  }, [id, local, markFixture]);
  return local ?? fetched;
}

export function RecordDrawer() {
  const { ui, dispatch } = useUi();
  const { replay } = useReplay();
  const { openRecord, startRollback } = useActions();
  const rec = useRecord(ui.selectedId);
  const ledgerEntry = rec ? replay.ledger.find((l) => l.seq === rec.ledger_seq) : undefined;

  if (!ui.selectedId) {
    return (
      <aside className="card flex h-full min-h-0 flex-col items-center justify-center gap-2 p-6 text-center text-muted">
        <Fingerprint size={34} className="text-faint" />
        <div className="text-lg font-semibold text-ink/85">Select a record</div>
        <p className="max-w-[18rem] text-sm">Click a node in the custody graph to see its provenance, the checks it passed and the decision that was made.</p>
      </aside>
    );
  }
  if (!rec) {
    return (
      <aside className="card flex h-full items-center justify-center p-6 text-muted">
        <div className="text-center"><CircleHelp size={30} className="mx-auto text-faint" /><div className="mt-2 text-sm">{ui.selectedId} is not a memory record (sources have no drawer).</div></div>
      </aside>
    );
  }
  const poisonable = rec.status !== 'reverted' && rec.status !== 'erased' && rec.status !== 'rejected';
  const sigOk = ledgerEntry ? ledgerEntry.sig_ok : rec.sig_ok;
  const claimCount = rec.claims?.length ?? 0;
  const instructions = rec.claims?.filter((c) => c.type === 'procedure').length ?? 0;

  return (
    <aside className="card flex h-full min-h-0 flex-col overflow-hidden" aria-label={`Record ${rec.id}`}>
      <div className="flex items-center gap-3 border-b border-border px-4 py-2.5">
        <span className="caps !text-[0.8125rem] text-ink/90">Record</span>
        <span className="mono text-lg font-bold">{rec.id}</span>
        <span className="ml-auto"><StatusBadge status={rec.status} size="md" /></span>
        <button type="button" aria-label="Close drawer" className="text-muted hover:text-ink" onClick={() => dispatch({ type: 'select', id: null })}><X size={19} /></button>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
        <blockquote className={cx('rounded-lg border-l-4 bg-panel2 px-3.5 py-2.5 text-lg font-medium leading-snug', rec.status === 'erased' ? 'border-reverted text-revertedink' : 'border-muted/60')}>
          “{displayText(rec)}”
        </blockquote>
        <dl>
          <Row k="tier">
            {rec.tier ? <span className="inline-flex items-center gap-2"><TierChip tier={rec.tier} size="md" /><span className="text-muted">{tierName(rec.tier)}</span></span> : <span className="text-muted">none (no custody)</span>}
          </Row>
          <Row k="kind">{KIND_LABEL[rec.kind]}{instructions > 0 && <span className="text-muted"> (+{instructions} instruction{instructions > 1 ? 's' : ''})</span>}</Row>
          <Row k="source">
            <span className="font-semibold">{rec.source.type}</span> <button type="button" className="mono text-sm text-info hover:underline" onClick={() => dispatch({ type: 'select', id: rec.source.ref })}>{rec.source.ref}</button>
            {rec.source.from && <div className="truncate text-sm text-muted">{rec.source.from}</div>}
          </Row>
          <Row k="principal"><span className="mono text-sm">{rec.principal}</span></Row>
          <Row k="session">
            S{rec.session}
            {rec.parents.length > 0 && (
              <span className="text-muted"> · parents{' '}
                {rec.parents.map((p, i) => (
                  <span key={p}>{i > 0 && ', '}<button type="button" className="mono text-sm text-info hover:underline" onClick={() => openRecord(p, { goto: false })}>{p}</button></span>
                ))}
              </span>
            )}
          </Row>
          <Row k="raw sha256"><CopyHash value={rec.raw_hash} short={shortHash(rec.raw_hash, 6, 4)} /></Row>
          <Row k="ledger">
            {rec.ledger_seq > 0 ? (
              <span className="inline-flex items-center gap-2">
                <span className="mono">#{rec.ledger_seq}</span>
                <span className={cx('inline-flex items-center gap-1 font-semibold', sigOk ? 'text-ok' : 'text-dangerink')}>
                  {sigOk ? <BadgeCheck size={17} /> : <ShieldAlert size={17} />}{sigOk ? 'signed' : 'signature invalid'}
                </span>
              </span>
            ) : <span className="text-muted">none (undefended store)</span>}
          </Row>
          {rec.conflicts_with && (
            <Row k="conflicts">
              <button type="button" onClick={() => openRecord(rec.conflicts_with!.id, { goto: false })} className="inline-flex items-center gap-2 text-left hover:underline">
                <TierChip tier={rec.conflicts_with.tier} size="xs" /><span className="mono text-sm">{rec.conflicts_with.id}</span><span className="text-sm text-muted">{rec.conflicts_with.text}</span>
              </button>
            </Row>
          )}
        </dl>

        {claimCount > 0 && (
          <section>
            <h3 className="caps mb-1.5">Claims</h3>
            <ul className="space-y-1">
              {rec.claims!.map((c) => (
                <li key={c.id} className="flex items-baseline gap-2 text-sm">
                  <span className={cx('rounded px-1.5 text-xs font-bold', c.type === 'procedure' ? 'bg-t0/25 text-ink' : 'bg-panel2 text-muted')}>{c.type}</span>
                  <span className="min-w-0 break-words text-ink">{c.s}.{c.r} = {c.o}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section>
          <h3 className="caps mb-1.5">Checks</h3>
          {rec.checks && rec.checks.length > 0 ? (
            <ul className="divide-y divide-border/60 rounded-lg border border-border">
              {rec.checks.map((c, i) => {
                const sev = checkSeverity(c);
                return (
                  <li key={i} className="flex items-start gap-3 px-3 py-2">
                    <span className={cx('mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full', sev === 'flag' ? 'bg-warn' : sev === 'info' ? 'bg-info' : 'bg-ok')} aria-hidden />
                    <span className="w-[5.6rem] shrink-0 text-sm font-semibold text-ink">{c.check}</span>
                    <span className="min-w-0 flex-1 text-sm text-ink">
                      {sev === 'flag' ? '⚠ ' : sev === 'ok' ? '✓ ' : ''}{c.result}
                      <span className="text-muted">{c.score !== undefined ? ` · ${c.score.toFixed(2)}` : ''}{c.ms !== undefined ? ` · ${c.ms} ms` : ''}</span>
                      {c.model && <span className="block truncate text-xs text-faint">{c.model}</span>}
                    </span>
                  </li>
                );
              })}
            </ul>
          ) : <p className="text-sm text-muted">No checks recorded{rec.tier ? '.' : ' (undefended store: nothing is checked).'}</p>}
        </section>

        {rec.decision && (
          <section>
            <h3 className="caps mb-1.5">Decision</h3>
            <div className="rounded-lg border border-border bg-panel2 px-3.5 py-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className={cx('rounded-md px-2 py-0.5 text-sm font-extrabold uppercase tracking-wide', rec.decision.verdict === 'quarantine' ? 'bg-warn/25 text-ink' : rec.decision.verdict === 'reject' ? 'bg-danger/25 text-ink' : 'bg-ok/22 text-ink')}>{rec.decision.verdict}</span>
                <Tip wide content={(() => { const d = ruleDoc(rec.decision!.rule); return d ? <div><div className="font-semibold">{d.title}</div><p className="mt-1 text-muted">{d.text}</p><pre className="mono mt-2 whitespace-pre-wrap rounded bg-bg/70 p-2 text-xs">{d.code}</pre></div> : <span>{rec.decision!.rule}</span>; })()}>
                  <span className="mono cursor-help border-b border-dotted border-muted text-sm text-ink">{rec.decision.rule}</span>
                </Tip>
              </div>
              <p className="mt-1.5 text-sm leading-snug text-muted">{rec.decision.explanation}</p>
            </div>
          </section>
        )}

        {rec.status_history && rec.status_history.length > 0 && (
          <section>
            <h3 className="caps mb-1.5 flex items-center gap-1.5"><History size={14} /> Status history</h3>
            <ol className="space-y-1">
              {rec.status_history.map((h, i) => (
                <li key={i} className="flex items-center gap-2 text-sm text-muted">
                  <span className="mono w-[4.6rem] text-xs">{formatISTTime(h.ts)}</span><StatusBadge status={h.status} size="xs" /><span className="truncate text-xs">by {h.by}</span>
                </li>
              ))}
            </ol>
          </section>
        )}
      </div>
      <div className="border-t border-border p-3">
        <Button variant="danger" icon={Undo2} className="w-full !py-2.5 text-base" disabled={!poisonable || !rec.tier} onClick={() => void startRollback(rec.id, ui.rollback?.mode ?? 'revert')} kbd="R">
          Mark poison &amp; roll back
        </Button>
      </div>
    </aside>
  );
}

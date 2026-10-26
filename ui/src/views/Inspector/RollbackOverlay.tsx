import { AlertTriangle, BadgeCheck, Download, Loader2, RefreshCw, Undo2, X } from 'lucide-react';
import type { RollbackResult } from '../../types';
import { useReplay } from '../../state/app';
import { useUi } from '../../state/ui';
import { useActions } from '../../state/actions';
import { cx, formatISTTime } from '../../lib/format';
import { displayText } from '../../lib/labels';
import { Button, Pill, Segmented, StatusIcon, TierChip } from '../../components/ui';

function downloadJson(name: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function RollbackOverlay() {
  const { ui } = useUi();
  const { replay } = useReplay();
  const { startRollback, closeRollback } = useActions();
  const rb = ui.rollback;
  if (!rb) return null;
  const erase = rb.mode === 'erase';
  const result: RollbackResult | undefined = rb.result;
  const records = replay.sides.custos.records;
  const extraRecs = rb.extras?.records ?? [];
  const recOf = (id: string) => records[id] ?? extraRecs.find((r) => r.id === id);
  const ledgerEntry = result ? [...(rb.extras?.ledger ?? []), ...replay.ledger].find((l) => l.seq === result.ledger_seq) : undefined;
  const derived = result ? result.closure_by_hop.slice(1).flat() : [];
  const total = result ? result.closure_by_hop.flat().length : 0;
  const rederivedOf = (id: string) => result?.rederived.find((x) => x.old === id)?.new;
  const exposure = result?.exposure;

  const exportReport = () => {
    if (!result) return;
    downloadJson(`exposure_${result.root}_${result.mode}.json`, {
      root: result.root, mode: result.mode, tenant: 'northstar-demo',
      closure_by_hop: result.closure_by_hop.map((ids, hop) => ids.map((id) => ({ hop, id, text: recOf(id) ? displayText(recOf(id)!) : undefined }))).flat(),
      rederived: result.rederived,
      exposure: result.exposure,
      ledger_seq: result.ledger_seq,
      ledger_entry: ledgerEntry ?? null,
      source: rb.source,
    });
  };

  return (
    <section className="card flex h-[min(44%,21rem)] min-h-[15rem] shrink-0 animate-slide-up flex-col overflow-hidden border-danger/55" role="dialog" aria-label="Rollback">
      <>
        <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-panel px-5 py-2.5">
          <Undo2 size={26} className="text-dangerink" />
          <h2 className="text-2xl font-extrabold tracking-wide">ROLL BACK <span className="mono">{rb.root}</span></h2>
          <div className="flex items-center gap-2 text-sm text-muted">
            mode
            <Segmented<'revert' | 'erase'> label="Rollback mode" size="sm" value={rb.mode}
              onChange={(m) => { if (m !== rb.mode) void startRollback(rb.root, m); }}
              options={[{ value: 'revert', label: 'Revert', title: 'Mark the poison and everything derived from it as reverted; re-derive clean summaries' }, { value: 'erase', label: 'DPDP erase', title: 'Same closure, but content is erased (DPDP Act §12). Hashes stay in the ledger.' }]} />
          </div>
          {rb.source === 'fixture' && <Pill tone="warn" title="The backend was not reachable: this result is the recorded fixture, not a live call.">FIXTURE RESULT</Pill>}
          <button type="button" aria-label="Close" onClick={closeRollback} className="ml-auto rounded-lg p-1.5 text-muted hover:bg-panel2 hover:text-ink"><X size={22} /></button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-3">
          {rb.phase === 'loading' && <div className="flex items-center gap-3 text-lg text-muted"><Loader2 className="animate-spin" /> Computing the taint closure over the provenance graph…</div>}
          {rb.phase === 'error' && (
            <div className="flex flex-wrap items-center gap-4 text-base">
              <AlertTriangle className="text-warn" /> <span className="text-ink">{rb.error}</span>
              <Button icon={RefreshCw} onClick={() => void startRollback(rb.root, rb.mode)}>Retry</Button>
            </div>
          )}
          {result && (
            <div className="grid gap-x-6 gap-y-3 xl:grid-cols-[minmax(0,1.55fr)_minmax(22rem,1fr)]">
              <div className="min-w-0">
              <div className="caps mb-1.5">Taint closure over the provenance graph</div>
              <ol className="space-y-1">
                {result.closure_by_hop.map((ids, hop) => ids.map((id) => {
                  const shown = hop < rb.hop || rb.phase === 'done';
                  const r = recOf(id);
                  const nn = rederivedOf(id);
                  return (
                    <li key={id} className={cx('grid grid-cols-[3.6rem_3.2rem_1fr_auto] items-center gap-x-3 rounded-lg border px-3 py-1.5 transition-all duration-500', shown ? 'border-border bg-panel2 opacity-100' : 'border-transparent opacity-30')}>
                      <span className="text-sm font-semibold text-muted">hop {hop}</span>
                      <span className="mono text-base font-bold text-ink">{id}</span>
                      <span className="flex min-w-0 items-center gap-2 text-[0.9375rem] text-ink">
                        {r?.tier && <TierChip tier={r.tier} size="xs" />}
                        <span className={cx('truncate', shown && 'text-revertedink')}>{r ? displayText(r) : '…'}</span>
                      </span>
                      <span className="flex items-center gap-3 whitespace-nowrap">
                        {shown && <span className="inline-flex items-center gap-1.5 text-base font-bold text-revertedink animate-fade-in"><StatusIcon status={erase ? 'erased' : 'reverted'} size={19} /> {erase ? 'erased' : 'reverted'}</span>}
                        {nn && rb.rederiveShown && <span className="animate-pop-in rounded-md border border-ok/60 bg-ok/12 px-2 py-0.5 text-sm font-semibold text-ink">→ re-derived clean as <span className="mono font-bold">{nn}</span></span>}
                      </span>
                    </li>
                  );
                }))}
              </ol>
              </div>
              <div className="min-w-0">
              {erase && rb.exposureShown && (
                <p className="mb-3 animate-slide-up rounded-lg border border-info/50 bg-info/10 px-4 py-2.5 text-base font-semibold text-ink">
                  Erasure completeness 100% ({total}/{total} closure records) · {derived.length} derived record{derived.length === 1 ? '' : 's'} removed · hashes kept in the ledger, content gone.
                </p>
              )}

              {rb.exposureShown && exposure && (
                <div className="animate-slide-up rounded-xl border border-border bg-bg/50 px-4 py-3">
                  <div className="caps mb-1.5">Exposure (from the read log)</div>
                  <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-lg text-ink">
                    <span>sessions {exposure.sessions.length ? exposure.sessions.map((s) => <b key={s} className="mr-1.5">S{s}</b>) : <b>none</b>}</span>
                    <span>users {exposure.users.length ? exposure.users.map((u) => <b key={u} className="mono mr-1.5 text-base">{u}</b>) : <b>none</b>}</span>
                    <span>reads <b>{exposure.reads}</b></span>
                    <span>
                      tool calls{' '}
                      <b>{exposure.tool_calls.filter((t) => t.status === 'blocked').length} blocked</b>, <b>{exposure.tool_calls.filter((t) => t.status === 'executed').length} executed</b>
                      {exposure.tool_calls.filter((t) => t.status === 'asked').length > 0 && <>, <b>{exposure.tool_calls.filter((t) => t.status === 'asked').length} asked</b></>}
                    </span>
                  </div>
                  {exposure.tool_calls.length > 0 && (
                    <ul className="mt-1.5 flex flex-wrap gap-2 text-sm text-muted">
                      {exposure.tool_calls.map((t) => <li key={t.call_id} className="chip"><span className="mono">{t.tool}</span> · {t.status}</li>)}
                    </ul>
                  )}
                </div>
              )}
              {!rb.exposureShown && <div className="rounded-xl border border-dashed border-border px-4 py-6 text-sm text-faint">Exposure report loads when the closure is complete…</div>}
              </div>
            </div>
          )}
        </div>

        {result && (
          <div className="flex shrink-0 flex-wrap items-center gap-3 border-t border-border px-5 py-2.5">
            <span className="text-sm text-muted">
              Ledger <span className="mono font-semibold text-ink">#{result.ledger_seq}</span> {erase ? 'ERASE' : 'REVERT'}{' '}
              {rb.phase !== 'done' ? <Loader2 size={15} className="inline animate-spin" /> : ledgerEntry?.sig_ok ? <span className="inline-flex items-center gap-1 font-semibold text-ok"><BadgeCheck size={16} /> signed {formatISTTime(ledgerEntry.ts)}</span> : <span className="font-semibold text-ink">recorded</span>}
            </span>
            <span className="ml-auto flex gap-2">
              <Button icon={Download} onClick={exportReport} disabled={rb.phase !== 'done'}>Export exposure report</Button>
              <Button variant="primary" onClick={closeRollback}>Close</Button>
            </span>
          </div>
        )}
      </>
    </section>
  );
}

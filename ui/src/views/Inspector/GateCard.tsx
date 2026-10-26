import { ArrowDownToLine, Check, Mail, Database, Network, OctagonX, ShieldCheck, ShieldQuestion, Undo2, X, Wrench } from 'lucide-react';
import type { GateArg, GateDecision } from '../../types';
import { useReplay } from '../../state/app';
import { useUi } from '../../state/ui';
import { useActions } from '../../state/actions';
import { cx, formatMs } from '../../lib/format';
import { ruleDoc } from '../../lib/policy';
import { Button, TierChip, Tip } from '../../components/ui';

function SourceIcon({ type }: { type: string }) {
  if (type === 'email') return <Mail size={16} className="shrink-0 text-t0" />;
  if (type === 'erp') return <Database size={16} className="shrink-0 text-muted" />;
  return <Wrench size={16} className="shrink-0 text-muted" />;
}

function RuleId({ rule }: { rule: string }) {
  const d = ruleDoc(rule);
  return (
    <Tip wide side="bottom" content={d ? <div><div className="font-semibold">{d.title}</div><p className="mt-1 text-muted">{d.text}</p><pre className="mono mt-2 whitespace-pre-wrap rounded bg-bg/70 p-2 text-xs">{d.code}</pre></div> : <span>{rule}</span>}>
      <span className="mono cursor-help border-b border-dotted border-muted text-sm text-ink">{rule}</span>
    </Tip>
  );
}

export function GateCard({ gate, variant, onDock, onClose }: { gate: GateDecision; variant: 'modal' | 'docked'; onDock?: () => void; onClose?: () => void }) {
  const { replay } = useReplay();
  const { dispatch } = useUi();
  const { openRecord, startRollback } = useActions();
  const modal = variant === 'modal';
  const allow = gate.decision === 'allow';
  const ask = gate.decision === 'ask';
  const rec = (id: string) => replay.sides.custos.records[id];
  const decisive = gate.loo?.decisive[0];
  const decisiveRec = decisive ? rec(decisive) : undefined;
  const Icon = allow ? ShieldCheck : ask ? ShieldQuestion : OctagonX;
  const delay = (i: number) => (modal ? { animationDelay: `${i * 170}ms` } : undefined);
  const nonDecisive = gate.loo ? Math.max(0, gate.loo.replans - gate.loo.decisive.length) : 0;

  const focusRecord = (id: string) => {
    openRecord(id, { goto: false });
    dispatch({ type: 'gateOpen', open: false });
  };

  const row = (a: GateArg, i: number) => {
    const src = a.sources[0];
    const r = src ? rec(src.record_id) : undefined;
    const bad = a.critical && !a.ok;
    return (
      <tr key={a.name} className={cx(modal && 'animate-slide-up', bad && 'bg-danger/10')} style={delay(i)}>
        <td className={cx('pl-3 pr-3 text-base font-semibold text-ink', modal ? 'py-2' : 'py-1')}>{a.name}{a.critical && <span className="ml-1.5 text-[0.6875rem] font-bold tracking-wide text-muted">CRITICAL</span>}</td>
        <td className="py-2 pr-3 text-base font-medium text-ink">{a.value}</td>
        <td className="py-2 pr-3">
          {src ? (
            <button type="button" onClick={() => focusRecord(src.record_id)} className="inline-flex max-w-[17rem] items-center gap-2 text-left hover:underline" title={`Focus ${src.record_id} in the custody graph`}>
              <span className="mono text-sm font-bold text-ink">{src.record_id}</span>
              <SourceIcon type={src.source_type} />
              <span className="truncate text-sm text-muted">{src.from ?? src.source_type}</span>
              {a.sources.length > 1 && <span className="text-xs text-faint">+{a.sources.length - 1}</span>}
            </button>
          ) : <span className="text-sm text-faint">user turn / none</span>}
          {r && r.session > 0 && <span className="ml-1 text-xs text-faint">S{r.session}</span>}
        </td>
        <td className="py-2 pr-3">{a.min_tier ? <TierChip tier={a.min_tier} size="sm" /> : <span className="text-faint">-</span>}</td>
        <td className="py-2 pr-3">{a.required_tier ? <TierChip tier={a.required_tier} size="sm" /> : <span className="text-sm text-faint">n/a</span>}</td>
        <td className="py-2 pr-3 text-center">
          {a.required_tier === null ? <span className="text-faint">·</span> : a.ok
            ? <Check size={22} className="inline text-ok" strokeWidth={3} aria-label="ok" />
            : <X size={22} className="inline text-dangerink" strokeWidth={3} aria-label="fails the required tier" />}
        </td>
      </tr>
    );
  };

  return (
    <div className={cx('flex min-h-0 flex-col', modal ? 'w-[min(62rem,94vw)]' : 'h-full')}>
      <div className={cx('flex items-center gap-4 border-b px-5 py-3', allow ? 'border-ok/50 bg-ok/10' : ask ? 'border-warn/50 bg-warn/10' : 'border-danger/60 bg-danger/12')}>
        <Icon size={modal ? 38 : 30} className={allow ? 'text-ok' : ask ? 'text-warn' : 'text-dangerink'} strokeWidth={2.1} />
        <div className="min-w-0">
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5">
            <span className={cx('font-black tracking-wide', modal ? 'text-3xl' : 'text-2xl', allow ? 'text-ok' : ask ? 'text-warn' : 'text-dangerink')}>{allow ? 'ALLOWED' : ask ? 'ASK THE USER' : 'BLOCKED'}</span>
            <code className="mono text-lg font-semibold text-ink">{gate.tool}</code>
            {gate.risk === 'high' && <span className="rounded border border-border px-1.5 text-xs font-bold tracking-wide text-muted">HIGH-RISK</span>}
          </div>
          <div className="mt-0.5 text-sm text-muted">rule <RuleId rule={gate.rule} /> <span className="text-faint">· policy as code</span></div>
        </div>
        <span className="ml-auto text-sm text-muted">{formatMs(gate.ms)}</span>
        {modal && onClose && <button type="button" aria-label="Dock this card" title="Dock into the Inspector (Esc)" onClick={onClose} className="rounded-lg border border-border p-1.5 text-muted hover:text-ink"><ArrowDownToLine size={20} /></button>}
      </div>

      {(() => {
        const table = (
          <table className={cx('w-full border-separate border-spacing-0 text-left', !modal && '[&_td]:!py-1')}>
            <thead>
              <tr className="text-xs font-semibold uppercase tracking-wide text-muted">
                <th className="pb-1.5 pl-3 pr-3">Argument</th><th className="pb-1.5 pr-3">Value</th><th className="pb-1.5 pr-3">From memory</th><th className="pb-1.5 pr-3">Tier</th><th className="pb-1.5 pr-3">Needs</th><th className="pb-1.5 pr-3 text-center">OK</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50">{gate.args.map(row)}</tbody>
          </table>
        );
        const loo = gate.loo ? (
          <div className={cx('rounded-xl border border-border bg-panel2 px-4 py-3', modal ? 'mx-3 mt-3 animate-slide-up' : '')} style={delay(gate.args.length + 2)}>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-base text-ink">
              <span className="caps">Leave-one-out</span>
              <span>{gate.loo.replans} re-plans</span>
              <span className="text-muted">·</span>
              <span>without <span className="mono font-bold">{gate.loo.decisive.join(', ')}</span> → {Object.entries(gate.loo.counterfactual).map(([k, v]) => `${k} ${v}`).join(', ')}</span>
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-2 text-lg font-semibold text-ink">
              <span aria-hidden>⇒</span> decisive memory:
              <button type="button" className="mono rounded-md bg-t0/25 px-2 py-0.5 hover:bg-t0/35" onClick={() => decisive && focusRecord(decisive)}>{decisive}</button>
              {decisiveRec && <span className="text-base font-normal text-muted">({decisiveRec.source.type} {decisiveRec.source.ref}, session S{decisiveRec.session})</span>}
            </div>
            {nonDecisive > 0 && <p className="mt-1 text-sm text-muted">The other {nonDecisive} re-plan{nonDecisive > 1 ? 's' : ''} changed nothing: those memories still lead to the same account.</p>}
          </div>
        ) : null;
        const next = !allow ? <p className={cx('text-sm text-muted', modal && 'mx-3 mt-2 animate-slide-up')} style={delay(gate.args.length + 3)}>Next step: verify by call-back, then update the vendor in the ERP.</p> : null;
        const actions = (
          <div className={cx('flex flex-wrap items-center gap-2', modal ? 'border-t border-border px-4 py-3' : 'pt-1')}>
            {decisive && <Button icon={Network} onClick={() => focusRecord(decisive)}>Open custody trail</Button>}
            {decisive && !allow && <Button variant="danger" icon={Undo2} onClick={() => { dispatch({ type: 'gateOpen', open: false }); void startRollback(decisive, 'revert'); }}>Roll back {decisive}</Button>}
            {(onClose ?? onDock) && <Button variant="ghost" onClick={onClose ?? onDock}>{allow ? 'Close' : 'Keep blocked'}</Button>}
          </div>
        );
        if (modal) {
          return (
            <>
              <div className="min-h-0 flex-1 overflow-auto px-2 py-3">{table}{loo}{next}</div>
              {actions}
            </>
          );
        }
        return (
          <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-auto px-3 py-2 xl:grid-cols-[minmax(0,1.55fr)_minmax(19rem,1fr)]">
            <div className="min-w-0">{table}</div>
            <div className="flex min-w-0 flex-col gap-2">{loo}{next}{actions}</div>
          </div>
        );
      })()}
    </div>
  );
}

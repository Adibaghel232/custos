import { useEffect } from 'react';
import { OctagonX, ScrollText, ShieldCheck, ShieldQuestion, Pause } from 'lucide-react';
import { useReplay } from '../../state/app';
import { useUi } from '../../state/ui';
import type { DockTab } from '../../state/ui';
import { cx } from '../../lib/format';
import { CustodyGraph } from './CustodyGraph';
import { RecordDrawer } from './RecordDrawer';
import { GateCard } from './GateCard';
import { QuarantineQueue, useQuarantineList } from './QuarantineQueue';
import { LedgerTable, useLedgerEntries } from './LedgerTable';
import { RollbackOverlay } from './RollbackOverlay';

function GateModal() {
  const { ui, dispatch } = useUi();
  const { replay } = useReplay();
  const latest = replay.gates[replay.gates.length - 1];

  // pop the card when a block / ask decision arrives (or when we arrive here with an unseen one)
  useEffect(() => {
    if (!latest) { if (ui.gateSeenSeq !== 0) dispatch({ type: 'gateSeen', seq: 0 }); return; }
    if (latest.gate.decision !== 'allow' && latest.seq > ui.gateSeenSeq) {
      // mark as seen at pop time, so closing it in any way (Esc, buttons, backdrop) never re-opens it
      dispatch({ type: 'gateSeen', seq: latest.seq });
      dispatch({ type: 'gateOpen', open: true });
      dispatch({ type: 'dock', tab: 'gate' });
    }
  }, [latest, ui.gateSeenSeq, dispatch]);

  if (!ui.gateOpen || !latest) return null;
  const close = () => {
    dispatch({ type: 'gateOpen', open: false });
    dispatch({ type: 'gateSeen', seq: latest.seq });
    dispatch({ type: 'dock', tab: 'gate' });
  };
  return (
    <div className="absolute inset-0 z-50 grid place-items-center bg-black/55 p-6 backdrop-blur-[2px] animate-fade-in" onMouseDown={close}>
      <div className="card animate-pop-in overflow-hidden shadow-pop" onMouseDown={(e) => e.stopPropagation()}>
        <GateCard gate={latest.gate} variant="modal" onClose={close} />
      </div>
    </div>
  );
}

function Dock() {
  const { ui, dispatch } = useUi();
  const { replay } = useReplay();
  const held = useQuarantineList().length;
  const { entries } = useLedgerEntries();
  const latest = replay.gates[replay.gates.length - 1];
  const tabs: Array<{ id: DockTab; label: string; badge?: string; tone?: string }> = [
    { id: 'gate', label: 'Gate decision', badge: latest ? latest.gate.decision.toUpperCase() : undefined, tone: latest?.gate.decision === 'allow' ? 'text-ok' : latest?.gate.decision === 'ask' ? 'text-warn' : 'text-dangerink' },
    { id: 'quarantine', label: 'Quarantine', badge: String(held), tone: held ? 'text-warn' : 'text-muted' },
    { id: 'ledger', label: 'Ledger', badge: String(entries.length), tone: 'text-muted' },
  ];
  const GIcon = latest?.gate.decision === 'allow' ? ShieldCheck : latest?.gate.decision === 'ask' ? ShieldQuestion : OctagonX;
  return (
    <section className="card flex h-[min(44%,21rem)] min-h-[15rem] shrink-0 flex-col overflow-hidden" aria-label="Gate, quarantine and ledger">
      <div role="tablist" className="flex items-center gap-1 border-b border-border px-2 pt-1.5">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={ui.dockTab === t.id}
            onClick={() => dispatch({ type: 'dock', tab: t.id })}
            className={cx(
              'flex items-center gap-2 rounded-t-lg border-b-2 px-4 py-2 text-base font-semibold transition-colors',
              ui.dockTab === t.id ? 'border-ok bg-panel2 text-ink' : 'border-transparent text-muted hover:text-ink',
            )}
          >
            {t.id === 'gate' && <GIcon size={19} className={t.tone} />}
            {t.id === 'quarantine' && <Pause size={18} className={held ? 'text-warn' : 'text-faint'} />}
            {t.id === 'ledger' && <ScrollText size={18} className="text-muted" />}
            {t.label}
            {t.badge !== undefined && <span className={cx('rounded-md bg-bg/70 px-1.5 text-sm font-bold', t.tone)}>{t.badge}</span>}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-hidden" role="tabpanel">
        {ui.dockTab === 'gate' && (
          latest
            ? <div className="h-full overflow-auto"><GateCard gate={latest.gate} variant="docked" /></div>
            : <div className="grid h-full place-items-center p-6 text-center text-muted"><div><ShieldCheck size={30} className="mx-auto text-faint" /><div className="mt-2 text-lg font-semibold text-ink/85">No gated call yet</div><p className="mt-1 text-sm">The consequence gate checks every high-risk call (pay, send) in session 9.</p></div></div>
        )}
        {ui.dockTab === 'quarantine' && <div className="h-full overflow-auto"><QuarantineQueue /></div>}
        {ui.dockTab === 'ledger' && <LedgerTable />}
      </div>
    </section>
  );
}

export function InspectorView() {
  const { ui } = useUi();
  return (
    <div className="relative flex h-full flex-col gap-3 p-3 2xl:gap-4 2xl:p-4">
      <div className="flex min-h-0 flex-1 gap-3 2xl:gap-4">
        <div className="min-w-0 flex-1"><CustodyGraph /></div>
        <div className="w-[25rem] shrink-0 2xl:w-[30rem]"><RecordDrawer /></div>
      </div>
      {ui.rollback ? <RollbackOverlay /> : <Dock />}
      <GateModal />
    </div>
  );
}

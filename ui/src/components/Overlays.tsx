import { useEffect } from 'react';
import { CircleAlert, CircleCheck, Info, X } from 'lucide-react';
import { useUi } from '../state/ui';
import { SHORTCUTS } from '../state/shortcuts';
import { cx } from '../lib/format';
import { Button, Kbd, Toggle } from './ui';

export function Toasts() {
  const { ui, dispatch } = useUi();
  return (
    <div className="pointer-events-none fixed bottom-5 left-1/2 z-[150] flex w-[min(40rem,92vw)] -translate-x-1/2 flex-col gap-2">
      {ui.toasts.map((t) => {
        const Icon = t.kind === 'error' ? CircleAlert : t.kind === 'ok' ? CircleCheck : Info;
        return (
          <div
            key={t.id}
            role="status"
            className={cx(
              'pointer-events-auto flex items-start gap-3 rounded-xl border bg-panel2 px-4 py-3 text-sm shadow-pop animate-slide-up',
              t.kind === 'error' ? 'border-danger/70' : t.kind === 'ok' ? 'border-ok/60' : 'border-border',
            )}
          >
            <Icon size={20} className={cx('mt-0.5 shrink-0', t.kind === 'error' ? 'text-dangerink' : t.kind === 'ok' ? 'text-ok' : 'text-info')} />
            <p className="min-w-0 flex-1 leading-snug">{t.text}</p>
            <button type="button" aria-label="Dismiss" className="text-muted hover:text-ink" onClick={() => dispatch({ type: 'dismissToast', id: t.id })}><X size={18} /></button>
          </div>
        );
      })}
    </div>
  );
}

export function ShortcutHelp() {
  const { ui, dispatch, setMotionReduced } = useUi();
  useEffect(() => {
    if (!ui.helpOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' || e.key === '?') { e.stopPropagation(); dispatch({ type: 'help', open: false }); } };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [ui.helpOpen, dispatch]);
  if (!ui.helpOpen) return null;
  return (
    <div className="fixed inset-0 z-[180] grid place-items-center bg-black/55 p-6 backdrop-blur-[2px] animate-fade-in" onMouseDown={() => dispatch({ type: 'help', open: false })}>
      <div role="dialog" aria-label="Keyboard shortcuts" className="card w-[min(44rem,94vw)] animate-pop-in p-6" onMouseDown={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-2xl font-bold">Keyboard shortcuts</h2>
          <Button variant="ghost" icon={X} onClick={() => dispatch({ type: 'help', open: false })}>Close</Button>
        </div>
        <ul className="grid grid-cols-1 gap-x-8 gap-y-2 sm:grid-cols-2">
          {SHORTCUTS.map((s) => (
            <li key={s.what} className="flex items-center justify-between gap-4 border-b border-border/60 py-2">
              <span className="text-base text-ink">{s.what}</span>
              <span className="flex shrink-0 gap-1">{s.keys.map((k) => <Kbd key={k}>{k}</Kbd>)}</span>
            </li>
          ))}
        </ul>
        <div className="mt-5 flex items-center justify-between rounded-lg border border-border bg-panel2 px-4 py-3">
          <Toggle checked={!ui.motionReduced} onChange={(on) => setMotionReduced(!on)} label="Animations" hint="Overrides the OS reduced-motion setting for this console" />
          <span className="text-xs text-muted">Recording tip: hold each state for 2 s before the next action.</span>
        </div>
      </div>
    </div>
  );
}

import { useEffect } from 'react';
import { useUi } from '../../state/ui';
import { Pane } from './Pane';

/** Centre-stage caption that fires when the session-9 outcome lands (spec 3.3). */
function OverlayCaption() {
  const { ui, dispatch } = useUi();
  const o = ui.overlayText;
  useEffect(() => {
    if (!o) return;
    const t = window.setTimeout(() => dispatch({ type: 'overlay', text: null }), 4200);
    return () => window.clearTimeout(t);
  }, [o, dispatch]);
  if (!o) return null;
  const [a, b] = ['Same model. Same memory. Same email.', 'Only difference: chain of custody.'];
  return (
    <div key={o.nonce} className="pointer-events-none absolute inset-x-0 bottom-[26%] z-30 flex justify-center px-6" aria-live="assertive">
      <div className="animate-pop-in rounded-2xl border border-border bg-bg/92 px-9 py-5 text-center shadow-pop backdrop-blur-sm">
        <div className="text-3xl font-bold tracking-tight text-ink 2xl:text-4xl">{a}</div>
        <div className="mt-1 text-3xl font-extrabold tracking-tight text-ok 2xl:text-4xl">{b}</div>
      </div>
    </div>
  );
}

export function ArenaView() {
  return (
    <div className="relative h-full">
      <div className="grid h-full grid-cols-2 gap-3 p-3 2xl:gap-4 2xl:p-4">
        <Pane side="undefended" />
        <Pane side="custos" />
      </div>
      <OverlayCaption />
    </div>
  );
}

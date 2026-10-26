// Global keyboard shortcuts. Keys are chosen for one-handed use while narrating.
//   space play/pause · → / B next beat · ← previous beat · Shift+→ one event · N next session · P previous session
//   1 2 4 speed · R roll back · F fit graph · G gate card · O replay the overlay text · 0 reset · [ ] switch view · ? help
import { useEffect } from 'react';
import { OVERLAY_TEXT, useReplay } from './app';
import { VIEWS, useRoute } from './route';
import { useUi } from './ui';
import { useActions } from './actions';

export const SHORTCUTS: Array<{ keys: string[]; what: string }> = [
  { keys: ['Space'], what: 'Play / pause' },
  { keys: ['N'], what: 'Next session (and play it)' },
  { keys: ['B', '→'], what: 'Jump to the next narration beat' },
  { keys: ['←'], what: 'Previous beat' },
  { keys: ['Shift', '→'], what: 'Step one event' },
  { keys: ['P'], what: 'Restart / previous session' },
  { keys: ['1', '2', '4'], what: 'Playback speed 1× / 2× / 4×' },
  { keys: ['R'], what: 'Mark poison and roll back' },
  { keys: ['G'], what: 'Open the gate decision card' },
  { keys: ['F'], what: 'Fit the custody graph' },
  { keys: ['O'], what: 'Replay the "same model, same memory…" overlay' },
  { keys: ['0'], what: 'Reset the replay' },
  { keys: ['[', ']'], what: 'Previous / next view' },
  { keys: ['?'], what: 'This help' },
  { keys: ['Esc'], what: 'Close a card or overlay' },
];

export function useShortcuts(): void {
  const { dispatch: ed, replay } = useReplay();
  const { ui, dispatch: ud } = useUi();
  const { route, go } = useRoute();
  const actions = useActions();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
      const onControl = !!t && (t.tagName === 'BUTTON' || t.tagName === 'A' || t.getAttribute('role') === 'switch');
      const k = e.key;
      switch (k) {
        case ' ':
          if (onControl) return; // let a focused button handle its own Space
          e.preventDefault();
          ed({ type: 'toggle' });
          return;
        case 'ArrowRight':
          e.preventDefault();
          ed(e.shiftKey ? { type: 'step' } : { type: 'nextBeat' });
          return;
        case 'ArrowLeft':
          e.preventDefault();
          ed(e.shiftKey ? { type: 'stepBack' } : { type: 'prevBeat' });
          return;
        case 'n': case 'N': ed({ type: 'nextSession' }); return;
        case 'p': case 'P': ed({ type: 'prevSession' }); return;
        case 'b': case 'B': ed({ type: 'nextBeat' }); return;
        case '1': ed({ type: 'speed', speed: 1 }); return;
        case '2': ed({ type: 'speed', speed: 2 }); return;
        case '4': ed({ type: 'speed', speed: 4 }); return;
        case '0': case 'Home': ed({ type: 'reset' }); return;
        case 'r': case 'R': {
          const root = actions.defaultRollbackRoot();
          if (root) { if (route.view !== 'inspector') go('inspector'); void actions.startRollback(root, ui.rollback?.mode ?? 'revert'); }
          return;
        }
        case 'g': case 'G':
          if (replay.gates.length) actions.openGate();
          return;
        case 'f': case 'F': ud({ type: 'fit' }); return;
        case 'o': case 'O': ud({ type: 'overlay', text: OVERLAY_TEXT }); return;
        case '?': ud({ type: 'help', open: !ui.helpOpen }); return;
        case '[': case ']': {
          const i = VIEWS.indexOf(route.view);
          const next = VIEWS[(i + (k === ']' ? 1 : VIEWS.length - 1)) % VIEWS.length];
          go(next);
          return;
        }
        case 'Escape':
          if (ui.helpOpen) ud({ type: 'help', open: false });
          else if (ui.rollback) actions.closeRollback();
          else if (ui.gateOpen) ud({ type: 'gateOpen', open: false });
          return;
        default:
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [ed, ud, go, route.view, ui.helpOpen, ui.rollback, ui.gateOpen, ui.rollback?.mode, replay.gates.length, actions]);
}

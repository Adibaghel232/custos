// Interaction state that is not part of the replay: selection, graph focus, dock tab, modals, toasts, theme.
import { createContext, useCallback, useContext, useEffect, useMemo, useReducer } from 'react';
import type { ReactNode } from 'react';
import type { RollbackResult } from '../types';
import type { RollbackExtras, Source } from '../api';

export type DockTab = 'gate' | 'quarantine' | 'ledger';
export type Theme = 'dark' | 'light';
export type ToastKind = 'info' | 'ok' | 'error';
export interface Toast { id: number; kind: ToastKind; text: string }

export interface RollbackUi {
  root: string;
  mode: 'revert' | 'erase';
  phase: 'loading' | 'animating' | 'done' | 'error';
  result?: RollbackResult;
  extras?: RollbackExtras;
  source?: Source;
  /** closure hops revealed so far (0 = none yet) */
  hop: number;
  rederiveShown: boolean;
  exposureShown: boolean;
  error?: string;
}

export interface UiState {
  selectedId: string | null;
  focusId: string | null;
  hops: number;
  showReads: boolean;
  dockTab: DockTab;
  gateOpen: boolean;
  /** newest gate event the user has already seen / dismissed */
  gateSeenSeq: number;
  helpOpen: boolean;
  rollback: RollbackUi | null;
  toasts: Toast[];
  expanded: Record<string, boolean>;
  fitNonce: number;
  /** bumped after live actions so snapshot queries (graph, ledger, quarantine) refetch */
  storeVersion: number;
  theme: Theme;
  motionReduced: boolean;
  /** shown for ~3.5 s when the outcome of session 9 lands */
  overlayText: { text: string; nonce: number } | null;
}

export type UiAction =
  | { type: 'select'; id: string | null }
  | { type: 'focus'; id: string | null }
  | { type: 'hops'; hops: number }
  | { type: 'reads'; on: boolean }
  | { type: 'dock'; tab: DockTab }
  | { type: 'gateOpen'; open: boolean }
  | { type: 'gateSeen'; seq: number }
  | { type: 'help'; open: boolean }
  | { type: 'rollback'; value: RollbackUi | null }
  | { type: 'rollbackPatch'; patch: Partial<RollbackUi> }
  | { type: 'toast'; toast: Toast }
  | { type: 'dismissToast'; id: number }
  | { type: 'expand'; key: string; value: boolean }
  | { type: 'fit' }
  | { type: 'store' }
  | { type: 'theme'; theme: Theme }
  | { type: 'motion'; reduced: boolean }
  | { type: 'overlay'; text: string | null };

function readLs(key: string): string | null {
  try { return window.localStorage.getItem(key); } catch { return null; }
}
function writeLs(key: string, value: string): void {
  try { window.localStorage.setItem(key, value); } catch { /* storage can be blocked */ }
}

function initialTheme(): Theme {
  const saved = readLs('custos.theme');
  if (saved === 'light' || saved === 'dark') return saved;
  return 'dark'; // dark by default (screen recordings)
}

function initialMotionReduced(): boolean {
  const forced = readLs('custos.motion');
  if (forced === 'on') return false;
  if (forced === 'off') return true;
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function init(): UiState {
  return {
    selectedId: null, focusId: null, hops: 2, showReads: false, dockTab: 'gate', gateOpen: false, gateSeenSeq: 0,
    helpOpen: false, rollback: null, toasts: [], expanded: {}, fitNonce: 0, storeVersion: 0,
    theme: initialTheme(), motionReduced: initialMotionReduced(), overlayText: null,
  };
}

let overlayNonce = 0;

function reducer(s: UiState, a: UiAction): UiState {
  switch (a.type) {
    case 'select': return { ...s, selectedId: a.id };
    case 'focus': return { ...s, focusId: a.id, fitNonce: s.fitNonce + 1 };
    case 'hops': return { ...s, hops: a.hops, fitNonce: s.fitNonce + 1 };
    case 'reads': return { ...s, showReads: a.on, fitNonce: s.fitNonce + 1 };
    case 'dock': return { ...s, dockTab: a.tab };
    case 'gateOpen': return { ...s, gateOpen: a.open };
    case 'gateSeen': return { ...s, gateSeenSeq: a.seq };
    case 'help': return { ...s, helpOpen: a.open };
    case 'rollback': return { ...s, rollback: a.value };
    case 'rollbackPatch': return s.rollback ? { ...s, rollback: { ...s.rollback, ...a.patch } } : s;
    case 'toast': return { ...s, toasts: [...s.toasts.slice(-3), a.toast] };
    case 'dismissToast': return { ...s, toasts: s.toasts.filter((t) => t.id !== a.id) };
    case 'expand': return { ...s, expanded: { ...s.expanded, [a.key]: a.value } };
    case 'fit': return { ...s, fitNonce: s.fitNonce + 1 };
    case 'store': return { ...s, storeVersion: s.storeVersion + 1 };
    case 'theme': return { ...s, theme: a.theme };
    case 'motion': return { ...s, motionReduced: a.reduced };
    case 'overlay': return { ...s, overlayText: a.text ? { text: a.text, nonce: ++overlayNonce } : null };
  }
}

interface UiApi {
  ui: UiState;
  dispatch: React.Dispatch<UiAction>;
  toast: (kind: ToastKind, text: string) => void;
  setTheme: (t: Theme) => void;
  setMotionReduced: (r: boolean) => void;
}

const Ctx = createContext<UiApi | null>(null);
let toastId = 0;

export function UiProvider({ children }: { children: ReactNode }) {
  const [ui, dispatch] = useReducer(reducer, undefined, init);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', ui.theme);
    writeLs('custos.theme', ui.theme);
  }, [ui.theme]);
  useEffect(() => {
    document.documentElement.setAttribute('data-motion', ui.motionReduced ? 'reduced' : 'full');
  }, [ui.motionReduced]);

  const toast = useCallback((kind: ToastKind, text: string) => {
    const id = ++toastId;
    dispatch({ type: 'toast', toast: { id, kind, text } });
    window.setTimeout(() => dispatch({ type: 'dismissToast', id }), kind === 'error' ? 9000 : 5000);
  }, []);
  const setTheme = useCallback((theme: Theme) => dispatch({ type: 'theme', theme }), []);
  const setMotionReduced = useCallback((reduced: boolean) => {
    writeLs('custos.motion', reduced ? 'off' : 'on');
    dispatch({ type: 'motion', reduced });
  }, []);

  const value = useMemo<UiApi>(() => ({ ui, dispatch, toast, setTheme, setMotionReduced }), [ui, toast, setTheme, setMotionReduced]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useUi(): UiApi {
  const v = useContext(Ctx);
  if (!v) throw new Error('useUi outside UiProvider');
  return v;
}

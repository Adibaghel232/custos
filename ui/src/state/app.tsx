// App-level providers: data source + trace loading, and the replay engine.
import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import type { Dispatch, ReactNode } from 'react';
import type { Trace } from '../types';
import {
  backendStatus, getTrace, listTraces, onBackendStatus, probeBackend, refreshBackend, setFixtureAct,
} from '../api';
import type { LiveStatus, Source, TraceSummary } from '../api';
import { computeReplayState } from './replay';
import type { ReplayState } from './replay';
import { engineReducer, initialEngine, maxCursor, nextDelay } from './engine';
import type { EngineAction, EngineState, Speed } from './engine';
import { useRoute } from './route';
import { useUi } from './ui';

export const OVERLAY_TEXT = 'Same model. Same memory. Same email. Only difference: chain of custody.';

interface DataApi {
  backend: LiveStatus;
  traceSource: Source | null;
  traces: TraceSummary[];
  scenario: string;
  act: 1 | 2;
  setAct: (a: 1 | 2) => void;
  trace: Trace | null;
  loading: boolean;
  error: string | null;
  retryBackend: () => Promise<void>;
  /** true when any part of what is on screen comes from static fixtures */
  fixtureMode: boolean;
  markFixture: (source: Source) => void;
}

interface ReplayApi {
  engine: EngineState;
  replay: ReplayState;
  dispatch: Dispatch<EngineAction>;
  sessions: { n: number; label: string }[];
  /** the recorded rollback is next and is waiting for the reviewer */
  awaitingRollback: boolean;
  atEnd: boolean;
  speed: Speed;
}

const DataCtx = createContext<DataApi | null>(null);
const ReplayCtx = createContext<ReplayApi | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const { route, go } = useRoute();
  const { dispatch: uiDispatch } = useUi();

  const [backend, setBackendState] = useState<LiveStatus>(backendStatus());
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [traceSource, setTraceSource] = useState<Source | null>(null);
  const [anyFixture, setAnyFixture] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const actParam = route.params.get('act');
  const act: 1 | 2 = actParam === '2' ? 2 : 1;
  const pinned = route.params.get('trace');

  useEffect(() => onBackendStatus(setBackendState), []);

  // 1. list traces
  useEffect(() => {
    let dead = false;
    (async () => {
      setLoading(true);
      try {
        await probeBackend();
        const res = await listTraces();
        if (dead) return;
        setTraces(res.data);
        if (res.source === 'fixture') setAnyFixture(true);
      } catch (e) {
        if (!dead) setError((e as Error).message);
      }
    })();
    return () => { dead = true; };
  }, [reloadKey]);

  // 2. pick + load the trace for (scenario, act)
  const chosen = useMemo<TraceSummary | null>(() => {
    if (!traces.length) return null;
    if (pinned) { const hit = traces.find((t) => t.trace_id === pinned); if (hit) return hit; }
    const matching = traces.filter((t) => t.act === act);
    const pool = matching.length ? matching : traces;
    return [...pool].sort((a, b) => b.recorded_at.localeCompare(a.recorded_at))[0];
  }, [traces, act, pinned]);

  const [engine, dispatch] = useReducer(engineReducer, initialEngine);

  useEffect(() => {
    if (!chosen) return;
    let dead = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        setFixtureAct(chosen.act);
        const res = await getTrace(chosen);
        if (dead) return;
        setTrace(res.data);
        setTraceSource(res.source);
        if (res.source === 'fixture') setAnyFixture(true);
        dispatch({ type: 'load', events: res.data.events, live: res.data.live });
        uiDispatch({ type: 'select', id: null });
        uiDispatch({ type: 'focus', id: null });
        uiDispatch({ type: 'rollback', value: null });
        uiDispatch({ type: 'gateOpen', open: false });
        uiDispatch({ type: 'gateSeen', seq: 0 });
        uiDispatch({ type: 'store' });
      } catch (e) {
        if (!dead) setError(`Could not load a trace: ${(e as Error).message}`);
      } finally {
        if (!dead) setLoading(false);
      }
    })();
    return () => { dead = true; };
  }, [chosen, reloadKey, uiDispatch]);

  const setAct = useCallback((a: 1 | 2) => go(route.view, { act: String(a), trace: null }), [go, route.view]);

  const retryBackend = useCallback(async () => {
    await refreshBackend();
    setAnyFixture(false);
    setReloadKey((k) => k + 1);
  }, []);

  const markFixture = useCallback((source: Source) => { if (source === 'fixture') setAnyFixture(true); }, []);

  // ---- engine clock -------------------------------------------------------------------------------
  useEffect(() => {
    if (!engine.playing) return;
    const delay = nextDelay(engine);
    if (delay === null) { dispatch({ type: 'pause' }); return; }
    const h = window.setTimeout(() => dispatch({ type: 'step' }), delay);
    return () => window.clearTimeout(h);
  }, [engine]);

  const sessions = useMemo(() => trace?.sessions ?? [], [trace]);
  const labels = useMemo(() => new Map(sessions.map((s) => [s.n, s.label])), [sessions]);
  const replay = useMemo(() => computeReplayState(engine.events, engine.cursor, engine.actions, labels), [engine.events, engine.cursor, engine.actions, labels]);

  // overlay text fires when the Custos outcome of a session lands during playback
  const lastOutcome = useRef<number | null>(null);
  const outcomeSeq = replay.sides.custos.outcome?.seq ?? null;
  const ev = engine.events[engine.cursor];
  useEffect(() => {
    if (outcomeSeq === null) { lastOutcome.current = null; return; }
    if (lastOutcome.current !== outcomeSeq) {
      lastOutcome.current = outcomeSeq;
      if (ev && ev.seq === outcomeSeq) uiDispatch({ type: 'overlay', text: OVERLAY_TEXT });
    }
  }, [outcomeSeq, ev, uiDispatch]);

  const awaitingRollback = engine.holdIdx >= 0 && !engine.released && engine.cursor === engine.holdIdx - 1;
  const atEnd = engine.cursor >= maxCursor(engine);

  const data = useMemo<DataApi>(() => ({
    backend, traceSource, traces, scenario: trace?.scenario ?? 'vendor_fraud', act, setAct, trace, loading, error, retryBackend,
    fixtureMode: anyFixture || backend === 'down', markFixture,
  }), [backend, traceSource, traces, trace, act, setAct, loading, error, retryBackend, anyFixture, markFixture]);

  const rep = useMemo<ReplayApi>(() => ({
    engine, replay, dispatch, sessions, awaitingRollback, atEnd, speed: engine.speed,
  }), [engine, replay, sessions, awaitingRollback, atEnd]);

  return (
    <DataCtx.Provider value={data}>
      <ReplayCtx.Provider value={rep}>{children}</ReplayCtx.Provider>
    </DataCtx.Provider>
  );
}

export function useData(): DataApi {
  const v = useContext(DataCtx);
  if (!v) throw new Error('useData outside AppProvider');
  return v;
}

export function useReplay(): ReplayApi {
  const v = useContext(ReplayCtx);
  if (!v) throw new Error('useReplay outside AppProvider');
  return v;
}

export const SPEEDS: Speed[] = [1, 2, 4];

// Data layer. Talks to the FastAPI gateway under /api (proxied by Vite to http://127.0.0.1:8080) and falls
// back to the static fixtures in /fixtures when the backend is unreachable.
//
//  * Reads (traces, graph, records, ledger, quarantine, bench): API first, fixture on any failure.
//  * Live actions (rollback, ledger verify, tamper demo, quarantine actions):
//      - backend up   -> the API call is the only truth. A failure is surfaced to the caller (no silent fixture).
//      - backend down -> the fixture result is played (and the result is tagged source: 'fixture').
//  * Every result carries `source: 'api' | 'fixture'` so the UI can badge sample data honestly.
import type {
  LedgerEntry, GraphEdge, GraphNode, MemoryRecord, RollbackResult, Trace,
} from './types';
import { neighbourhood } from './lib/graph';
import { makeEntry, tamperDemo, verifyChain } from './lib/ledger';
import type { TamperResult, VerifyResult } from './lib/ledger';
import { synthesizeRollback } from './lib/rollbackSynth';
import type { SynthContext } from './lib/rollbackSynth';
import type { BenchSummary, F1Row, F2Row, F3Row, F4Row, F5Row, F6Row, FigureName } from './benchTypes';
import { normF1, normF2, normF3, normF4, normF5, normF6 } from './benchTypes';

export type Source = 'api' | 'fixture';
export interface Loaded<T> { data: T; source: Source }

export interface TraceSummary {
  trace_id: string;
  scenario: string;
  act: 1 | 2;
  recorded_at: string;
  live: boolean;
  backend: string;
  events?: number;
}
export interface GraphQuery { tenant?: string; focus?: string; hops?: number }
export interface GraphResponse { nodes: GraphNode[]; edges: GraphEdge[] }
export type QuarantineAction = 'confirm_t3' | 'reject' | 'poison';
export interface QuarantineResult { records: MemoryRecord[]; ledger: LedgerEntry[] }
export interface RollbackExtras { records: MemoryRecord[]; ledger: LedgerEntry[] }
export type TamperResponse = VerifyResult & { tampered_seq?: number; copy?: LedgerEntry[] };
export type LiveStatus = 'unknown' | 'up' | 'down';

export class ApiError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

// ---------------------------------------------------------------------------------------------------
// transport

const API_TIMEOUT_MS = 6000;
let backend: LiveStatus = 'unknown';
let probePromise: Promise<boolean> | null = null;
let fixtureAct: 1 | 2 = 1;
const listeners = new Set<(s: LiveStatus) => void>();

export const backendStatus = (): LiveStatus => backend;
export function onBackendStatus(fn: (s: LiveStatus) => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}
function setBackend(s: LiveStatus): void {
  if (backend === s) return;
  backend = s;
  listeners.forEach((fn) => fn(s));
}

/** Which act's fixtures answer graph / ledger / record lookups while in fixture mode. */
export function setFixtureAct(act: 1 | 2): void { fixtureAct = act; }

async function getJson<T>(url: string, init?: RequestInit, timeoutMs = API_TIMEOUT_MS): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...init, signal: ctrl.signal, headers: { Accept: 'application/json', ...(init?.headers ?? {}) } });
    if (!res.ok) throw new ApiError(`${init?.method ?? 'GET'} ${url} -> HTTP ${res.status}`, res.status);
    const ct = res.headers.get('content-type') ?? '';
    if (!ct.includes('json')) throw new ApiError(`${url} did not return JSON (got ${ct || 'no content-type'})`);
    return (await res.json()) as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError(`${url}: ${(e as Error).message || 'network error'}`);
  } finally {
    clearTimeout(timer);
  }
}

function postJson<T>(url: string, body?: unknown, timeoutMs = API_TIMEOUT_MS): Promise<T> {
  return getJson<T>(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) }, timeoutMs);
}

const fixtureCache = new Map<string, Promise<unknown>>();
function fixture<T>(name: string): Promise<T> {
  let p = fixtureCache.get(name);
  if (!p) {
    p = getJson<T>(`${import.meta.env.BASE_URL}fixtures/${name}`, undefined, 8000);
    fixtureCache.set(name, p);
    p.catch(() => fixtureCache.delete(name)); // do not cache failures
  }
  return p as Promise<T>;
}

/** One cheap probe decides API-vs-fixture; call refreshBackend() to try again (e.g. backend started late). */
export function probeBackend(force = false): Promise<boolean> {
  if (!force && backend !== 'unknown') return Promise.resolve(backend === 'up');
  if (!force && probePromise) return probePromise;
  probePromise = (async () => {
    try {
      const raw = await getJson<unknown>('/api/traces', undefined, 2500);
      const good = Array.isArray(raw) || (!!raw && typeof raw === 'object');
      setBackend(good ? 'up' : 'down');
      return good;
    } catch {
      setBackend('down');
      return false;
    } finally {
      probePromise = null;
    }
  })();
  return probePromise;
}
export const refreshBackend = (): Promise<boolean> => probeBackend(true);

/** Run `live` when the backend is up (failures fall back for reads), else `fix`. */
async function readThrough<T>(live: () => Promise<T>, fix: () => Promise<T>): Promise<Loaded<T>> {
  if (await probeBackend()) {
    try {
      return { data: await live(), source: 'api' };
    } catch {
      // A single failing read must not poison the whole session; fall back for this call only.
    }
  }
  return { data: await fix(), source: 'fixture' };
}

// ---------------------------------------------------------------------------------------------------
// traces

function normaliseTraceList(raw: unknown): TraceSummary[] {
  const arr = Array.isArray(raw) ? raw : (raw && typeof raw === 'object' && Array.isArray((raw as { traces?: unknown }).traces) ? (raw as { traces: unknown[] }).traces : []);
  return (arr as Array<Partial<TraceSummary> & { events?: unknown }>)
    .filter((t) => typeof t.trace_id === 'string')
    .map((t) => ({
      trace_id: t.trace_id as string,
      scenario: t.scenario ?? 'vendor_fraud',
      act: t.act === 2 ? 2 : 1,
      recorded_at: t.recorded_at ?? '',
      live: !!t.live,
      backend: t.backend ?? '',
      events: Array.isArray(t.events) ? t.events.length : (typeof t.events === 'number' ? t.events : undefined),
    }));
}

export function listTraces(): Promise<Loaded<TraceSummary[]>> {
  return readThrough(
    async () => {
      const list = normaliseTraceList(await getJson<unknown>('/api/traces'));
      if (!list.length) throw new ApiError('backend has no traces');
      return list;
    },
    async () => normaliseTraceList(await fixture<unknown>('traces.json')),
  );
}

export function getTrace(summary: Pick<TraceSummary, 'trace_id' | 'act'>): Promise<Loaded<Trace>> {
  return readThrough(
    () => getJson<Trace>(`/api/traces/${encodeURIComponent(summary.trace_id)}`),
    () => fixture<Trace>(`trace_act${summary.act}.json`),
  );
}

// ---------------------------------------------------------------------------------------------------
// store snapshots

export function getGraph(q: GraphQuery): Promise<Loaded<GraphResponse>> {
  const params = new URLSearchParams();
  if (q.tenant) params.set('tenant', q.tenant);
  if (q.focus) params.set('focus', q.focus);
  if (q.hops !== undefined) params.set('hops', String(q.hops));
  const qs = params.toString();
  return readThrough(
    () => getJson<GraphResponse>(`/api/graph${qs ? `?${qs}` : ''}`),
    async () => {
      const full = await fixture<GraphResponse>(`graph_act${fixtureAct}.json`);
      if (!q.focus) return full;
      return neighbourhood(full.nodes, full.edges, q.focus, q.hops ?? 2, { includeReads: true });
    },
  );
}

export function getRecord(id: string): Promise<Loaded<MemoryRecord>> {
  return readThrough(
    () => getJson<MemoryRecord>(`/api/records/${encodeURIComponent(id)}`),
    async () => {
      const all = await fixture<Record<string, MemoryRecord>>(`records_act${fixtureAct}.json`);
      const r = all[id];
      if (!r) throw new ApiError(`no fixture record ${id}`, 404);
      return r;
    },
  );
}

export function getQuarantine(): Promise<Loaded<MemoryRecord[]>> {
  return readThrough(
    () => getJson<MemoryRecord[]>('/api/quarantine'),
    async () => (fixtureAct === 1 ? fixture<MemoryRecord[]>('quarantine.json') : []),
  );
}

export function getLedger(opts: { from?: number; limit?: number } = {}): Promise<Loaded<LedgerEntry[]>> {
  const params = new URLSearchParams();
  if (opts.from !== undefined) params.set('from', String(opts.from));
  if (opts.limit !== undefined) params.set('limit', String(opts.limit));
  const qs = params.toString();
  return readThrough(
    () => getJson<LedgerEntry[]>(`/api/ledger${qs ? `?${qs}` : ''}`),
    async () => {
      const all = await fixture<LedgerEntry[]>(fixtureAct === 1 ? 'ledger_act1.json' : 'ledger.json');
      const from = opts.from ?? 0;
      const sliced = all.filter((e) => e.seq >= from);
      return opts.limit ? sliced.slice(0, opts.limit) : sliced;
    },
  );
}

// ---------------------------------------------------------------------------------------------------
// live actions

/**
 * POST /api/quarantine/{id}  body {action}. Backend returns the changed MemoryRecord(s).
 * Fixture mode computes the same transition locally and extends the (visible) ledger chain.
 */
export async function postQuarantine(
  rec: MemoryRecord,
  action: QuarantineAction,
  ctx: { tip: LedgerEntry | undefined; now: string; conflict?: MemoryRecord },
): Promise<Loaded<QuarantineResult>> {
  if (await probeBackend()) {
    const body = await postJson<MemoryRecord | MemoryRecord[]>(`/api/quarantine/${encodeURIComponent(rec.id)}`, { action });
    return { data: { records: Array.isArray(body) ? body : [body], ledger: [] }, source: 'api' };
  }
  const reviewer = 'reviewer:ap-lead-01';
  const hist = (r: MemoryRecord, status: MemoryRecord['status']): MemoryRecord['status_history'] => [...(r.status_history ?? []), { ts: ctx.now, status, by: reviewer }];
  const ledger: LedgerEntry[] = [];
  const append = (event: LedgerEntry['event'], recordId: string, payload: unknown): void => {
    const prev = ledger[ledger.length - 1] ?? ctx.tip;
    ledger.push(makeEntry(prev, { ts: ctx.now, event, record_id: recordId, actor: reviewer, payload }));
  };
  const records: MemoryRecord[] = [];
  if (action === 'confirm_t3') {
    // Simulated ERP vendor update: the claim is promoted to T3 and the old fact is closed in time, not deleted.
    records.push({
      ...rec, status: 'committed', tier: 'T3', conflicts_with: undefined,
      decision: { verdict: 'commit', rule: 'quarantine.confirm_t3', explanation: 'Confirmed via T3: the vendor was updated in the ERP (simulated call-back). Promoted to T3; the previous fact is closed in time, not deleted.' },
      status_history: hist(rec, 'committed'),
    });
    if (ctx.conflict) records.push({ ...ctx.conflict, status: 'superseded', valid_to: ctx.now, status_history: hist(ctx.conflict, 'superseded') });
    append('PROMOTE', rec.id, { action, promoted: rec.id, closed: ctx.conflict?.id });
  } else if (action === 'reject') {
    records.push({ ...rec, status: 'rejected', status_history: hist(rec, 'rejected') });
    append('DECIDE', rec.id, { action, verdict: 'reject' });
  } else {
    // poison: the reviewer's verdict; the rollback itself is a separate call (POST /api/rollback).
    records.push({ ...rec, status_history: hist(rec, rec.status) });
    append('DECIDE', rec.id, { action, verdict: 'poison' });
  }
  return { data: { records, ledger }, source: 'fixture' };
}

export type VerifyOutcome = Loaded<VerifyResult>;

export async function postLedgerVerify(visible: LedgerEntry[]): Promise<VerifyOutcome> {
  if (await probeBackend()) {
    return { data: await postJson<VerifyResult>('/api/ledger/verify'), source: 'api' };
  }
  return { data: verifyChain(visible), source: 'fixture' };
}

export async function postTamperDemo(visible: LedgerEntry[]): Promise<Loaded<TamperResponse>> {
  if (await probeBackend()) {
    return { data: await postJson<TamperResponse>('/api/ledger/tamper-demo'), source: 'api' };
  }
  const r: TamperResult = tamperDemo(visible);
  return { data: r, source: 'fixture' };
}

export async function postRollback(
  req: { root: string; mode: 'revert' | 'erase' },
  synth: SynthContext,
): Promise<Loaded<RollbackResult>> {
  if (await probeBackend()) {
    return { data: await postJson<RollbackResult>('/api/rollback', req, 30000), source: 'api' };
  }
  const authored = await fixture<Record<string, { revert: { result: RollbackResult }; erase: { result: RollbackResult } }>>('rollback.json');
  const hit = authored[req.root]?.[req.mode]?.result;
  return { data: hit ?? synthesizeRollback(req.root, req.mode, synth), source: 'fixture' };
}

/** Records and ledger entries that a rollback created (the re-derived records). Fixture: authored. Live: fetched. */
export async function getRollbackExtras(result: RollbackResult, source: Source): Promise<RollbackExtras> {
  if (source === 'fixture') {
    const authored = await fixture<Record<string, { revert: RollbackExtras; erase: RollbackExtras }>>('rollback.json');
    const hit = authored[result.root]?.[result.mode];
    return hit ? { records: hit.records, ledger: hit.ledger } : { records: [], ledger: [] };
  }
  const records: MemoryRecord[] = [];
  for (const { new: id } of result.rederived) {
    try { records.push((await getJson<MemoryRecord>(`/api/records/${encodeURIComponent(id)}`))); } catch { /* shown as "not loaded" */ }
  }
  let ledger: LedgerEntry[] = [];
  try { ledger = await getJson<LedgerEntry[]>(`/api/ledger?from=${Math.max(1, result.ledger_seq)}&limit=8`); } catch { /* table refreshes later */ }
  return { records, ledger };
}

// ---------------------------------------------------------------------------------------------------
// bench

export function getBenchSummary(): Promise<Loaded<BenchSummary>> {
  return readThrough(
    () => getJson<BenchSummary>('/api/bench/summary'),
    () => fixture<BenchSummary>('bench_summary.json'),
  );
}

export interface FigureData {
  F1: F1Row[]; F2: F2Row[]; F3: F3Row[]; F4: F4Row[]; F5: F5Row[]; F6: F6Row[];
}

const NORMALISE: { [K in FigureName]: (raw: unknown) => FigureData[K] } = {
  F1: normF1, F2: normF2, F3: normF3, F4: normF4, F5: normF5, F6: normF6,
};

export async function getBenchFigure<K extends FigureName>(name: K): Promise<Loaded<FigureData[K]>> {
  const loaded = await readThrough<unknown>(
    () => getJson<unknown>(`/api/bench/figure/${name}`),
    () => fixture<unknown>(`bench_${name}.json`),
  );
  return { data: NORMALISE[name](loaded.data) as FigureData[K], source: loaded.source };
}

// ---------------------------------------------------------------------------------------------------
// live run (SSE). Optional: only used when the backend is up and the user starts a live session.

export async function startLiveSession(scenario: string, n: number): Promise<void> {
  await postJson<unknown>(`/api/live/${encodeURIComponent(scenario)}/session/${n}`);
}

/** Subscribes to GET /api/live/stream. Each message is a JSON TraceEvent. Returns an unsubscribe function. */
export function openLiveStream(onEvent: (ev: import('./types').TraceEvent) => void, onError: (e: Event) => void): () => void {
  const es = new EventSource('/api/live/stream');
  es.onmessage = (m) => {
    try { onEvent(JSON.parse(m.data) as import('./types').TraceEvent); } catch { /* ignore keep-alives */ }
  };
  es.onerror = onError;
  return () => es.close();
}

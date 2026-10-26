// ActBuilder: assembles one trace (Act 1 or Act 2) of the vendor-fraud scenario from a small DSL.
// Both sides ("undefended" and "custos") run on parallel lanes; events are sorted by time at the end.
// Everything that depends on a hash (ledger chain, raw_hash) is computed, never typed by hand.
import type {
  CheckResult, Claim, Decision, GateDecision, GraphEdge, GraphNode, LedgerEntry, MemoryRecord, RecordStatus, Side, Tier, Trace, TraceEvent,
} from '../../src/types.ts';
import type { PayloadMap } from '../../src/tracePayloads.ts';
import { makeEntry } from '../../src/lib/ledger.ts';
import { sha256Hex } from '../../src/lib/sha256.ts';
import { istIso, RUN_START_MS } from './util.ts';

export const TENANT = 'northstar-demo';
export const USER = 'finance-clerk-02';
export const MODEL = 'nvidia/nemotron-3.5-lightning-30b-a3b';

type EvType = TraceEvent['type'];

interface PendingEvent { t_ms: number; order: number; side: Side; session: number; type: EvType; payload: unknown }

export interface SourceInfo { id: string; label: string; tier: Tier; session: number; kindIcon: 'email' | 'erp' | 'user' }

export interface ReadLog { session: number; read_id: string; principal: string; returned: string[]; query: string }

export class Lane {
  lt = 0;
  readonly side: Side;
  private readonly act: ActBuilder;
  constructor(act: ActBuilder, side: Side) { this.act = act; this.side = side; }

  /** Emit an event `dt` ms after this lane's previous one. Returns the event's absolute t_ms. */
  emit<K extends EvType>(type: K, payload: K extends keyof PayloadMap ? PayloadMap[K] : unknown, dt = 400): number {
    this.lt += dt;
    return this.act.push(this.side, type, payload, this.lt);
  }
  wait(dt: number): void { this.lt += dt; }
  /** Absolute wall-clock ISO of "now" on this lane (+ optional look-ahead). */
  ts(ahead = 0): string { return istIso(RUN_START_MS + this.act.sessionBase + this.lt + ahead); }
  absMs(ahead = 0): number { return this.act.sessionBase + this.lt + ahead; }
}

export class ActBuilder {
  readonly act: 1 | 2;
  private pending: PendingEvent[] = [];
  private order = 0;
  sessionBase = 0;
  session = 0;
  readonly lanes: Record<Side, Lane>;
  readonly ledger: LedgerEntry[] = [];
  readonly records = new Map<string, MemoryRecord>();       // custos side, latest state
  readonly urecords = new Map<string, MemoryRecord>();      // undefended side, latest state
  readonly background: MemoryRecord[] = [];                 // pre-existing tenant memory (not in the trace)
  readonly sources = new Map<string, SourceInfo>();
  readonly reads: ReadLog[] = [];
  readonly gates: { session: number; gate: GateDecision }[] = [];
  readonly sessions: { n: number; label: string }[] = [];
  readonly extraEdges: GraphEdge[] = [];

  constructor(act: 1 | 2) {
    this.act = act;
    this.lanes = { undefended: new Lane(this, 'undefended'), custos: new Lane(this, 'custos') };
  }

  get U(): Lane { return this.lanes.undefended; }
  get C(): Lane { return this.lanes.custos; }

  push(side: Side, type: EvType, payload: unknown, laneTime: number): number {
    const t_ms = this.sessionBase + laneTime;
    this.pending.push({ t_ms, order: this.order++, side, session: this.session, type, payload });
    return t_ms;
  }

  startSession(n: number, label: string): void {
    this.session = n;
    this.sessions.push({ n, label });
    this.U.lt = 0;
    this.C.lt = 0;
    this.U.emit('session_start', { n, label }, 0);
    this.C.emit('session_start', { n, label }, 0);
  }

  /** Close the session: next one starts after the slower lane plus a gap. */
  endSession(gapMs = 1100): void {
    this.sessionBase += Math.max(this.U.lt, this.C.lt) + gapMs;
    this.U.lt = 0;
    this.C.lt = 0;
  }

  /** Narrator pause point, emitted after both lanes have finished their work for this session. */
  beat(id: string, title: string, note?: string, pause = true): void {
    const lt = Math.max(this.U.lt, this.C.lt) + 350;
    this.push('custos', 'beat', { id, title, note, pause }, lt);
    this.U.lt = lt;
    this.C.lt = lt;
  }

  // ---- ledger ------------------------------------------------------------------------------

  /** Append to the hash chain WITHOUT emitting a trace event (used for pre-history and look-ahead seq). */
  ledgerAppend(ts: string, event: LedgerEntry['event'], record_id: string | undefined, actor: string, payload: unknown): LedgerEntry {
    const prev = this.ledger[this.ledger.length - 1];
    const e = makeEntry(prev, { ts, event, record_id, actor, payload });
    this.ledger.push(e);
    return e;
  }

  /** Append an entry timed on the custos lane and emit it as a `ledger` trace event. */
  ledgerOnLane(dt: number, event: LedgerEntry['event'], record_id: string | undefined, actor: string, payload: unknown): LedgerEntry {
    const lane = this.C;
    const e = this.ledgerAppend(lane.ts(dt), event, record_id, actor, payload);
    lane.emit('ledger', e, dt);
    return e;
  }

  // ---- finalise ----------------------------------------------------------------------------

  finalEvents(): TraceEvent[] {
    const sideRank: Record<Side, number> = { undefended: 0, custos: 1 };
    const sorted = [...this.pending].sort((a, b) => a.t_ms - b.t_ms || sideRank[a.side] - sideRank[b.side] || a.order - b.order);
    return sorted.map((p, i) => ({ seq: i + 1, t_ms: p.t_ms, side: p.side, session: p.session, type: p.type, payload: p.payload }));
  }

  trace(meta: { trace_id: string; scenario: string; seed: number }): Trace {
    return {
      trace_id: meta.trace_id,
      scenario: meta.scenario,
      act: this.act,
      recorded_at: istIso(RUN_START_MS),
      live: false,
      models: {
        agent: MODEL, extractor: MODEL, judge: MODEL, embed: 'nvidia/nemotron-3.5-embed-1b',
      },
      backend: 'mem0',
      seed: meta.seed,
      sessions: this.sessions,
      events: this.finalEvents(),
    };
  }
}

// ---- record / claim constructors -----------------------------------------------------------------

export function claim(id: string, s: string, r: string, o: string, type: Claim['type'], valid_from?: string): Claim {
  const c: Claim = { id, s, r, o, type };
  if (valid_from) c.valid_from = valid_from;
  return c;
}

export interface RecordSpec {
  id: string;
  text: string;
  tier: Tier | null;
  status: RecordStatus;
  kind: MemoryRecord['kind'];
  source: MemoryRecord['source'];
  principal: string;
  session: number;
  parents?: string[];
  raw: string;
  ledger_seq: number;
  sig_ok?: boolean;
  atIso: string;
  claims?: Claim[];
  checks?: CheckResult[];
  decision?: Decision;
  conflicts_with?: MemoryRecord['conflicts_with'];
  history?: { ts: string; status: RecordStatus; by: string }[];
  valid_from?: string;
}

export function mkRecord(s: RecordSpec): MemoryRecord {
  const rec: MemoryRecord = {
    id: s.id,
    tenant: TENANT,
    text: s.text,
    tier: s.tier,
    status: s.status,
    kind: s.kind,
    source: s.source,
    principal: s.principal,
    session: s.session,
    parents: s.parents ?? [],
    raw_hash: sha256Hex(s.raw),
    ledger_seq: s.ledger_seq,
    sig_ok: s.sig_ok ?? s.ledger_seq > 0,
    valid_from: s.valid_from ?? s.atIso,
    valid_to: null,
    recorded_at: s.atIso,
  };
  if (s.claims) rec.claims = s.claims;
  if (s.checks) rec.checks = s.checks;
  if (s.decision) rec.decision = s.decision;
  if (s.conflicts_with) rec.conflicts_with = s.conflicts_with;
  rec.status_history = s.history ?? [{ ts: s.atIso, status: s.status, by: s.tier ? 'gateway' : 'mem0' }];
  return rec;
}

export function graphLabel(r: MemoryRecord): string {
  if (r.kind === 'summary') return r.id === 'm_23' || r.id === 'u_23' ? 'summary · vendor emails wk 12' : 'summary · vendor emails wk 12 (clean)';
  const c = r.claims?.[0];
  if (r.id === 'm_25') return 'note · vendor changes this month';
  if (c) return `${c.s}.${c.r} ${c.o}`;
  return r.text.length > 34 ? r.text.slice(0, 33) + '…' : r.text;
}

export function recordToNode(r: MemoryRecord): GraphNode {
  return {
    id: r.id,
    kind: r.kind === 'summary' ? 'summary' : 'record',
    label: graphLabel(r),
    tier: r.tier,
    status: r.status,
    session: r.session,
  };
}

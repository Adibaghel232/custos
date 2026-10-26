// Pure replay reducer: (events up to a cursor) + (reviewer actions) -> everything the views draw.
// A live run and a replay are the same thing to the UI: both are a growing list of TraceEvents.
import type { Claim, GateDecision, LedgerEntry, MemoryRecord, RecordStatus, RollbackResult, Side, TraceEvent } from '../types';
import type {
  BeatPayload, CheckPayload, InboundPayload, MemoryReadPayload, MessagePayload, OutcomePayload, StampPayload, ToolCallPayload,
} from '../tracePayloads';
import { payloadOf } from '../tracePayloads';

// ---- derived structures --------------------------------------------------------------------------

export interface PipelineRow {
  key: string;
  recordId: string;
  session: number;
  side: Side;
  stamp?: StampPayload;
  claims?: Claim[];
  checks: CheckPayload[];
  decision?: {
    verdict: 'commit' | 'version' | 'quarantine' | 'reject';
    rule: string;
    explanation: string;
    status: RecordStatus;
    conflicts_with?: MemoryRecord['conflicts_with'];
  };
  written: boolean;
  lastSeq: number;
}

export interface ToolCallState {
  callId: string;
  tool: string;
  args: Record<string, string>;
  risk: 'low' | 'high';
  status: ToolCallPayload['status'];
  result?: string;
  seq: number;
  lastSeq: number;
}

export type LogItem =
  | { kind: 'inbound'; key: string; seq: number; payload: InboundPayload }
  | { kind: 'message'; key: string; seq: number; payload: MessagePayload }
  | { kind: 'pipeline'; key: string; seq: number; row: PipelineRow }
  | { kind: 'write'; key: string; seq: number; recordId: string }
  | { kind: 'read'; key: string; seq: number; payload: MemoryReadPayload }
  | { kind: 'tool'; key: string; seq: number; call: ToolCallState }
  | { kind: 'gate'; key: string; seq: number; gate: GateDecision }
  | { kind: 'outcome'; key: string; seq: number; payload: OutcomePayload };

export interface SessionLog { n: number; label: string; startSeq: number; items: LogItem[] }

export interface SideState {
  side: Side;
  sessions: SessionLog[];
  records: Record<string, MemoryRecord>;
  /** record ids in first-write order */
  order: string[];
  outcome?: OutcomePayload & { seq: number };
}

export interface ReadEntry { seq: number; session: number; read_id: string; principal: string; query: string; purpose: string; returned: string[] }
export interface GateEntry { seq: number; session: number; gate: GateDecision }
export interface BeatState { seq: number; payload: BeatPayload }

export interface ReplayState {
  cursor: number;
  lastEvent?: TraceEvent;
  currentSession: number;
  sides: Record<Side, SideState>;
  reads: ReadEntry[];
  gates: GateEntry[];
  ledger: LedgerEntry[];
  maxLedgerSeq: number;
  rollbacks: RollbackResult[];
  beat?: BeatState;
  /** every record id the whole trace ever writes (so views can tell "not yet" from "unknown") */
  futureRecordIds: Set<string>;
}

// ---- reviewer actions applied on top of the trace --------------------------------------------------

export type AppliedAction =
  | { id: string; kind: 'rollback'; at: number; result: RollbackResult; records: MemoryRecord[]; ledger: LedgerEntry[]; ts: string }
  | { id: string; kind: 'quarantine'; at: number; recordId: string; action: 'confirm_t3' | 'reject' | 'poison'; records: MemoryRecord[]; ledger: LedgerEntry[]; ts: string };

type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;
/** An action as the UI builds it; the engine stamps id and position. */
export type NewAction = DistributiveOmit<AppliedAction, 'id' | 'at'>;

// ---- reducer ------------------------------------------------------------------------------------------

function emptySide(side: Side): SideState {
  return { side, sessions: [], records: {}, order: [], outcome: undefined };
}

export function emptyReplayState(): ReplayState {
  return {
    cursor: -1,
    currentSession: 0,
    sides: { undefended: emptySide('undefended'), custos: emptySide('custos') },
    reads: [],
    gates: [],
    ledger: [],
    maxLedgerSeq: 0,
    rollbacks: [],
    futureRecordIds: new Set(),
  };
}

function sessionOf(side: SideState, n: number, label = ''): SessionLog {
  let s = side.sessions.find((x) => x.n === n);
  if (!s) {
    s = { n, label, startSeq: -1, items: [] };
    side.sessions.push(s);
    side.sessions.sort((a, b) => a.n - b.n);
  }
  return s;
}

function upsertRecord(side: SideState, rec: MemoryRecord): boolean {
  const isNew = !(rec.id in side.records);
  side.records[rec.id] = rec;
  if (isNew) side.order.push(rec.id);
  return isNew;
}

function addLedger(state: ReplayState, entry: LedgerEntry): void {
  if (state.ledger.some((e) => e.seq === entry.seq)) return;
  state.ledger.push(entry);
  state.ledger.sort((a, b) => a.seq - b.seq);
  if (entry.seq > state.maxLedgerSeq) state.maxLedgerSeq = entry.seq;
}

function applyEvent(state: ReplayState, ev: TraceEvent, sessionLabels: Map<number, string>): void {
  const side = state.sides[ev.side];
  state.lastEvent = ev;
  state.currentSession = ev.session;
  const key = `${ev.side}:${ev.seq}`;

  switch (ev.type) {
    case 'session_start': {
      const p = payloadOf(ev, 'session_start');
      const s = sessionOf(side, p.n, p.label || sessionLabels.get(p.n) || '');
      if (s.startSeq < 0) s.startSeq = ev.seq;
      if (!s.label) s.label = p.label;
      break;
    }
    case 'inbound': {
      sessionOf(side, ev.session, sessionLabels.get(ev.session)).items.push({ kind: 'inbound', key, seq: ev.seq, payload: payloadOf(ev, 'inbound') });
      break;
    }
    case 'message': {
      sessionOf(side, ev.session, sessionLabels.get(ev.session)).items.push({ kind: 'message', key, seq: ev.seq, payload: payloadOf(ev, 'message') });
      break;
    }
    case 'stamp':
    case 'claims':
    case 'check':
    case 'decision': {
      const s = sessionOf(side, ev.session, sessionLabels.get(ev.session));
      const recordId = (ev.payload as { record_id: string }).record_id;
      const rowKey = `${ev.side}:${ev.session}:${recordId}`;
      let item = s.items.find((i) => i.kind === 'pipeline' && i.row.key === rowKey) as Extract<LogItem, { kind: 'pipeline' }> | undefined;
      if (!item) {
        const row: PipelineRow = { key: rowKey, recordId, session: ev.session, side: ev.side, checks: [], written: false, lastSeq: ev.seq };
        item = { kind: 'pipeline', key: rowKey, seq: ev.seq, row };
        s.items.push(item);
      }
      const row = item.row;
      row.lastSeq = ev.seq;
      if (ev.type === 'stamp') row.stamp = payloadOf(ev, 'stamp');
      else if (ev.type === 'claims') row.claims = payloadOf(ev, 'claims').claims;
      else if (ev.type === 'check') row.checks.push(payloadOf(ev, 'check'));
      else {
        const d = payloadOf(ev, 'decision');
        row.decision = { verdict: d.verdict, rule: d.rule, explanation: d.explanation, status: d.status, conflicts_with: d.conflicts_with };
      }
      break;
    }
    case 'memory_write': {
      const rec = payloadOf(ev, 'memory_write');
      const isNew = upsertRecord(side, rec);
      const s = sessionOf(side, ev.session, sessionLabels.get(ev.session));
      const row = s.items.find((i) => i.kind === 'pipeline' && i.row.recordId === rec.id && i.row.session === ev.session) as Extract<LogItem, { kind: 'pipeline' }> | undefined;
      if (row) {
        row.row.written = true;
        row.row.lastSeq = ev.seq;
      } else if (isNew) {
        s.items.push({ kind: 'write', key, seq: ev.seq, recordId: rec.id });
      }
      break;
    }
    case 'memory_read': {
      const p = payloadOf(ev, 'memory_read');
      sessionOf(side, ev.session, sessionLabels.get(ev.session)).items.push({ kind: 'read', key, seq: ev.seq, payload: p });
      if (ev.side === 'custos') {
        state.reads.push({ seq: ev.seq, session: ev.session, read_id: p.read_id, principal: p.principal, query: p.query, purpose: p.purpose, returned: p.returned.map((r) => r.id) });
      }
      break;
    }
    case 'tool_call': {
      const p = payloadOf(ev, 'tool_call');
      const s = sessionOf(side, ev.session, sessionLabels.get(ev.session));
      const existing = s.items.find((i) => i.kind === 'tool' && i.call.callId === p.call_id) as Extract<LogItem, { kind: 'tool' }> | undefined;
      if (existing) {
        existing.call.status = p.status;
        existing.call.result = p.result ?? existing.call.result;
        existing.call.lastSeq = ev.seq;
      } else {
        s.items.push({ kind: 'tool', key: `${ev.side}:call:${p.call_id}`, seq: ev.seq, call: { callId: p.call_id, tool: p.tool, args: p.args, risk: p.risk, status: p.status, result: p.result, seq: ev.seq, lastSeq: ev.seq } });
      }
      break;
    }
    case 'gate': {
      const g = payloadOf(ev, 'gate');
      sessionOf(side, ev.session, sessionLabels.get(ev.session)).items.push({ kind: 'gate', key, seq: ev.seq, gate: g });
      state.gates.push({ seq: ev.seq, session: ev.session, gate: g });
      break;
    }
    case 'outcome': {
      const p = payloadOf(ev, 'outcome');
      side.outcome = { ...p, seq: ev.seq };
      sessionOf(side, ev.session, sessionLabels.get(ev.session)).items.push({ kind: 'outcome', key, seq: ev.seq, payload: p });
      break;
    }
    case 'ledger': {
      addLedger(state, payloadOf(ev, 'ledger'));
      break;
    }
    case 'revert': {
      applyRollback(state, payloadOf(ev, 'revert'), { records: [], ledger: [] }, '');
      break;
    }
    case 'beat': {
      state.beat = { seq: ev.seq, payload: payloadOf(ev, 'beat') };
      break;
    }
  }
}

function applyRollback(state: ReplayState, result: RollbackResult, extras: { records: MemoryRecord[]; ledger: LedgerEntry[] }, ts: string): void {
  const status: RecordStatus = result.mode === 'erase' ? 'erased' : 'reverted';
  const side = state.sides.custos;
  for (const id of result.closure_by_hop.flat()) {
    const r = side.records[id];
    if (!r) continue;
    side.records[id] = { ...r, status, valid_to: r.valid_to ?? ts ?? null, status_history: [...(r.status_history ?? []), { ts: ts || r.recorded_at, status, by: 'reviewer:ap-lead-01' }] };
  }
  for (const r of extras.records) upsertRecord(side, r);
  for (const e of extras.ledger) addLedger(state, e);
  if (!state.rollbacks.some((x) => x.root === result.root && x.mode === result.mode)) state.rollbacks.push(result);
}

function applyAction(state: ReplayState, a: AppliedAction): void {
  const side = state.sides.custos;
  if (a.kind === 'rollback') {
    applyRollback(state, a.result, { records: a.records, ledger: a.ledger }, a.ts);
    return;
  }
  for (const r of a.records) {
    if (r.id in side.records) side.records[r.id] = r;
    else upsertRecord(side, r);
  }
  for (const e of a.ledger) addLedger(state, e);
}

export function computeReplayState(
  events: TraceEvent[],
  cursor: number,
  actions: AppliedAction[],
  sessionLabels: Map<number, string>,
): ReplayState {
  const state = emptyReplayState();
  const last = Math.min(cursor, events.length - 1);
  for (let i = 0; i <= last; i++) applyEvent(state, events[i], sessionLabels);
  state.cursor = last;
  for (const a of actions) if (a.at <= last) applyAction(state, a);
  // record ids that the trace writes later on
  for (const e of events) if (e.type === 'memory_write' && e.side === 'custos') state.futureRecordIds.add((e.payload as MemoryRecord).id);
  return state;
}

// ---- small selectors ------------------------------------------------------------------------------------

export function sideRecords(state: ReplayState, side: Side): MemoryRecord[] {
  const s = state.sides[side];
  return s.order.map((id) => s.records[id]);
}

export function findCustosRecord(state: ReplayState, id: string): MemoryRecord | undefined {
  return state.sides.custos.records[id];
}

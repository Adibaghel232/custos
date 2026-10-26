// Payload shapes the UI expects for each TraceEvent.type.
// types.ts (spec 3.8) only says "payload shape keyed by type"; these are the concrete shapes the
// console reads. The backend trace writer should emit the same keys (extra keys are ignored).
import type {
  CheckResult, Claim, Decision, GateDecision, LedgerEntry, MemoryRecord, RecordStatus, RollbackResult, Side, Tier, TraceEvent,
} from './types';

export interface SessionStartPayload { n: number; label: string }

/** Content arriving from outside the agent (email, ERP sync, web page...). */
export interface InboundPayload {
  kind: 'email' | 'erp_sync' | 'web' | 'file' | 'peer_agent';
  ref: string;            // e_9, erp_V-1042 ... (same id the records cite as source.ref)
  from: string;           // sender address / system name
  subject?: string;
  text: string;
}

export interface MessagePayload { role: 'user' | 'assistant'; text: string }

/** Custos only: custody stamp. Creates the pipeline row for record_id. */
export interface StampPayload {
  record_id: string;
  tier: Tier;
  source: MemoryRecord['source'];
  principal: string;
  raw_hash: string;
  ledger_seq: number;
  parents?: string[];
}

export interface ClaimsPayload { record_id: string; claims: Claim[] }
export type CheckPayload = CheckResult & { record_id: string };
export type DecisionPayload = Decision & {
  record_id: string;
  status: RecordStatus;
  conflicts_with?: MemoryRecord['conflicts_with'];
};

/** memory_write carries a full MemoryRecord (latest state of that record). */
export type MemoryWritePayload = MemoryRecord;

export interface MemoryReadPayload {
  read_id: string;
  principal: string;
  purpose: string;
  query: string;
  returned: { id: string; tier: Tier | null; label?: string }[];
  filtered?: number;
  ledger_seq?: number;
  ms?: number;
}

export interface ToolCallPayload {
  call_id: string;
  tool: string;
  args: Record<string, string>;
  risk: 'low' | 'high';
  status: 'proposed' | 'executed' | 'blocked' | 'asked' | 'denied';
  result?: string;
}

export type GatePayload = GateDecision;
export type RevertPayload = RollbackResult;
export type LedgerPayload = LedgerEntry;

export interface OutcomePayload {
  side: Side;
  verdict: 'attacker_paid' | 'verified_paid' | 'blocked' | 'held' | (string & {});
  /** Rupees that left the building. 0 = nothing moved. (A boolean is tolerated.) */
  money_moved: number | boolean;
  to_account: string | null;
  to_payee?: string | null;
  headline?: string;
  detail?: string;
}

/** Auto-pause point for the narrator. */
export interface BeatPayload {
  id: string;
  title: string;
  note?: string;
  /** default true; false = marker only. */
  pause?: boolean;
}

export interface PayloadMap {
  session_start: SessionStartPayload;
  inbound: InboundPayload;
  message: MessagePayload;
  memory_write: MemoryWritePayload;
  stamp: StampPayload;
  claims: ClaimsPayload;
  check: CheckPayload;
  decision: DecisionPayload;
  memory_read: MemoryReadPayload;
  tool_call: ToolCallPayload;
  gate: GatePayload;
  revert: RevertPayload;
  ledger: LedgerPayload;
  outcome: OutcomePayload;
  beat: BeatPayload;
}

export function payloadOf<K extends TraceEvent['type']>(ev: TraceEvent, _type: K): PayloadMap[K] {
  return ev.payload as PayloadMap[K];
}

export const ALL_EVENT_TYPES: TraceEvent['type'][] = [
  'session_start', 'inbound', 'message', 'memory_write', 'stamp', 'claims', 'check', 'decision',
  'memory_read', 'tool_call', 'gate', 'revert', 'ledger', 'outcome', 'beat',
];

/** Human-readable check outcome classification (see CheckResult.result strings). */
export type CheckSeverity = 'ok' | 'info' | 'flag';

export function checkSeverity(c: CheckResult): CheckSeverity {
  const r = c.result.toLowerCase();
  // CUSUM prints its alarm threshold ("0.4 (alarm at 4.0)"): only a raised alarm is a flag.
  if (c.check === 'cusum') return /raised|triggered|exceeded|alarm!/.test(r) ? 'flag' : 'ok';
  if (/contradict|violation|ungrounded|unsupported|mismatch|injection|not entailed|not grounded|drift/.test(r)) return 'flag';
  if (/conflicts? with|conflicts?:|differs from/.test(r)) return 'flag';
  if (/^(none|no |ok|pass|clear)/.test(r) || /none found|no conflict|consistent|entail|neutral|supported|grounded|within/.test(r)) return 'ok';
  return c.check === 'instruction' ? 'info' : 'ok';
}

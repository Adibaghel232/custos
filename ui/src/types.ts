// Data contracts for the Custos demo console.
// Source of truth: docs/DEMO_DECK_PLAN.md section 3.8. The backend emits exactly these shapes.
// Do NOT change a field here without changing the spec and the backend. UI-only additions
// (payload shapes per TraceEvent type, API result wrappers) live in tracePayloads.ts and api.ts.

export type Tier = 'T0' | 'T1' | 'T2' | 'T3' | 'T4';
export type Side = 'undefended' | 'custos';
export type RecordStatus = 'pending' | 'committed' | 'quarantined' | 'rejected' | 'superseded' | 'reverted' | 'erased';

export interface Claim { id: string; s: string; r: string; o: string; valid_from?: string;
  type: 'fact' | 'preference' | 'event' | 'procedure'; }
export interface CheckResult { check: 'conflict' | 'nli' | 'grounding' | 'cusum' | 'instruction';
  result: string; score?: number; against?: string; model?: string; ms?: number; }
export interface Decision { verdict: 'commit' | 'version' | 'quarantine' | 'reject'; rule: string; explanation: string; }

export interface MemoryRecord {
  id: string; tenant: string; text: string; tier: Tier | null;   // null on the undefended side
  status: RecordStatus; kind: Claim['type'] | 'summary';
  source: { type: 'email' | 'web' | 'file' | 'peer_agent' | 'user' | 'erp' | 'crm' | 'tool' | 'system' | 'derived';
            ref: string; from?: string };
  principal: string; session: number; parents: string[];
  raw_hash: string; ledger_seq: number; sig_ok: boolean;
  valid_from?: string; valid_to?: string | null; recorded_at: string;
  claims?: Claim[]; checks?: CheckResult[]; decision?: Decision;
  conflicts_with?: { id: string; tier: Tier; text: string };
  status_history?: { ts: string; status: RecordStatus; by: string }[];
}

export interface GateArg { name: string; value: string; critical: boolean;
  sources: { record_id: string; tier: Tier; source_type: string; from?: string }[];
  min_tier: Tier | null; required_tier: Tier | null; ok: boolean; }
export interface GateDecision { call_id: string; tool: string; risk: 'low' | 'high'; args: GateArg[];
  decision: 'allow' | 'ask' | 'block'; rule: string;
  loo?: { replans: number; decisive: string[]; counterfactual: Record<string, string> }; ms: number; }

export interface RollbackResult { root: string; mode: 'revert' | 'erase'; closure_by_hop: string[][];
  rederived: { old: string; new: string }[];
  exposure: { sessions: number[]; users: string[]; reads: number;
              tool_calls: { call_id: string; tool: string; status: 'executed' | 'blocked' | 'asked' }[] };
  ledger_seq: number; }

export interface LedgerEntry { seq: number; ts: string;
  event: 'WRITE' | 'DECIDE' | 'READ' | 'GATE' | 'REVERT' | 'ERASE' | 'PROMOTE';
  record_id?: string; actor: string; payload_hash: string; prev_hash: string; hash: string; sig_ok: boolean; }

export interface GraphNode { id: string; kind: 'source' | 'record' | 'summary' | 'session'; label: string;
  tier: Tier | null; status: RecordStatus | 'n/a'; session?: number; }
export interface GraphEdge { id: string; source: string; target: string;
  kind: 'derived_from' | 'conflicts_with' | 'supersedes' | 'read_by'; }

export interface TraceEvent { seq: number; t_ms: number; side: Side; session: number;
  type: 'session_start' | 'inbound' | 'message' | 'memory_write' | 'stamp' | 'claims' | 'check' | 'decision'
      | 'memory_read' | 'tool_call' | 'gate' | 'revert' | 'ledger' | 'outcome' | 'beat';
  payload: unknown; }   // payload shape keyed by type: MemoryRecord fields, GateDecision, RollbackResult, ...
export interface Trace { trace_id: string; scenario: string; act: 1 | 2; recorded_at: string; live: boolean;
  models: Record<string, string>; backend: string; seed: number;
  sessions: { n: number; label: string }[]; events: TraceEvent[]; }

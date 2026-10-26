// Fixture-mode rollback for roots that have no authored result: compute the taint closure and the
// exposure from the data already on screen. (Live mode always asks the backend: POST /api/rollback.)
import type { GateDecision, MemoryRecord, RollbackResult } from '../types';
import { childrenMap, taintClosureByHop } from './graph';

export interface SynthContext {
  records: Array<Pick<MemoryRecord, 'id' | 'parents'>>;
  reads: Array<{ session: number; principal: string; returned: string[] }>;
  gates: Array<{ session: number; gate: GateDecision }>;
  ledgerTip: number;
}

export function synthesizeRollback(root: string, mode: 'revert' | 'erase', ctx: SynthContext): RollbackResult {
  const closure = taintClosureByHop(root, childrenMap(ctx.records));
  const ids = new Set(closure.flat());
  const incident = new Set<number>();
  const toolCalls: RollbackResult['exposure']['tool_calls'] = [];
  for (const { session, gate } of ctx.gates) {
    const depends = gate.args.some((a) => a.sources.some((s) => ids.has(s.record_id)));
    if (!depends) continue;
    toolCalls.push({ call_id: gate.call_id, tool: gate.tool, status: gate.decision === 'allow' ? 'executed' : gate.decision === 'block' ? 'blocked' : 'asked' });
    if (gate.decision !== 'allow') incident.add(session);
  }
  const exposed = ctx.reads.filter((r) => r.returned.some((id) => ids.has(id)) && !incident.has(r.session));
  return {
    root,
    mode,
    closure_by_hop: closure,
    rederived: [],
    exposure: {
      sessions: [...new Set(exposed.map((r) => r.session))].sort((a, b) => a - b),
      users: [...new Set(exposed.map((r) => r.principal))],
      reads: exposed.length,
      tool_calls: toolCalls,
    },
    ledger_seq: ctx.ledgerTip + 1,
  };
}

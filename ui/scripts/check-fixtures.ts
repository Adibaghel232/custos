// Validates the generated fixtures against the section 3.8 contracts and the scenario invariants.
// Exit code 1 on any failure. Run via `npm run fixtures`.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type {
  GateDecision, GraphEdge, GraphNode, LedgerEntry, MemoryRecord, RollbackResult, Trace, TraceEvent,
} from '../src/types.ts';
import { ALL_EVENT_TYPES } from '../src/tracePayloads.ts';
import type { OutcomePayload } from '../src/tracePayloads.ts';
import { verifyChain, tamperDemo } from '../src/lib/ledger.ts';

const here = dirname(fileURLToPath(import.meta.url));
const dir = join(here, '..', 'public', 'fixtures');
const load = <T>(name: string): T => JSON.parse(readFileSync(join(dir, name), 'utf8')) as T;

let failures = 0;
const ok = (cond: unknown, msg: string): void => {
  if (!cond) { failures++; console.error('  FAIL', msg); }
};

const TIERS = new Set(['T0', 'T1', 'T2', 'T3', 'T4']);
const STATUSES = new Set(['pending', 'committed', 'quarantined', 'rejected', 'superseded', 'reverted', 'erased']);
const SIDES = new Set(['undefended', 'custos']);

const seenTypes = new Set<string>();

for (const act of [1, 2] as const) {
  console.log(`act ${act}`);
  const trace = load<Trace>(`trace_act${act}.json`);
  const graph = load<{ nodes: GraphNode[]; edges: GraphEdge[] }>(`graph_act${act}.json`);
  const ledger = load<LedgerEntry[]>(act === 1 ? 'ledger_act1.json' : 'ledger.json');
  const records = load<Record<string, MemoryRecord>>(`records_act${act}.json`);
  const rollback = load<Record<string, { revert: { result: RollbackResult; records: MemoryRecord[]; ledger: LedgerEntry[] }; erase: { result: RollbackResult; records: MemoryRecord[]; ledger: LedgerEntry[] } }>>('rollback.json');

  ok(trace.act === act, 'trace.act matches file');
  ok(trace.sessions.length === 9, 'nine sessions');
  ok(trace.events.length > 50, 'enough events');

  // events: ordered, typed, sided
  let prevSeq = 0;
  let prevT = -1;
  for (const e of trace.events as TraceEvent[]) {
    seenTypes.add(e.type);
    ok(e.seq === prevSeq + 1, `seq contiguous at ${e.seq}`);
    ok(e.t_ms >= prevT, `t_ms non-decreasing at seq ${e.seq}`);
    ok(ALL_EVENT_TYPES.includes(e.type), `known type ${e.type}`);
    ok(SIDES.has(e.side), `known side at seq ${e.seq}`);
    ok(e.session >= 1 && e.session <= 9, `session range at seq ${e.seq}`);
    prevSeq = e.seq;
    prevT = e.t_ms;
  }
  for (const s of trace.sessions) {
    ok(trace.events.some((e) => e.type === 'session_start' && e.session === s.n && e.side === 'custos'), `session_start custos S${s.n}`);
    ok(trace.events.some((e) => e.type === 'session_start' && e.session === s.n && e.side === 'undefended'), `session_start undefended S${s.n}`);
  }

  // records written
  const written = new Map<string, MemoryRecord>();
  for (const e of trace.events) {
    if (e.type !== 'memory_write') continue;
    const r = e.payload as MemoryRecord;
    written.set(`${e.side}:${r.id}`, r);
    ok(STATUSES.has(r.status), `status valid for ${r.id}`);
    if (e.side === 'custos') ok(r.tier !== null && TIERS.has(r.tier), `custos record ${r.id} has a tier`);
    else ok(r.tier === null, `undefended record ${r.id} has no tier`);
    ok(/^[0-9a-f]{64}$/.test(r.raw_hash), `raw_hash looks like sha256 for ${r.id}`);
    if (e.side === 'custos') ok(ledger.some((l) => l.seq === r.ledger_seq), `ledger_seq ${r.ledger_seq} of ${r.id} exists`);
  }
  ok(written.has('custos:m_02') && written.get('custos:m_02')?.tier === 'T3', 'm_02 is T3');
  ok(written.get('custos:m_23')?.tier === 'T0', 'm_23 inherits T0 (low-water mark)');
  ok(written.get('custos:m_25')?.tier === 'T0', 'm_25 inherits T0');

  // ledger
  const v = verifyChain(ledger);
  ok(v.ok, `ledger verifies (${ledger.length} entries)`);
  const t = tamperDemo(ledger);
  ok(!t.ok && t.first_bad_seq === t.tampered_seq, 'tamper demo is detected at the edited seq');
  for (const e of trace.events) {
    if (e.type !== 'ledger') continue;
    const le = e.payload as LedgerEntry;
    ok(ledger.some((l) => l.seq === le.seq && l.hash === le.hash), `ledger event ${le.seq} matches ledger file`);
  }

  // graph
  const nodeIds = new Set(graph.nodes.map((n) => n.id));
  ok(nodeIds.size === graph.nodes.length, 'graph node ids unique');
  for (const ed of graph.edges) ok(nodeIds.has(ed.source) && nodeIds.has(ed.target), `edge endpoints ${ed.id}`);
  for (const r of Object.values(records)) for (const p of r.parents) ok(nodeIds.has(p) || records[p] !== undefined, `parent ${p} of ${r.id} resolvable`);

  // gate
  const gates = trace.events.filter((e) => e.type === 'gate').map((e) => e.payload as GateDecision);
  ok(gates.length === 1, 'exactly one gate decision per act');
  for (const g of gates) {
    const criticalFail = g.args.some((a) => a.critical && !a.ok);
    ok((g.decision === 'block') === criticalFail, 'gate blocks iff a critical argument fails');
    for (const a of g.args) if (a.critical) ok(a.ok === (a.min_tier !== null && a.required_tier !== null && Number(a.min_tier.slice(1)) >= Number(a.required_tier.slice(1))), `arg ${a.name} ok flag consistent`);
  }
  const outcomes = trace.events.filter((e) => e.type === 'outcome').map((e) => e.payload as OutcomePayload);
  ok(outcomes.length === 2, 'two outcomes (one per side)');
  ok(outcomes.find((o) => o.side === 'undefended')?.verdict === 'attacker_paid', 'undefended pays the attacker');
  if (act === 1) {
    ok(outcomes.find((o) => o.side === 'custos')?.to_account === '****4410', 'act1 custos pays the ERP account');
    ok(written.get('custos:m_14')?.status === 'quarantined', 'act1 m_14 quarantined');
    ok(written.get('custos:m_14')?.conflicts_with?.id === 'm_02', 'act1 m_14 conflicts with m_02');
  } else {
    ok(gates[0].decision === 'block', 'act2 gate blocks');
    ok(JSON.stringify(gates[0].loo?.decisive) === '["m_17"]', 'act2 leave-one-out names m_17');
    ok(written.get('custos:m_17')?.status === 'committed' && written.get('custos:m_17')?.tier === 'T0', 'act2 m_17 committed as T0');
    ok(trace.events.some((e) => e.type === 'revert'), 'act2 trace holds the recorded revert event');
  }

  // rollback
  const root = act === 1 ? 'm_14' : 'm_17';
  const rb = rollback[root];
  ok(!!rb, `rollback fixture for ${root}`);
  if (rb) {
    for (const mode of ['revert', 'erase'] as const) {
      const r = rb[mode].result;
      ok(r.mode === mode && r.root === root, `${root} ${mode} result header`);
      ok(r.rederived.length === 1 && r.rederived[0].old === 'm_23' && r.rederived[0].new === 'm_31', `${root} ${mode} re-derives m_23 -> m_31`);
      const chain = [...ledger, ...rb[mode].ledger];
      ok(verifyChain(chain).ok, `${root} ${mode} extended ledger verifies`);
      ok(rb[mode].ledger[0].seq === r.ledger_seq, `${root} ${mode} ledger_seq points at the REVERT/ERASE entry`);
    }
    if (act === 2) {
      const r = rb.revert.result;
      ok(JSON.stringify(r.closure_by_hop) === '[["m_17"],["m_23","m_25"]]', 'act2 closure_by_hop');
      ok(JSON.stringify(r.exposure.sessions) === '[5,7]' && r.exposure.reads === 3, 'act2 exposure sessions 5,7 / 3 reads');
      ok(r.exposure.tool_calls.length === 1 && r.exposure.tool_calls[0].status === 'blocked', 'act2 exposure: 1 blocked call');
      const ev = trace.events.find((e) => e.type === 'revert');
      ok(JSON.stringify(ev?.payload) === JSON.stringify(r), 'trace revert event equals rollback.json revert result');
    }
  }
}

// every TraceEvent type appears somewhere across the two traces
for (const t of ALL_EVENT_TYPES) ok(seenTypes.has(t), `event type ${t} appears in the fixtures`);

// bench
const summary = load<Record<string, unknown>>('bench_summary.json');
const KEYS = ['EPISODES', 'N_ATTACK', 'CALLS', 'GPU_HOURS', 'COST_USD', 'GPU', 'N_BACKENDS', 'K',
  'ASR_U', 'ASR_PI', 'ASR_AMG', 'ASR_C', 'ISR_U', 'ISR_C', 'AASR_U', 'AASR_PI', 'AASR_C', 'ASR_U_F03', 'ASR_C_F03', 'ASR_U_F04', 'ISR_U_MEM0', 'ISR_C_MEM0',
  'FQR_C', 'FQR_AMG', 'UTIL_D', 'LEAK_K', 'LEAK_N', 'EXTRACT_C', 'ERASE_C', 'ERASE_U', 'DERIVED_N',
  'TAMPER', 'TAMPER_N', 'LINEAGE', 'ATTR', 'LEDGER_N', 'P95_READ', 'P95_GATE', 'TOKENS_WRITE', 'WRITES_S', 'READS_S',
  'MGS_C', 'MGS_U', 'MET', 'DEMO_X', 'DEMO_K', 'RUN_ID'];
for (const k of KEYS) ok(k in summary, `bench_summary has ${k}`);
ok(summary.SAMPLE === true && String(summary.RUN_ID).startsWith('SAMPLE'), 'bench fixture is flagged as SAMPLE');
ok(load<unknown[]>('bench_F1.json').length === 44, 'F1 has 11 families x 4 defences');

if (failures) {
  console.error(`\n${failures} fixture check(s) failed`);
  process.exit(1);
}
console.log('\nall fixture checks passed');

// Generates ui/public/fixtures/*.json from the scenario script (docs/DEMO_DECK_PLAN.md sections 1.5, 3.8, 5).
// Run: npm run fixtures   (generates, then validates with scripts/check-fixtures.ts)
// The fixtures are what the console shows when the FastAPI backend is unreachable. They are hand-scripted
// from the demo scenario; hashes are computed (never typed), so the ledger chain really verifies.
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildAct } from './fixtures/scenario.ts';
import { benchSummary, f1Rows, f2Rows, f3Rows, f4Rows, f5Rows, f6Rows } from './fixtures/bench.ts';

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, '..', 'public', 'fixtures');
mkdirSync(out, { recursive: true });

const write = (name: string, data: unknown): void => {
  writeFileSync(join(out, name), JSON.stringify(data, null, 1) + '\n', 'utf8');
  console.log('  wrote', name);
};

const a1 = buildAct(1);
const a2 = buildAct(2);

console.log('traces');
write('trace_act1.json', a1.trace);
write('trace_act2.json', a2.trace);
write('traces.json', [a1.trace, a2.trace].map((t) => ({
  trace_id: t.trace_id, scenario: t.scenario, act: t.act, recorded_at: t.recorded_at, live: t.live, backend: t.backend,
  events: t.events.length, models: t.models,
})));

console.log('store snapshots');
write('graph_act1.json', a1.graph);
write('graph_act2.json', a2.graph);
write('ledger_act1.json', a1.ledger);
write('ledger.json', a2.ledger);
write('records_act1.json', a1.records);
write('records_act2.json', a2.records);
write('quarantine.json', a1.quarantine);
write('rollback.json', { ...a1.rollback, ...a2.rollback });

console.log('bench (SAMPLE DATA)');
write('bench_summary.json', benchSummary());
write('bench_F1.json', f1Rows());
write('bench_F2.json', f2Rows());
write('bench_F3.json', f3Rows());
write('bench_F4.json', f4Rows());
write('bench_F5.json', f5Rows());
write('bench_F6.json', f6Rows());

console.log(`done: act1 ${a1.trace.events.length} events, act2 ${a2.trace.events.length} events`);

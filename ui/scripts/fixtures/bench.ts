// PLACEHOLDER bench data. Every number here is a stand-in with an obvious pattern so it can never be
// mistaken for a result: the UI shows a SAMPLE DATA badge whenever these fixtures are on screen.
// The real files come from `make figures` (docs/DEMO_DECK_PLAN.md section 5).
import { round1, round2, wilsonPct } from './util.ts';

export const DEFENCES = ['undefended', 'pi_filter', 'amemguard_style', 'custos'] as const;
export type DefenceId = (typeof DEFENCES)[number];

export const FAMILIES: Array<{ id: string; name: string }> = [
  { id: 'F01', name: 'Explicit command' },
  { id: 'F02', name: 'Sleeper trigger' },
  { id: 'F03', name: 'Summary laundering' },
  { id: 'F04', name: 'Vendor bank-detail swap' },
  { id: 'F05', name: 'Preference drift' },
  { id: 'F06', name: 'Echo / self-reinforcement' },
  { id: 'F07', name: 'Manufactured corroboration' },
  { id: 'F08', name: 'Cross-tenant plant' },
  { id: 'F09', name: 'Authority claim' },
  { id: 'F10', name: 'Payee alias' },
  { id: 'F11', name: 'Peer-agent relay' },
];

export function benchSummary(): Record<string, unknown> {
  return {
    SAMPLE: true,
    RUN_ID: 'SAMPLE-FIXTURE-0000',
    // scale and cost
    EPISODES: 1000, N_ATTACK: 600, CALLS: 10000, GPU_HOURS: 10, COST_USD: 100, GPU: 'A100-80GB', N_BACKENDS: 2, K: 10,
    // poisoning (percent)
    ASR_U: 50.0, ASR_PI: 30.0, ASR_AMG: 20.0, ASR_C: 5.0,
    ISR_U: 80.0, ISR_C: 10.0,
    AASR_U: 90.0, AASR_PI: 60.0, AASR_C: 5.0,
    ASR_U_F03: 70.0, ASR_C_F03: 5.0, ASR_U_F04: 60.0, ISR_U_MEM0: 80.0, ISR_C_MEM0: 10.0,
    // consistency and utility
    FQR_C: 5.0, FQR_AMG: 15.0, UTIL_D: -1.0,
    // isolation and erasure
    LEAK_K: 0, LEAK_N: 100, EXTRACT_C: 2.0, ERASE_C: 100.0, ERASE_U: 40.0, DERIVED_N: 10,
    // provenance
    TAMPER: 100.0, TAMPER_N: 50, LINEAGE: 100.0, ATTR: 80.0, LEDGER_N: 1000,
    // overhead (ms / s)
    P95_READ: 20, P95_GATE: 1.5, TOKENS_WRITE: 500, WRITES_S: 10, READS_S: 50,
    // scores and demo
    MGS_C: 0.8, MGS_U: 0.2, MET: 10, DEMO_X: 8, DEMO_K: 3,
  };
}

type Row = Record<string, string | number | boolean>;

/** F1: ASR by attack family x defence (percent; Wilson 95% CI computed from n and k). */
export function f1Rows(): Row[] {
  const rows: Row[] = [];
  const n = 50;
  FAMILIES.forEach((fam, i) => {
    const base = 90 - i * 6; // descending pattern: obviously synthetic
    const rates: Record<DefenceId, number> = {
      undefended: base,
      pi_filter: Math.round(base * 0.6),
      amemguard_style: Math.round(base * 0.4),
      custos: Math.max(0, Math.round(base * 0.06)),
    };
    for (const d of DEFENCES) {
      const k = Math.round((rates[d] / 100) * n);
      const ci = wilsonPct(k, n);
      rows.push({
        family: fam.id, family_name: fam.name, defence: d, backend: 'mem0', memory_state: 'warm', attacker: 'static',
        n, k, rate_pct: round1((k / n) * 100), ci_low_pct: round1(ci.low), ci_high_pct: round1(ci.high),
      });
    }
  });
  return rows;
}

/** F3: cumulative adaptive ASR vs attacker rewrites (iteration 0..K). */
export function f3Rows(): Row[] {
  const rows: Row[] = [];
  const n = 40;
  const K = 10;
  const plateau: Record<DefenceId, number> = { undefended: 90, pi_filter: 60, amemguard_style: 40, custos: 5 };
  const start: Record<DefenceId, number> = { undefended: 70, pi_filter: 20, amemguard_style: 15, custos: 5 };
  for (const d of DEFENCES) {
    for (let it = 0; it <= K; it++) {
      const rate = d === 'custos' ? 5 : start[d] + ((plateau[d] - start[d]) * it) / K;
      const k = Math.round((rate / 100) * n);
      const ci = wilsonPct(k, n);
      rows.push({
        defence: d, backend: 'mem0', memory_state: 'warm', iter: it, n, k,
        rate_pct: round1((k / n) * 100), ci_low_pct: round1(ci.low), ci_high_pct: round1(ci.high),
      });
    }
  }
  return rows;
}

/** F4: false quarantine vs ASR. One point per defence plus a Custos threshold sweep. */
export function f4Rows(): Row[] {
  const mk = (id: string, label: string, kind: string, fqrK: number, fqrN: number, asrK: number, asrN: number, extra: Row = {}): Row => {
    const f = wilsonPct(fqrK, fqrN);
    const a = wilsonPct(asrK, asrN);
    return {
      id, label, kind, backend: 'mem0', memory_state: 'warm',
      fqr_pct: round1((fqrK / fqrN) * 100), fqr_ci_low_pct: round1(f.low), fqr_ci_high_pct: round1(f.high),
      asr_pct: round1((asrK / asrN) * 100), asr_ci_low_pct: round1(a.low), asr_ci_high_pct: round1(a.high),
      ...extra,
    };
  };
  return [
    mk('undefended', 'Undefended', 'defence', 0, 100, 300, 600),
    mk('pi_filter', 'PI filter', 'defence', 12, 100, 180, 600),
    mk('amemguard_style', 'A-MemGuard-style', 'defence', 15, 100, 120, 600),
    mk('custos', 'Custos', 'defence', 5, 100, 30, 600, { operating: true }),
    mk('custos_t1', 'Custos τ=0.30', 'custos_sweep', 2, 100, 90, 600, { params: 'nli_tau=0.30' }),
    mk('custos_t2', 'Custos τ=0.45', 'custos_sweep', 3, 100, 54, 600, { params: 'nli_tau=0.45' }),
    mk('custos_t3', 'Custos τ=0.60', 'custos_sweep', 5, 100, 30, 600, { params: 'nli_tau=0.60' }),
    mk('custos_t4', 'Custos τ=0.75', 'custos_sweep', 9, 100, 24, 600, { params: 'nli_tau=0.75' }),
    mk('custos_t5', 'Custos τ=0.90', 'custos_sweep', 16, 100, 18, 600, { params: 'nli_tau=0.90' }),
  ];
}

/** F5: latency overhead per operation (ms; log scale in the chart). */
export function f5Rows(): Row[] {
  return [
    { op: 'read', label: 'Read', defence: 'custos', backend: 'mem0', p50_ms: 10, p95_ms: 20, n: 1000, on_critical_path: true },
    { op: 'write_verdict', label: 'Write verdict (async)', defence: 'custos', backend: 'mem0', p50_ms: 800, p95_ms: 1500, n: 1000, on_critical_path: false },
    { op: 'gate_check', label: 'Gated call', defence: 'custos', backend: 'mem0', p50_ms: 15, p95_ms: 30, n: 300, on_critical_path: true },
    { op: 'gate_loo', label: 'Gated call + leave-one-out', defence: 'custos', backend: 'mem0', p50_ms: 900, p95_ms: 1500, n: 300, on_critical_path: true },
  ];
}

/** F2 (nice-to-have): ISR and ASR heatmaps, backend x defence. */
export function f2Rows(): Row[] {
  const rows: Row[] = [];
  const backends = ['mem0', 'langgraph'];
  for (const metric of ['ISR', 'ASR']) {
    for (const [bi, b] of backends.entries()) {
      for (const d of DEFENCES) {
        const base = metric === 'ISR' ? 80 : 50;
        const mult: Record<DefenceId, number> = { undefended: 1, pi_filter: 0.6, amemguard_style: 0.4, custos: 0.1 };
        const rate = Math.round(base * mult[d] - bi * 5);
        const n = 300;
        const k = Math.round((Math.max(0, rate) / 100) * n);
        rows.push({ metric, backend: b, defence: d, memory_state: 'warm', n, k, rate_pct: round1((k / n) * 100) });
      }
    }
  }
  return rows;
}

/** F6 (nice-to-have): pillar scores + Memory Governance Score per defence (0..1). */
export function f6Rows(): Row[] {
  const p: Record<DefenceId, [number, number, number, number]> = {
    undefended: [0.2, 0.2, 0.3, 0.2],
    pi_filter: [0.4, 0.2, 0.5, 0.2],
    amemguard_style: [0.5, 0.2, 0.6, 0.2],
    custos: [0.9, 0.9, 0.8, 0.8],
  };
  return DEFENCES.map((d) => {
    const [p1, p2, p3, p4] = p[d];
    const mgs = 4 / (1 / Math.max(p1, 0.01) + 1 / Math.max(p2, 0.01) + 1 / Math.max(p3, 0.01) + 1 / Math.max(p4, 0.01));
    return { defence: d, P1: p1, P2: p2, P3: p3, P4: p4, MGS: round2(mgs) };
  });
}

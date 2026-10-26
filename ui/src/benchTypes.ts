// Bench dashboard data shapes (docs/DEMO_DECK_PLAN.md section 5).
// KPI JSON = final.json (same keys as the deck placeholders). Figure JSON = rows written by `make figures`;
// section 5 does not fix their field names, so these are the shapes the UI reads (see report).
// Percent fields end in _pct. For convenience the loader also accepts rate / ci_low / ci_high as fractions 0..1.

export type DefenceId = 'undefended' | 'pi_filter' | 'amemguard_style' | 'custos';

export const DEFENCE_ORDER: DefenceId[] = ['undefended', 'pi_filter', 'amemguard_style', 'custos'];

export const DEFENCE_LABEL: Record<DefenceId, string> = {
  undefended: 'Undefended',
  pi_filter: 'PI filter',
  amemguard_style: 'A-MemGuard-style',
  custos: 'Custos',
};

export type BenchSummary = Record<string, number | string | boolean | undefined>;

export interface F1Row {
  family: string;
  family_name: string;
  defence: DefenceId;
  backend?: string;
  memory_state?: string;
  attacker?: string;
  n: number;
  k?: number;
  rate_pct: number;
  ci_low_pct: number;
  ci_high_pct: number;
}

export interface F2Row {
  metric: 'ISR' | 'ASR';
  backend: string;
  defence: DefenceId;
  memory_state?: string;
  n: number;
  k?: number;
  rate_pct: number;
}

export interface F3Row {
  defence: DefenceId;
  backend?: string;
  memory_state?: string;
  iter: number;
  n: number;
  k?: number;
  rate_pct: number;
  ci_low_pct: number;
  ci_high_pct: number;
}

export interface F4Row {
  id: string;
  label: string;
  kind: 'defence' | 'custos_sweep';
  backend?: string;
  memory_state?: string;
  fqr_pct: number;
  fqr_ci_low_pct: number;
  fqr_ci_high_pct: number;
  asr_pct: number;
  asr_ci_low_pct: number;
  asr_ci_high_pct: number;
  operating?: boolean;
  params?: string;
}

export interface F5Row {
  op: string;
  label?: string;
  defence?: string;
  backend?: string;
  p50_ms: number;
  p95_ms: number;
  n?: number;
  on_critical_path?: boolean;
}

export interface F6Row {
  defence: DefenceId;
  P1: number;
  P2: number;
  P3: number;
  P4: number;
  MGS: number;
}

export type FigureName = 'F1' | 'F2' | 'F3' | 'F4' | 'F5' | 'F6';

// ---- tolerant normalisation -------------------------------------------------------------------------

type Raw = Record<string, unknown>;

const num = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) ? v : typeof v === 'string' && v.trim() !== '' && Number.isFinite(Number(v)) ? Number(v) : undefined);

/** Reads `<base>_pct`, or `<base>` as a fraction 0..1 (cells.csv style). */
function pctField(r: Raw, base: string): number | undefined {
  const direct = num(r[`${base}_pct`]);
  if (direct !== undefined) return direct;
  const frac = num(r[base]);
  return frac === undefined ? undefined : frac * 100;
}

export function rowsOf(raw: unknown): Raw[] {
  if (Array.isArray(raw)) return raw as Raw[];
  if (raw && typeof raw === 'object') {
    const o = raw as Raw;
    for (const key of ['rows', 'data', 'items']) if (Array.isArray(o[key])) return o[key] as Raw[];
  }
  return [];
}

export function normF1(raw: unknown): F1Row[] {
  const out: F1Row[] = [];
  for (const r of rowsOf(raw)) {
    const rate = pctField(r, 'rate');
    if (rate === undefined || typeof r.defence !== 'string') continue;
    out.push({
      family: String(r.family ?? ''), family_name: String(r.family_name ?? r.family ?? ''), defence: r.defence as DefenceId,
      backend: r.backend as string | undefined, memory_state: r.memory_state as string | undefined, attacker: r.attacker as string | undefined,
      n: num(r.n) ?? 0, k: num(r.k), rate_pct: rate,
      ci_low_pct: pctField(r, 'ci_low') ?? rate, ci_high_pct: pctField(r, 'ci_high') ?? rate,
    });
  }
  return out;
}

export function normF2(raw: unknown): F2Row[] {
  const out: F2Row[] = [];
  for (const r of rowsOf(raw)) {
    const rate = pctField(r, 'rate');
    if (rate === undefined || typeof r.defence !== 'string') continue;
    out.push({ metric: (String(r.metric).toUpperCase() === 'ISR' ? 'ISR' : 'ASR'), backend: String(r.backend ?? ''), defence: r.defence as DefenceId, memory_state: r.memory_state as string | undefined, n: num(r.n) ?? 0, k: num(r.k), rate_pct: rate });
  }
  return out;
}

export function normF3(raw: unknown): F3Row[] {
  const out: F3Row[] = [];
  for (const r of rowsOf(raw)) {
    const rate = pctField(r, 'rate');
    const iter = num(r.iter ?? r.iteration);
    if (rate === undefined || iter === undefined || typeof r.defence !== 'string') continue;
    out.push({
      defence: r.defence as DefenceId, backend: r.backend as string | undefined, memory_state: r.memory_state as string | undefined,
      iter, n: num(r.n) ?? 0, k: num(r.k), rate_pct: rate, ci_low_pct: pctField(r, 'ci_low') ?? rate, ci_high_pct: pctField(r, 'ci_high') ?? rate,
    });
  }
  return out;
}

export function normF4(raw: unknown): F4Row[] {
  const out: F4Row[] = [];
  for (const r of rowsOf(raw)) {
    const fqr = pctField(r, 'fqr');
    const asr = pctField(r, 'asr');
    if (fqr === undefined || asr === undefined) continue;
    out.push({
      id: String(r.id ?? r.config_id ?? r.defence ?? ''), label: String(r.label ?? r.defence ?? r.id ?? ''),
      kind: r.kind === 'custos_sweep' ? 'custos_sweep' : 'defence',
      backend: r.backend as string | undefined, memory_state: r.memory_state as string | undefined,
      fqr_pct: fqr, fqr_ci_low_pct: pctField(r, 'fqr_ci_low') ?? fqr, fqr_ci_high_pct: pctField(r, 'fqr_ci_high') ?? fqr,
      asr_pct: asr, asr_ci_low_pct: pctField(r, 'asr_ci_low') ?? asr, asr_ci_high_pct: pctField(r, 'asr_ci_high') ?? asr,
      operating: r.operating === true, params: r.params as string | undefined,
    });
  }
  return out;
}

export function normF5(raw: unknown): F5Row[] {
  const out: F5Row[] = [];
  for (const r of rowsOf(raw)) {
    const p50 = num(r.p50_ms ?? r.p50);
    const p95 = num(r.p95_ms ?? r.p95);
    if (p50 === undefined || p95 === undefined) continue;
    out.push({ op: String(r.op ?? ''), label: r.label as string | undefined, defence: r.defence as string | undefined, backend: r.backend as string | undefined, p50_ms: p50, p95_ms: p95, n: num(r.n), on_critical_path: r.on_critical_path as boolean | undefined });
  }
  return out;
}

export function normF6(raw: unknown): F6Row[] {
  const out: F6Row[] = [];
  for (const r of rowsOf(raw)) {
    const mgs = num(r.MGS ?? r.mgs);
    if (mgs === undefined || typeof r.defence !== 'string') continue;
    out.push({ defence: r.defence as DefenceId, P1: num(r.P1) ?? 0, P2: num(r.P2) ?? 0, P3: num(r.P3) ?? 0, P4: num(r.P4) ?? 0, MGS: mgs });
  }
  return out;
}

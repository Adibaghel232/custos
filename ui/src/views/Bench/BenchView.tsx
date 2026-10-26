import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { FlaskConical, Table2, BarChart3 } from 'lucide-react';
import { getBenchFigure, getBenchSummary } from '../../api';
import type { FigureData, Source } from '../../api';
import type { BenchSummary } from '../../benchTypes';
import { useData } from '../../state/app';
import { useUi } from '../../state/ui';
import { cx, formatCount } from '../../lib/format';
import { Pill } from '../../components/ui';
import { useTokens } from '../../lib/tokens';
import { ChartLegend, defenceLegend, F1Chart, F1Table, F3Chart, F3Table, F4Chart, F4Table, F5Chart, F5Table } from './charts';

// ---- KPI tiles -----------------------------------------------------------------------------------------

function CountUp({ value, decimals = 0, suffix = '', prefix = '' }: { value: number; decimals?: number; suffix?: string; prefix?: string }) {
  const { ui } = useUi();
  const [v, setV] = useState(ui.motionReduced ? value : 0);
  const raf = useRef(0);
  useEffect(() => {
    if (ui.motionReduced) { setV(value); return; }
    const start = performance.now();
    const dur = 1000;
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / dur);
      setV(value * (1 - Math.pow(1 - p, 3)));
      if (p < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [value, ui.motionReduced]);
  return <>{prefix}{v.toFixed(decimals).replace('-', '−')}{suffix}</>;
}

const n = (s: BenchSummary, k: string): number | undefined => (typeof s[k] === 'number' ? (s[k] as number) : undefined);

function Tile({ label, hero, children, sub, bar }: { label: string; hero?: boolean; children: ReactNode; sub: ReactNode; bar: string }) {
  return (
    <div className={cx('card relative flex min-w-0 flex-col justify-between overflow-hidden px-4 py-2.5', hero && '2xl:col-span-1')}>
      <span aria-hidden className="absolute inset-y-0 left-0 w-1" style={{ background: bar }} />
      <div className="text-sm font-semibold text-muted">{label}</div>
      <div className={cx('mt-0.5 font-extrabold leading-none tracking-tight text-ink', hero ? 'text-5xl' : 'text-[2.1rem]')}>{children}</div>
      <div className="mt-1.5 text-sm leading-snug text-muted">{sub}</div>
    </div>
  );
}

function Kpis({ s }: { s: BenchSummary }) {
  const t = useTokens();
  const dash = <span className="text-faint">—</span>;
  const a = (k: string, d = 1, suffix = '%') => (n(s, k) === undefined ? dash : <CountUp value={n(s, k)!} decimals={d} suffix={suffix} />);
  const leakN = n(s, 'LEAK_N');
  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4 2xl:grid-cols-[1.45fr_repeat(6,minmax(0,1fr))]">
      <Tile label="Attack success (ASR)" hero bar={t['def-custos']}
        sub={<>undefended → Custos · n = {n(s, 'N_ATTACK') !== undefined ? formatCount(n(s, 'N_ATTACK')!) : '—'}</>}>
        <span className="text-faint line-through decoration-2"><CountUp value={n(s, 'ASR_U') ?? 0} decimals={0} suffix="%" /></span>
        <span className="mx-1.5 text-3xl text-muted">→</span>{a('ASR_C')}
      </Tile>
      <Tile label="Injection success (ISR)" bar={t['def-custos']} sub="undefended → Custos">
        <span className="text-faint line-through decoration-2"><CountUp value={n(s, 'ISR_U') ?? 0} decimals={0} suffix="%" /></span>
        <span className="mx-1 text-2xl text-muted">→</span>{a('ISR_C', 0)}
      </Tile>
      <Tile label="Adaptive ASR" bar={t['def-custos']} sub={<>Custos · PI filter {n(s, 'AASR_PI') !== undefined ? `${n(s, 'AASR_PI')}%` : '—'} · K = {String(s.K ?? '—')}</>}>{a('AASR_C')}</Tile>
      <Tile label="False quarantine" bar={t['def-custos']} sub={<>Custos · A-MemGuard-style {n(s, 'FQR_AMG') !== undefined ? `${n(s, 'FQR_AMG')}%` : '—'}</>}>{a('FQR_C')}</Tile>
      <Tile label="Cross-tenant leakage" bar={t['def-custos']} sub={leakN ? <>bound ≤ 3/n = {(300 / leakN).toFixed(1)}%</> : '—'}>
        {n(s, 'LEAK_K') === undefined ? dash : <><CountUp value={n(s, 'LEAK_K')!} /><span className="text-2xl text-muted"> / {leakN !== undefined ? formatCount(leakN) : '—'}</span></>}
      </Tile>
      <Tile label="Utility Δ" bar={t.info} sub="LongMemEval, Custos off vs on">{a('UTIL_D', 1, ' pt')}</Tile>
      <Tile label="Memory Governance Score" bar={t['def-custos']} sub={<>Custos · undefended {n(s, 'MGS_U')?.toFixed(2) ?? '—'}</>}>{a('MGS_C', 2, '')}</Tile>
    </div>
  );
}

// ---- chart card ---------------------------------------------------------------------------------------

function ChartCard({ title, sub, legend, chart, table, className, sample }: { title: string; sub: string; legend: ReactNode; chart: ReactNode; table: ReactNode; className?: string; sample: boolean }) {
  const [asTable, setAsTable] = useState(false);
  return (
    <section className={cx('card relative flex min-h-[17rem] min-w-0 flex-col overflow-hidden xl:min-h-0', sample && 'sample-watermark', className)}>
      <header className="flex items-start gap-3 px-4 pt-3">
        <div className="min-w-0">
          <h3 className="text-lg font-bold leading-snug text-ink">{title}</h3>
          <p className="text-sm text-muted">{sub}</p>
        </div>
        <button type="button" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable} title="Switch between chart and table view" className="btn ml-auto !px-2.5 !py-1 text-sm">
          {asTable ? <BarChart3 size={16} /> : <Table2 size={16} />}{asTable ? 'Chart' : 'Table'}
        </button>
      </header>
      <div className="px-4 pb-1 pt-2">{legend}</div>
      <div className="min-h-0 flex-1 px-2 pb-2">{asTable ? table : chart}</div>
    </section>
  );
}

// ---- view ---------------------------------------------------------------------------------------------

export function BenchView() {
  const t = useTokens();
  const { markFixture } = useData();
  const [summary, setSummary] = useState<BenchSummary | null>(null);
  const [fig, setFig] = useState<Partial<FigureData>>({});
  const [sources, setSources] = useState<Source[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [backend, setBackend] = useState<string>('');
  const [memory, setMemory] = useState<string>('');

  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const [s, f1, f3, f4, f5] = await Promise.all([getBenchSummary(), getBenchFigure('F1'), getBenchFigure('F3'), getBenchFigure('F4'), getBenchFigure('F5')]);
        if (dead) return;
        setSummary(s.data);
        setFig({ F1: f1.data, F3: f3.data, F4: f4.data, F5: f5.data });
        const src = [s.source, f1.source, f3.source, f4.source, f5.source];
        setSources(src);
        src.forEach(markFixture);
      } catch (e) {
        if (!dead) setErr((e as Error).message);
      }
    })();
    return () => { dead = true; };
  }, [markFixture]);

  const options = useMemo(() => {
    const rows = [...(fig.F1 ?? []), ...(fig.F3 ?? []), ...(fig.F4 ?? []), ...(fig.F5 ?? [])] as Array<{ backend?: string; memory_state?: string }>;
    return { backends: [...new Set(rows.map((r) => r.backend).filter((x): x is string => !!x))], memories: [...new Set(rows.map((r) => r.memory_state).filter((x): x is string => !!x))] };
  }, [fig]);
  const be = backend || options.backends[0] || '';
  const mem = memory || (options.memories.includes('warm') ? 'warm' : options.memories[0]) || '';
  const keep = <T extends { backend?: string; memory_state?: string }>(rows: T[] | undefined): T[] => (rows ?? []).filter((r) => (!r.backend || !be || r.backend === be) && (!r.memory_state || !mem || r.memory_state === mem));
  const f1 = keep(fig.F1).filter((r) => !r.attacker || r.attacker === 'static');
  const f3 = keep(fig.F3);
  const f4 = keep(fig.F4);
  const f5 = keep(fig.F5);

  if (err) return <div className="grid h-full place-items-center p-8"><div className="card max-w-lg p-8 text-center"><h2 className="text-2xl font-bold">Could not load bench data</h2><p className="mt-2 text-muted">{err}</p></div></div>;
  if (!summary) return <div className="grid h-full place-items-center"><div className="h-9 w-9 animate-spin rounded-full border-4 border-border border-t-ok" /></div>;

  const sample = sources.includes('fixture') || summary.SAMPLE === true || String(summary.RUN_ID ?? '').toUpperCase().startsWith('SAMPLE');
  const nAttack = n(summary, 'N_ATTACK');
  const subtitle = (extra: string) => `${extra} · ${be || 'backend'} · memory ${mem || '—'} · 95% Wilson CI`;

  return (
    <div className="h-full overflow-y-auto">
      <div className="flex h-full min-h-[60rem] flex-col gap-3 p-3 2xl:gap-4 2xl:p-4">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          <FlaskConical size={24} className="text-muted" />
          <h2 className="text-xl font-extrabold tracking-wide">CUSTOS-BENCH</h2>
          <span className="text-base text-ink">run <b className="mono">{String(summary.RUN_ID ?? '—')}</b></span>
          <span className="text-base text-muted"><b className="text-ink">{n(summary, 'EPISODES') !== undefined ? formatCount(n(summary, 'EPISODES')!) : '—'}</b> episodes</span>
          <span className="text-base text-muted"><b className="text-ink">{String(summary.GPU_HOURS ?? '—')}</b> GPU-h on Brev ({String(summary.GPU ?? '—')})</span>
          {sample && <Pill tone="warn" className="!px-3 !py-1 !text-sm font-extrabold tracking-wider" title="These numbers come from the placeholder fixtures in ui/public/fixtures. They are NOT results.">SAMPLE DATA · placeholder numbers, not results</Pill>}
          <div className="ml-auto flex items-center gap-3 text-sm text-muted">
            <label className="flex items-center gap-2">backend
              <select value={be} onChange={(e) => setBackend(e.target.value)} className="rounded-lg border border-border bg-panel2 px-2.5 py-1.5 text-sm font-semibold text-ink" disabled={options.backends.length < 2}>
                {(options.backends.length ? options.backends : ['—']).map((b) => <option key={b}>{b}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-2">memory
              <select value={mem} onChange={(e) => setMemory(e.target.value)} className="rounded-lg border border-border bg-panel2 px-2.5 py-1.5 text-sm font-semibold text-ink" disabled={options.memories.length < 2}>
                {(options.memories.length ? options.memories : ['—']).map((m) => <option key={m}>{m}</option>)}
              </select>
            </label>
          </div>
        </div>

        <Kpis s={summary} />

        <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 xl:grid-cols-[1.08fr_1fr] xl:grid-rows-[minmax(0,1fr)_minmax(0,1fr)] 2xl:gap-4">
          <ChartCard className="xl:row-span-2" sample={sample}
            title="Attack success falls across all 11 families" sub={subtitle(`F1 · ASR by attack family × defence, n = ${nAttack !== undefined ? formatCount(nAttack) : '—'}`)}
            legend={<ChartLegend items={defenceLegend(t)} />} chart={<F1Chart rows={f1} />} table={<F1Table rows={f1} />} />
          <ChartCard sample={sample}
            title="Detectors erode with every rewrite; Custos stays flat" sub={subtitle(`F3 · adaptive ASR vs attacker rewrites (K = ${String(summary.K ?? '—')})`)}
            legend={<ChartLegend items={defenceLegend(t, true)} />} chart={<F3Chart rows={f3} />} table={<F3Table rows={f3} />} />
          <div className="grid min-h-0 grid-cols-1 gap-3 md:grid-cols-2 2xl:gap-4 [&>*]:min-h-0">
            <ChartCard sample={sample}
              title="Low ASR, few false holds" sub={subtitle('F4 · false quarantine vs ASR')}
              legend={<ChartLegend items={[...defenceLegend(t), { key: 'sweep', label: 'Custos sweep', color: t['def-custos'], line: true }]} />} chart={<F4Chart rows={f4} />} table={<F4Table rows={f4} />} />
            <ChartCard sample={sample}
              title="Overhead per operation" sub={subtitle('F5 · latency p50 / p95, log scale')}
              legend={<ChartLegend items={[{ key: 'p50', label: 'p50', color: t['def-custos'] }, { key: 'p95', label: 'p95', color: t.t4 }]} />} chart={<F5Chart rows={f5} />} table={<F5Table rows={f5} />} />
          </div>
        </div>
      </div>
    </div>
  );
}

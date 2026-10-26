// Bench figures F1, F3, F4, F5 rendered from JSON rows (spec section 5.4). Thin marks, hairline grid,
// legend always present, hover tooltips with n and 95% Wilson CI, and a table twin for every chart.
import { useMemo } from 'react';
import {
  Area, Bar, BarChart, CartesianGrid, ComposedChart, ErrorBar, LabelList, Legend, Line, ReferenceDot, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis,
} from 'recharts';
import type { F1Row, F3Row, F4Row, F5Row, DefenceId } from '../../benchTypes';
import { DEFENCE_LABEL, DEFENCE_ORDER } from '../../benchTypes';
import { useTokens } from '../../lib/tokens';
import type { Tokens } from '../../lib/tokens';
import { useUi } from '../../state/ui';
import { cx, formatCount } from '../../lib/format';

const defColor = (t: Tokens, d: DefenceId): string => ({ undefended: t['def-undefended'], pi_filter: t['def-pi'], amemguard_style: t['def-amg'], custos: t['def-custos'] }[d]);

export function ChartLegend({ items }: { items: Array<{ key: string; label: string; color: string; line?: boolean }> }) {
  return (
    <ul className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm text-ink" aria-label="Legend">
      {items.map((i) => (
        <li key={i.key} className="flex items-center gap-2">
          <span aria-hidden className={i.line ? 'h-0.5 w-5 rounded' : 'h-3 w-3 rounded-[3px]'} style={{ background: i.color, height: i.line ? 3 : undefined }} />
          {i.label}
        </li>
      ))}
    </ul>
  );
}

export const defenceLegend = (t: Tokens, line = false) => DEFENCE_ORDER.map((d) => ({ key: d, label: DEFENCE_LABEL[d], color: defColor(t, d), line }));

const axisTick = (t: Tokens, size = 13) => ({ fill: t.muted, fontSize: size });

function TipBox({ title, rows, note }: { title: string; rows: Array<{ key: string; label: string; value: string; color?: string; sub?: string }>; note?: string }) {
  return (
    <div className="min-w-[13rem] rounded-lg border border-border bg-panel2 px-3 py-2 text-sm text-ink shadow-pop">
      <div className="mb-1 font-semibold text-muted">{title}</div>
      {rows.map((r) => (
        <div key={r.key} className="flex items-baseline gap-2 py-0.5">
          {r.color && <span aria-hidden className="inline-block h-0.5 w-3.5 self-center rounded" style={{ background: r.color, height: 3 }} />}
          <span className="text-lg font-bold leading-none">{r.value}</span>
          <span className="text-muted">{r.label}</span>
          {r.sub && <span className="ml-auto text-xs text-faint">{r.sub}</span>}
        </div>
      ))}
      {note && <div className="mt-1 text-xs text-faint">{note}</div>}
    </div>
  );
}

const pct1 = (v: number) => `${(Math.round(v * 10) / 10).toString()}%`;

// ---- F1 ----------------------------------------------------------------------------------------------

export function F1Chart({ rows }: { rows: F1Row[] }) {
  const t = useTokens();
  const { ui } = useUi();
  const data = useMemo(() => {
    const fam = new Map<string, Record<string, number | string>>();
    for (const r of rows) {
      if (!DEFENCE_ORDER.includes(r.defence)) continue;
      const row = fam.get(r.family) ?? { family: r.family, name: `${r.family}  ${r.family_name}` };
      row[r.defence] = r.rate_pct;
      row[`${r.defence}_err`] = [Math.max(0, r.rate_pct - r.ci_low_pct), Math.max(0, r.ci_high_pct - r.rate_pct)] as unknown as number;
      row[`${r.defence}_n`] = r.n;
      row[`${r.defence}_lo`] = r.ci_low_pct;
      row[`${r.defence}_hi`] = r.ci_high_pct;
      fam.set(r.family, row);
    }
    return [...fam.values()].sort((a, b) => Number(b.undefended ?? 0) - Number(a.undefended ?? 0));
  }, [rows]);
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 28, bottom: 4, left: 4 }} barCategoryGap="22%" barGap={2}>
        <CartesianGrid horizontal={false} stroke={t.grid} />
        <XAxis type="number" domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} tickFormatter={(v: number) => `${v}%`} tick={axisTick(t)} stroke={t.grid} tickLine={false} />
        <YAxis type="category" dataKey="name" width={218} tick={{ fill: t.text, fontSize: 14 }} stroke={t.grid} tickLine={false} interval={0} />
        <Tooltip
          cursor={{ fill: t.text, fillOpacity: 0.05 }}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const d = payload[0].payload as Record<string, number | string>;
            return <TipBox title={String(d.name)} rows={DEFENCE_ORDER.map((k) => ({ key: k, label: DEFENCE_LABEL[k], color: defColor(t, k), value: pct1(Number(d[k] ?? 0)), sub: `CI ${pct1(Number(d[`${k}_lo`] ?? 0))}–${pct1(Number(d[`${k}_hi`] ?? 0))} · n=${d[`${k}_n`]}` }))} note="ASR with 95% Wilson interval" />;
          }}
        />
        {DEFENCE_ORDER.map((d) => (
          <Bar key={d} dataKey={d} name={DEFENCE_LABEL[d]} fill={defColor(t, d)} barSize={9} radius={[0, 4, 4, 0]} isAnimationActive={!ui.motionReduced} animationDuration={1000} animationEasing="ease-out">
            <ErrorBar dataKey={`${d}_err`} direction="x" width={4} strokeWidth={1.3} stroke={t.muted} />
            {d === 'custos' && <LabelList dataKey="custos" position="right" formatter={(v: number) => `${Math.round(v)}%`} style={{ fill: t.text, fontSize: 12, fontWeight: 600 }} />}
          </Bar>
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

export function F1Table({ rows }: { rows: F1Row[] }) {
  return (
    <Tbl head={['Family', 'Defence', 'n', 'ASR', '95% CI']} rows={rows.map((r) => [`${r.family} ${r.family_name}`, DEFENCE_LABEL[r.defence] ?? r.defence, formatCount(r.n), pct1(r.rate_pct), `${pct1(r.ci_low_pct)} – ${pct1(r.ci_high_pct)}`])} />
  );
}

// ---- F3 ----------------------------------------------------------------------------------------------

export function F3Chart({ rows }: { rows: F3Row[] }) {
  const t = useTokens();
  const { ui } = useUi();
  const { data, last } = useMemo(() => {
    const byIter = new Map<number, Record<string, number | number[]>>();
    for (const r of rows) {
      if (!DEFENCE_ORDER.includes(r.defence)) continue;
      const row = byIter.get(r.iter) ?? { iter: r.iter };
      row[r.defence] = r.rate_pct;
      row[`${r.defence}_band`] = [r.ci_low_pct, r.ci_high_pct];
      row[`${r.defence}_n`] = r.n;
      byIter.set(r.iter, row);
    }
    const sorted = [...byIter.values()].sort((a, b) => Number(a.iter) - Number(b.iter));
    return { data: sorted, last: sorted.length ? Number(sorted[sorted.length - 1].iter) : 0 };
  }, [rows]);
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={data} margin={{ top: 10, right: 64, bottom: 22, left: 4 }}>
        <CartesianGrid vertical={false} stroke={t.grid} />
        <XAxis dataKey="iter" type="number" domain={[0, last]} ticks={data.map((d) => Number(d.iter))} tick={axisTick(t)} stroke={t.grid} tickLine={false} label={{ value: 'attacker rewrites →', position: 'insideBottom', offset: -10, fill: t.muted, fontSize: 13 }} />
        <YAxis domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} tickFormatter={(v: number) => `${v}%`} tick={axisTick(t)} stroke={t.grid} tickLine={false} width={46} />
        <Tooltip
          cursor={{ stroke: t.muted, strokeWidth: 1 }}
          content={({ active, payload, label }) => {
            if (!active || !payload?.length) return null;
            const d = payload[0].payload as Record<string, number | number[]>;
            return <TipBox title={`after ${label} rewrite${Number(label) === 1 ? '' : 's'}`} rows={[...DEFENCE_ORDER].reverse().map((k) => ({ key: k, label: DEFENCE_LABEL[k], color: defColor(t, k), value: pct1(Number(d[k] ?? 0)), sub: `CI ${(d[`${k}_band`] as number[] | undefined)?.map(pct1).join('–') ?? ''} · n=${d[`${k}_n`]}` }))} note="cumulative ASR, 95% Wilson interval" />;
          }}
        />
        {DEFENCE_ORDER.map((d) => (
          <Area key={`${d}b`} dataKey={`${d}_band`} stroke="none" fill={defColor(t, d)} fillOpacity={0.12} isAnimationActive={false} legendType="none" activeDot={false} />
        ))}
        {DEFENCE_ORDER.map((d) => (
          <Line
            key={d} dataKey={d} name={DEFENCE_LABEL[d]} stroke={defColor(t, d)} strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" type="monotone"
            dot={false} activeDot={{ r: 6, stroke: t.panel, strokeWidth: 2, fill: defColor(t, d) }} isAnimationActive={!ui.motionReduced} animationDuration={1400}
          >
            <LabelList
              dataKey={d}
              content={(p) => {
                const { x, y, index, value } = p as { x: number; y: number; index: number; value: number };
                if (index !== data.length - 1) return null;
                return (
                  <g>
                    <circle cx={x} cy={y} r={5} fill={defColor(t, d)} stroke={t.panel} strokeWidth={2} />
                    <text x={x + 11} y={y + 5} fill={t.text} fontSize={14} fontWeight={700}>{Math.round(value)}%</text>
                  </g>
                );
              }}
            />
          </Line>
        ))}
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function F3Table({ rows }: { rows: F3Row[] }) {
  return <Tbl head={['Defence', 'Rewrites', 'n', 'Cumulative ASR', '95% CI']} rows={rows.map((r) => [DEFENCE_LABEL[r.defence] ?? r.defence, String(r.iter), formatCount(r.n), pct1(r.rate_pct), `${pct1(r.ci_low_pct)} – ${pct1(r.ci_high_pct)}`])} />;
}

// ---- F4 ----------------------------------------------------------------------------------------------

export function F4Chart({ rows }: { rows: F4Row[] }) {
  const t = useTokens();
  const { ui } = useUi();
  const defs = rows.filter((r) => r.kind === 'defence');
  const sweep = rows.filter((r) => r.kind === 'custos_sweep').sort((a, b) => a.fqr_pct - b.fqr_pct);
  const op = rows.find((r) => r.operating);
  const xMax = Math.max(10, ...rows.map((r) => r.fqr_ci_high_pct)) + 2;
  const colorOf = (r: F4Row): string => (DEFENCE_ORDER as string[]).includes(r.id) ? defColor(t, r.id as DefenceId) : t['def-custos'];
  const point = (r: F4Row) => [{ ...r, xerr: [r.fqr_pct - r.fqr_ci_low_pct, r.fqr_ci_high_pct - r.fqr_pct], yerr: [r.asr_pct - r.asr_ci_low_pct, r.asr_ci_high_pct - r.asr_pct] }];
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ScatterChart margin={{ top: 14, right: 26, bottom: 26, left: 6 }}>
        <CartesianGrid stroke={t.grid} />
        <XAxis type="number" dataKey="fqr_pct" domain={[0, xMax]} tickFormatter={(v: number) => `${v}%`} tick={axisTick(t)} stroke={t.grid} tickLine={false} label={{ value: 'false quarantine on benign updates →', position: 'insideBottom', offset: -12, fill: t.muted, fontSize: 13 }} />
        <YAxis type="number" dataKey="asr_pct" domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} tickFormatter={(v: number) => `${v}%`} tick={axisTick(t)} stroke={t.grid} tickLine={false} width={46} />
        <Tooltip
          cursor={false}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const r = payload[0].payload as F4Row;
            return <TipBox title={r.label + (r.params ? ` · ${r.params}` : '')} rows={[{ key: 'asr', label: 'attack success', value: pct1(r.asr_pct), sub: `CI ${pct1(r.asr_ci_low_pct)}–${pct1(r.asr_ci_high_pct)}` }, { key: 'fqr', label: 'false quarantine', value: pct1(r.fqr_pct), sub: `CI ${pct1(r.fqr_ci_low_pct)}–${pct1(r.fqr_ci_high_pct)}` }]} />;
          }}
        />
        <Scatter name="Custos threshold sweep" data={sweep} line={{ stroke: t['def-custos'], strokeWidth: 2 }} lineType="joint" isAnimationActive={!ui.motionReduced} animationDuration={1000}
          shape={(p: unknown) => { const { cx: x, cy: y } = p as { cx: number; cy: number }; return <g><circle cx={x} cy={y} r={14} fill="transparent" /><circle cx={x} cy={y} r={4} fill={t['def-custos']} stroke={t.panel} strokeWidth={2} /></g>; }} />
        {defs.map((r) => (
          <Scatter key={r.id} name={r.label} data={point(r)} isAnimationActive={false}
            shape={(p: unknown) => { const { cx: x, cy: y } = p as { cx: number; cy: number }; return <g><circle cx={x} cy={y} r={16} fill="transparent" /><circle cx={x} cy={y} r={8} fill={colorOf(r)} stroke={t.panel} strokeWidth={2} /></g>; }}>
            <ErrorBar dataKey="xerr" direction="x" width={4} stroke={t.muted} strokeWidth={1.3} />
            <ErrorBar dataKey="yerr" direction="y" width={4} stroke={t.muted} strokeWidth={1.3} />
            <LabelList dataKey="label" content={(p) => { const { x, y, value } = p as { x: number; y: number; value: string }; return <text x={x + 14} y={y + 5} fill={t.text} fontSize={13} fontWeight={600} style={{ whiteSpace: 'nowrap' }}>{String(value)}</text>; }} />
          </Scatter>
        ))}
        {op && <ReferenceDot x={op.fqr_pct} y={op.asr_pct} r={15} fill="none" stroke={t.text} strokeWidth={2} ifOverflow="visible" />}
      </ScatterChart>
    </ResponsiveContainer>
  );
}

export function F4Table({ rows }: { rows: F4Row[] }) {
  return <Tbl head={['Point', 'FQR', 'FQR 95% CI', 'ASR', 'ASR 95% CI']} rows={rows.map((r) => [r.label + (r.operating ? ' (operating point)' : ''), pct1(r.fqr_pct), `${pct1(r.fqr_ci_low_pct)} – ${pct1(r.fqr_ci_high_pct)}`, pct1(r.asr_pct), `${pct1(r.asr_ci_low_pct)} – ${pct1(r.asr_ci_high_pct)}`])} />;
}

// ---- F5 ----------------------------------------------------------------------------------------------

/** Category tick that wraps onto up to three short lines instead of overlapping its neighbour. */
function WrapTick(props: { x?: number; y?: number; payload?: { value: string }; fill: string }) {
  const { x = 0, y = 0, payload, fill } = props;
  const words = String(payload?.value ?? '').split(' ');
  const lines: string[] = [];
  for (const w of words) {
    const last = lines[lines.length - 1];
    if (last !== undefined && (last + ' ' + w).length <= 13) lines[lines.length - 1] = last + ' ' + w;
    else lines.push(w);
  }
  return (
    <text x={x} y={y + 14} textAnchor="middle" fill={fill} fontSize={13}>
      {lines.slice(0, 3).map((l, i) => <tspan key={i} x={x} dy={i === 0 ? 0 : 15}>{l}</tspan>)}
    </text>
  );
}

const fmtMs = (v: number) => (v >= 1000 ? `${+(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`);

export function F5Chart({ rows }: { rows: F5Row[] }) {
  const t = useTokens();
  const { ui } = useUi();
  const data = rows.map((r) => ({ ...r, name: r.label ?? r.op }));
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 22, right: 66, bottom: 4, left: 4 }} barCategoryGap="28%" barGap={3}>
        <CartesianGrid vertical={false} stroke={t.grid} />
        <XAxis dataKey="name" tick={<WrapTick fill={t.text} />} stroke={t.grid} tickLine={false} interval={0} height={58} />
        <YAxis scale="log" domain={[1, 10000]} ticks={[1, 10, 100, 1000, 10000]} allowDataOverflow tickFormatter={fmtMs} tick={axisTick(t)} stroke={t.grid} tickLine={false} width={56} />
        <Tooltip
          cursor={{ fill: t.text, fillOpacity: 0.05 }}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const r = payload[0].payload as F5Row & { name: string };
            return <TipBox title={r.name} rows={[{ key: 'p50', label: 'p50', value: fmtMs(r.p50_ms), color: t['def-custos'] }, { key: 'p95', label: 'p95', value: fmtMs(r.p95_ms), color: t.t4 }]} note={`${r.n !== undefined ? `n=${formatCount(r.n)} · ` : ''}${r.on_critical_path === false ? 'off the critical path' : 'on the critical path'}`} />;
          }}
        />
        <Legend content={() => null} />
        <ReferenceLine y={40} stroke={t.warn} strokeDasharray="6 4" strokeWidth={1.5} label={{ value: '40 ms', position: 'right', fill: t.text, fontSize: 12 }} />
        <ReferenceLine y={1500} stroke={t.warn} strokeDasharray="6 4" strokeWidth={1.5} label={{ value: '1.5 s', position: 'right', fill: t.text, fontSize: 12 }} />
        <Bar dataKey="p50_ms" name="p50" fill={t['def-custos']} barSize={18} radius={[4, 4, 0, 0]} isAnimationActive={!ui.motionReduced} animationDuration={900}>
          <LabelList dataKey="p50_ms" position="top" formatter={fmtMs} style={{ fill: t.text, fontSize: 12, fontWeight: 600 }} />
        </Bar>
        <Bar dataKey="p95_ms" name="p95" fill={t.t4} barSize={18} radius={[4, 4, 0, 0]} isAnimationActive={!ui.motionReduced} animationDuration={900}>
          <LabelList dataKey="p95_ms" position="top" formatter={fmtMs} style={{ fill: t.text, fontSize: 12, fontWeight: 600 }} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function F5Table({ rows }: { rows: F5Row[] }) {
  return <Tbl head={['Operation', 'p50', 'p95', 'n', 'Critical path']} rows={rows.map((r) => [r.label ?? r.op, fmtMs(r.p50_ms), fmtMs(r.p95_ms), r.n !== undefined ? formatCount(r.n) : '—', r.on_critical_path === false ? 'no (async)' : 'yes'])} />;
}

// ---- shared table twin ---------------------------------------------------------------------------------

export function Tbl({ head, rows }: { head: string[]; rows: string[][] }) {
  return (
    <div className="h-full overflow-auto">
      <table className="w-full border-separate border-spacing-0 text-left text-sm">
        <thead className="sticky top-0 bg-panel text-xs font-semibold uppercase tracking-wide text-muted"><tr>{head.map((h) => <th key={h} className="px-3 py-1.5">{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className={cx('border-b border-border/50', i % 2 === 1 && 'bg-panel2/40')}>
              {r.map((c, j) => <td key={j} className={cx('px-3 py-1.5', j === 0 ? 'text-ink' : 'tabular-nums text-ink')}>{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Shared helpers for the fixture generator. Erasable TypeScript only (Node runs this directly).

/** Fixture wall clock: the "recording" starts here (IST). */
export const RUN_START_MS = Date.parse('2026-10-26T16:30:00+05:30');
const IST_OFFSET_MS = 5.5 * 3600 * 1000;

/** ISO string with an explicit +05:30 offset, e.g. 2026-10-26T16:30:05.120+05:30 */
export function istIso(ms: number): string {
  return new Date(ms + IST_OFFSET_MS).toISOString().replace('Z', '+05:30');
}

/** Wilson score interval, z = 1.96. Returns percentages. */
export function wilsonPct(k: number, n: number): { low: number; high: number } {
  if (n === 0) return { low: 0, high: 0 };
  const z = 1.96;
  const p = k / n;
  const denom = 1 + (z * z) / n;
  const centre = (p + (z * z) / (2 * n)) / denom;
  const half = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / denom;
  return { low: Math.max(0, (centre - half) * 100), high: Math.min(100, (centre + half) * 100) };
}

export const round1 = (x: number): number => Math.round(x * 10) / 10;
export const round2 = (x: number): number => Math.round(x * 100) / 100;

export function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error('fixture assertion failed: ' + msg);
}

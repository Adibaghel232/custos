// Formatting helpers. Indian context: rupees use lakh/crore digit grouping (Rs 40,00,000).

const inr = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 });
const grouped = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });

export const formatINR = (amount: number): string => inr.format(amount);
export const formatCount = (n: number): string => grouped.format(n);

/** Compact en-IN style for KPI use: 1.2 L (lakh), 3.4 Cr (crore). */
export function formatINRCompact(amount: number): string {
  const abs = Math.abs(amount);
  if (abs >= 1e7) return `₹${(amount / 1e7).toFixed(2).replace(/\.?0+$/, '')} Cr`;
  if (abs >= 1e5) return `₹${(amount / 1e5).toFixed(2).replace(/\.?0+$/, '')} L`;
  return formatINR(amount);
}

/** 9f2c1ab4...e1 -> 9f2c…e1 */
export function shortHash(h: string | undefined | null, head = 4, tail = 2): string {
  if (!h) return '—';
  if (h.length <= head + tail + 1) return h;
  return `${h.slice(0, head)}…${h.slice(-tail)}`;
}

export function formatMs(ms: number | undefined): string {
  if (ms === undefined) return '';
  if (ms >= 1000) return `${(ms / 1000).toFixed(ms >= 10000 ? 0 : 1)} s`;
  return `${Math.round(ms)} ms`;
}

const istDate = new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
const istTime = new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

/** "26 Oct, 16:30" in IST. */
export function formatISTDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return istDate.format(d).replace(',', '');
}

export function formatISTTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return istTime.format(d);
}

export const pct = (v: number, digits = 1): string => `${v.toFixed(digits).replace(/\.0+$/, '')}%`;

export function plural(n: number, one: string, many = one + 's'): string {
  return `${n} ${n === 1 ? one : many}`;
}

export const cx = (...parts: Array<string | false | null | undefined>): string => parts.filter(Boolean).join(' ');

/** ISO timestamp in IST (+05:30) for a UTC epoch in ms, e.g. 2026-10-26T16:30:05.120+05:30 */
export function isoIST(ms: number): string {
  return new Date(ms + 5.5 * 3600 * 1000).toISOString().replace('Z', '+05:30');
}

// Resolve the CSS design tokens to concrete colour strings for SVG-based libraries (recharts, React Flow).
import { useEffect, useState } from 'react';
import type { Tier } from '../types';

const NAMES = [
  'bg', 'panel', 'panel-2', 'border', 'text', 'muted', 'faint', 't0', 't1', 't2', 't3', 't4', 'on-fill', 'danger', 'danger-ink', 'ok', 'warn', 'info',
  'reverted', 'reverted-ink', 'def-undefended', 'def-pi', 'def-amg', 'def-custos', 'grid',
] as const;

export type TokenName = (typeof NAMES)[number];
export type Tokens = Record<TokenName, string>;

export function readTokens(): Tokens {
  const cs = getComputedStyle(document.documentElement);
  const out = {} as Tokens;
  for (const n of NAMES) {
    const raw = cs.getPropertyValue(`--${n}`).trim();
    out[n] = raw ? `rgb(${raw})` : '#888';
  }
  return out;
}

/** Re-reads whenever the theme attribute changes. */
export function useTokens(): Tokens {
  const [tokens, setTokens] = useState<Tokens>(() => readTokens());
  useEffect(() => {
    const update = () => setTokens(readTokens());
    update();
    const mo = new MutationObserver(update);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => mo.disconnect();
  }, []);
  return tokens;
}

export const tierColor = (t: Tokens, tier: Tier | null): string => (tier ? t[tier.toLowerCase() as 't0' | 't1' | 't2' | 't3' | 't4'] : t.faint);

/** Same colour with alpha, from an `rgb(r g b)` token string. */
export function withAlpha(rgb: string, alpha: number): string {
  return rgb.startsWith('rgb(') && !rgb.includes('/') ? rgb.replace(')', ` / ${alpha})`) : rgb;
}

import type { RecordStatus, Tier } from '../types';

export const TIERS: Tier[] = ['T0', 'T1', 'T2', 'T3', 'T4'];

export const tierRank = (t: Tier | null | undefined): number => (t ? Number(t.slice(1)) : -1);

export interface TierMeta {
  tier: Tier;
  name: string;
  /** One line: where this tier comes from. */
  desc: string;
  examples: string;
}

export const TIER_META: Record<Tier, TierMeta> = {
  T0: { tier: 'T0', name: 'Untrusted', desc: 'Outside content: email, web, files, external peer agents.', examples: 'inbound email, web page, uploaded file' },
  T1: { tier: 'T1', name: 'Internal tool', desc: 'Internal tools, wiki / KB, authenticated internal peer agents.', examples: 'internal wiki, tool output' },
  T2: { tier: 'T2', name: 'User statement', desc: 'Authenticated user statements about their own data.', examples: 'user preference, instruction to own agent' },
  T3: { tier: 'T3', name: 'System of record', desc: 'ERP, CRM, order DB, HRIS, or a user statement with explicit confirmation.', examples: 'ERP vendor master, CRM account' },
  T4: { tier: 'T4', name: 'Operator policy', desc: 'System or operator policy, admin-signed configuration.', examples: 'signed policy, admin config' },
};

export const tierName = (t: Tier | null | undefined): string => (t ? TIER_META[t].name : 'No custody');

export const minTier = (tiers: Array<Tier | null | undefined>): Tier | null => {
  let best: Tier | null = null;
  for (const t of tiers) {
    if (!t) continue;
    if (best === null || tierRank(t) < tierRank(best)) best = t;
  }
  return best;
};

export interface StatusMeta {
  status: RecordStatus;
  label: string;
  /** Short word shown beside the icon so status never relies on colour alone. */
  short: string;
}

export const STATUS_META: Record<RecordStatus, StatusMeta> = {
  pending: { status: 'pending', label: 'Pending checks', short: 'PENDING' },
  committed: { status: 'committed', label: 'Committed', short: 'COMMITTED' },
  quarantined: { status: 'quarantined', label: 'Quarantined (held)', short: 'HELD' },
  rejected: { status: 'rejected', label: 'Rejected', short: 'REJECTED' },
  superseded: { status: 'superseded', label: 'Superseded', short: 'SUPERSEDED' },
  reverted: { status: 'reverted', label: 'Reverted', short: 'REVERTED' },
  erased: { status: 'erased', label: 'Erased', short: 'ERASED' },
};

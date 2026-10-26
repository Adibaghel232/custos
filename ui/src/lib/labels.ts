// Human-readable labels derived from trace data (kept out of components so views stay consistent).
import type { MemoryRecord } from '../types';
import type { ToolCallState } from '../state/replay';

export const TOOLS: Array<{ name: string; short: string; risk: 'low' | 'high'; note: string }> = [
  { name: 'erp_lookup_vendor', short: 'erp', risk: 'low', note: 'T3 system of record' },
  { name: 'read_inbox', short: 'inbox', risk: 'low', note: 'returns T0 outside content' },
  { name: 'memory_search', short: 'memory', risk: 'low', note: 'reads the memory store' },
  { name: 'pay_invoice', short: 'pay', risk: 'high', note: 'HIGH-RISK: moves money (simulated payment API)' },
  { name: 'send_email', short: 'email', risk: 'high', note: 'HIGH-RISK: sends mail' },
];

export const SOURCE_LABEL: Record<MemoryRecord['source']['type'], string> = {
  email: 'email', web: 'web', file: 'file', peer_agent: 'peer agent', user: 'user', erp: 'ERP', crm: 'CRM', tool: 'tool', system: 'system', derived: 'derived',
};

/** Compact "Acme.account = ****4410" style label for a memory row. */
export function memoryLabel(r: Pick<MemoryRecord, 'text' | 'kind' | 'claims' | 'status'>): string {
  if (r.kind === 'summary') return r.text.length > 74 ? r.text.slice(0, 73) + '…' : r.text;
  const c = r.claims?.[0];
  if (c) return `${c.s}.${c.r} = ${c.o}`;
  return r.text;
}

export function formatCall(c: Pick<ToolCallState, 'tool' | 'args'>): string {
  if (c.tool === 'pay_invoice') {
    const { payee, account, amount } = c.args;
    return `${c.tool}(${[payee, account, amount].filter(Boolean).join(', ')})`;
  }
  const vals = Object.values(c.args);
  return `${c.tool}(${vals.join(', ')})`;
}

export function sourceFromLabel(r: Pick<MemoryRecord, 'source' | 'principal'>): string {
  if (r.source.type === 'email') return r.source.from ?? 'email';
  if (r.source.type === 'erp') return 'ERP';
  return SOURCE_LABEL[r.source.type];
}

export function displayText(r: Pick<MemoryRecord, 'text' | 'status'>): string {
  return r.status === 'erased' ? '[content erased · hash retained in the ledger]' : r.text;
}

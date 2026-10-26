// Hash-chained ledger helpers shared by the UI (fixture mode) and the fixture generator.
// Chain rule (documented for the backend team, see report):
//   hash = sha256( prev_hash | seq | ts | event | record_id ('' if none) | payload_hash )
// The first entry's prev_hash is 64 zeros. `sig_ok` is reported by the gateway (Ed25519 in the
// real system); the browser never verifies signatures, it only displays the flag.
import type { LedgerEntry } from '../types';
import { canonicalJson, sha256Hex } from './sha256.ts';

export const GENESIS_HASH = '0'.repeat(64);

export interface VerifyResult {
  ok: boolean;
  entries: number;
  first_bad_seq: number | null;
  ms: number;
}

export interface TamperResult extends VerifyResult {
  tampered_seq: number;
  tampered_field: 'payload_hash';
  /** The edited COPY of the ledger (the real one is never touched). */
  copy: LedgerEntry[];
}

export type LedgerEventName = LedgerEntry['event'];

export function entryHash(e: Pick<LedgerEntry, 'prev_hash' | 'seq' | 'ts' | 'event' | 'record_id' | 'payload_hash'>): string {
  return sha256Hex(`${e.prev_hash}|${e.seq}|${e.ts}|${e.event}|${e.record_id ?? ''}|${e.payload_hash}`);
}

export function makeEntry(
  prev: LedgerEntry | undefined,
  input: { seq?: number; ts: string; event: LedgerEventName; record_id?: string; actor: string; payload: unknown },
): LedgerEntry {
  const seq = input.seq ?? (prev ? prev.seq + 1 : 1);
  const base = {
    seq,
    ts: input.ts,
    event: input.event,
    record_id: input.record_id,
    actor: input.actor,
    payload_hash: sha256Hex(canonicalJson(input.payload)),
    prev_hash: prev ? prev.hash : GENESIS_HASH,
  };
  const entry: LedgerEntry = { ...base, hash: entryHash(base), sig_ok: true };
  if (entry.record_id === undefined) delete entry.record_id;
  return entry;
}

/** Re-computes every hash and link. Returns the first bad seq (or null). */
export function verifyChain(entries: LedgerEntry[], now: () => number = () => Date.now()): VerifyResult {
  const t0 = now();
  let bad: number | null = null;
  for (let i = 0; i < entries.length && bad === null; i++) {
    const e = entries[i];
    const expectedPrev = i === 0 ? (e.seq === 1 ? GENESIS_HASH : e.prev_hash) : entries[i - 1].hash;
    if (e.prev_hash !== expectedPrev) bad = e.seq;
    else if (e.hash !== entryHash(e)) bad = e.seq;
    else if (!e.sig_ok) bad = e.seq;
    else if (i > 0 && e.seq !== entries[i - 1].seq + 1) bad = e.seq;
  }
  return { ok: bad === null, entries: entries.length, first_bad_seq: bad, ms: Math.max(1, Math.round(now() - t0)) };
}

/** Edits one entry in a COPY (never in place) and re-verifies, so detection can be shown on screen. */
export function tamperDemo(entries: LedgerEntry[], targetSeq?: number): TamperResult {
  const copy = entries.map((e) => ({ ...e }));
  // default target: the most recent DECIDE entry, i.e. "someone edited a verdict after the fact"
  let idx = targetSeq !== undefined ? copy.findIndex((e) => e.seq === targetSeq) : -1;
  if (idx < 0) {
    for (let i = copy.length - 1; i >= 0; i--) {
      if (copy[i].event === 'DECIDE') { idx = i; break; }
    }
  }
  if (idx < 0) idx = Math.max(0, Math.floor(copy.length / 2));
  const victim = copy[idx];
  victim.payload_hash = sha256Hex('tampered:' + victim.payload_hash);
  victim.sig_ok = false;
  const res = verifyChain(copy);
  return { ...res, tampered_seq: victim.seq, tampered_field: 'payload_hash', copy };
}

// Small synchronous SHA-256 (FIPS 180-4). Used for the ledger hash chain so the same code
// runs in the browser (fixture-mode "Verify chain" / tamper demo, no secure context needed)
// and in the Node fixture generator. Erasable-TypeScript only (no enums), so Node can run it directly.

const K = new Uint32Array(64);
const H0 = new Uint32Array(8);

(function init() {
  // Constants: first 32 bits of the fractional parts of the cube roots (K) and square roots (H0)
  // of the first 64 / 8 primes.
  const frac = (x: number) => Math.floor((x - Math.floor(x)) * 4294967296) >>> 0;
  let n = 0;
  for (let candidate = 2; n < 64; candidate++) {
    let prime = true;
    for (let d = 2; d * d <= candidate; d++) {
      if (candidate % d === 0) { prime = false; break; }
    }
    if (!prime) continue;
    if (n < 8) H0[n] = frac(Math.sqrt(candidate));
    K[n] = frac(Math.cbrt(candidate));
    n++;
  }
})();

const rotr = (x: number, n: number) => (x >>> n) | (x << (32 - n));

export function sha256Bytes(data: Uint8Array): Uint8Array {
  const bitLen = data.length * 8;
  const padded = new Uint8Array((((data.length + 9 + 63) >> 6) << 6));
  padded.set(data);
  padded[data.length] = 0x80;
  const view = new DataView(padded.buffer);
  view.setUint32(padded.length - 8, Math.floor(bitLen / 4294967296), false);
  view.setUint32(padded.length - 4, bitLen >>> 0, false);

  const h = new Uint32Array(H0);
  const w = new Uint32Array(64);
  for (let off = 0; off < padded.length; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(off + i * 4, false);
    for (let i = 16; i < 64; i++) {
      const w15 = w[i - 15], w2 = w[i - 2];
      const s0 = rotr(w15, 7) ^ rotr(w15, 18) ^ (w15 >>> 3);
      const s1 = rotr(w2, 17) ^ rotr(w2, 19) ^ (w2 >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }
    let a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
    for (let i = 0; i < 64; i++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (hh + S1 + ch + K[i] + w[i]) >>> 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) >>> 0;
      hh = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
    }
    h[0] = (h[0] + a) >>> 0; h[1] = (h[1] + b) >>> 0; h[2] = (h[2] + c) >>> 0; h[3] = (h[3] + d) >>> 0;
    h[4] = (h[4] + e) >>> 0; h[5] = (h[5] + f) >>> 0; h[6] = (h[6] + g) >>> 0; h[7] = (h[7] + hh) >>> 0;
  }
  const out = new Uint8Array(32);
  const ov = new DataView(out.buffer);
  for (let i = 0; i < 8; i++) ov.setUint32(i * 4, h[i], false);
  return out;
}

export function sha256Hex(input: string | Uint8Array): string {
  const bytes = typeof input === 'string' ? new TextEncoder().encode(input) : input;
  const digest = sha256Bytes(bytes);
  let s = '';
  for (const b of digest) s += b.toString(16).padStart(2, '0');
  return s;
}

/** Stable JSON: object keys sorted, so equal payloads always hash equally. */
export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null';
  if (Array.isArray(value)) return '[' + value.map(canonicalJson).join(',') + ']';
  const obj = value as Record<string, unknown>;
  const keys = Object.keys(obj).filter((k) => obj[k] !== undefined).sort();
  return '{' + keys.map((k) => JSON.stringify(k) + ':' + canonicalJson(obj[k])).join(',') + '}';
}

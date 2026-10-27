// Records the demo video frames: drives the console (replay of the recorded live run) with Playwright, captures it with the
// DevTools screencast (JPEG frames with timestamps, 1920x1000 + a caption band added later), shot by shot.
//
//   node scripts/video/record.mjs [--only S3,S4] [--out deliverables/video/work/frames]
//
// Needs: both replay servers up (scripts/video/servers.ps1), work/audio_manifest.json (audio_manifest.py), tools/pw (npm i playwright).
// Setup between shots (navigating, jumping the replay cursor) is NOT captured: those are the cuts.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const require = createRequire(path.join(ROOT, 'tools', 'pw', 'package.json'));
const { chromium } = require('playwright');

const args = process.argv.slice(2);
const arg = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const WORK = path.join(ROOT, 'deliverables', 'video', 'work');
const OUT = path.resolve(arg('--out', path.join(WORK, 'frames')));
const ONLY = arg('--only', '') ? arg('--only', '').split(',') : null;
// shots that share page state are re-recorded together
const GROUPS = [['S0'], ['S1'], ['S2'], ['S3'], ['S4', 'S5', 'S6'], ['S7', 'S8', 'S9', 'S10', 'S11'], ['S12']];
const WANTED = ONLY ? new Set(GROUPS.filter((g) => g.some((x) => ONLY.includes(x))).flat()) : null;
const URL2 = 'http://127.0.0.1:8080/';
const URL1 = 'http://127.0.0.1:8081/';
const W = 1920, H = 1000;
const manifest = JSON.parse(fs.readFileSync(path.join(WORK, 'audio_manifest.json'), 'utf8'));
const log = (...a) => console.log('[record]', ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------------------------------------ page-level patches
const INIT = `
(() => {
  // 1. a nearly invisible animated pixel, so the compositor keeps producing frames during static holds
  const tick = () => { const d = document.createElement('div'); d.id='__tick';
    d.style.cssText='position:fixed;right:0;bottom:0;width:2px;height:2px;z-index:2147483647;pointer-events:none;animation:__tk .12s steps(2) infinite';
    const st = document.createElement('style'); st.textContent='@keyframes __tk{0%{background:#0B1426}100%{background:#0B1427}}';
    document.documentElement.append(st, d); };
  // 2. visible pointer: a ring that follows the mouse and pulses on click (the Playwright cursor is not rendered)
  const cursor = () => { const c = document.createElement('div'); c.id='__cur';
    c.style.cssText='position:fixed;left:-50px;top:-50px;width:34px;height:34px;margin:-17px 0 0 -17px;border-radius:50%;border:3px solid #F5A623;background:rgba(245,166,35,.18);z-index:2147483646;pointer-events:none;transition:transform .12s, background .12s';
    document.documentElement.append(c);
    c.style.opacity = '0'; c.style.transition += ', opacity .5s'; let hide;
    const show = () => { c.style.opacity = '1'; clearTimeout(hide); hide = setTimeout(() => { c.style.opacity = '0'; }, 1600); };
    addEventListener('mousemove', (e) => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; show(); }, true);
    addEventListener('mousedown', () => { c.style.transform='scale(1.5)'; c.style.background='rgba(245,166,35,.5)'; show(); }, true);
    addEventListener('mouseup', () => { c.style.transform='scale(1)'; c.style.background='rgba(245,166,35,.18)'; show(); }, true); };
  // 3. the console hard-codes "Mem0 + Custos Gateway"; this run used the Custos native store, so say that
  const fix = () => { const w = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
    let n; while ((n = w.nextNode())) { if (n.nodeValue && n.nodeValue.includes('Mem0 + Custos Gateway')) n.nodeValue = n.nodeValue.replace('Mem0 + Custos Gateway', 'Custos store + gateway');
      // the Custos agent never called pay_invoice in session 9 of the recorded run; "blocked" would overstate what happened
      if (n.nodeValue && n.nodeValue.includes('Payment blocked · 0 payments executed')) n.nodeValue = n.nodeValue.replace('Payment blocked', 'No payment made'); } };
  // 4. the rollback list was laid out for short ids (m_17); the real record ids are 30 characters, so widen that column
  const css = () => { const st = document.createElement('style'); st.textContent = 'section[aria-label="Rollback"] ol > li { grid-template-columns: 3.6rem 19.5rem minmax(0,1fr) auto !important; }'; document.documentElement.append(st); };
  const boot = () => { css(); tick(); cursor(); new MutationObserver(fix).observe(document.documentElement, { childList: true, subtree: true, characterData: true }); fix(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();`;

// ------------------------------------------------------------------------------------------------ capture
class Capture {
  constructor(cdp) { this.cdp = cdp; this.on = false; this.frames = []; this.dir = null; this.n = 0; this.t0 = 0; }
  async init() {
    this.cdp.on('Page.screencastFrame', async (ev) => {
      try { await this.cdp.send('Page.screencastFrameAck', { sessionId: ev.sessionId }); } catch { /* page closing */ }
      if (!this.on) return;
      const ts = ev.metadata.timestamp;
      const file = path.join(this.dir, `f${String(this.n++).padStart(6, '0')}.jpg`);
      fs.writeFileSync(file, Buffer.from(ev.data, 'base64'));
      this.frames.push({ ts, file: path.basename(file) });
      if (this.n === 1) { const b = Buffer.from(ev.data, 'base64'); let i = 2; while (i < b.length) { if (b[i] === 0xff && (b[i + 1] === 0xc0 || b[i + 1] === 0xc2)) { const h = b.readUInt16BE(i + 5), w = b.readUInt16BE(i + 7); if (w !== W || h !== H) console.log('[record] WARNING first frame is', w, 'x', h); break; } i += 2 + b.readUInt16BE(i + 2); } }
    });
    await this.cdp.send('Page.startScreencast', { format: 'jpeg', quality: 82, maxWidth: W, maxHeight: H, everyNthFrame: 2 });
  }
  begin(id) {
    this.dir = path.join(OUT, id);
    fs.rmSync(this.dir, { recursive: true, force: true });
    fs.mkdirSync(this.dir, { recursive: true });
    this.frames = []; this.n = 0; this.on = true; this.t0 = Date.now() / 1000;
  }
  async end() {
    await sleep(250);                      // let the last frames land
    this.on = false;
    return { frames: this.frames, t_end: Date.now() / 1000 - 0.25, t_begin: this.t0 };
  }
}

// ------------------------------------------------------------------------------------------------ helpers
const playLabel = (page) => page.evaluate(() => { const b = [...document.querySelectorAll('button')].find((x) => /^(Play|Pause)/.test(x.innerText.trim())); return b ? b.innerText.trim() : ''; });
async function waitPaused(page, maxMs = 90000) { const t0 = Date.now(); while (Date.now() - t0 < maxMs) { if (/^Play/.test(await playLabel(page))) return true; await sleep(120); } return false; }
const blur = (page) => page.evaluate(() => document.activeElement && document.activeElement.blur());
async function glide(page, loc) {
  await loc.scrollIntoViewIfNeeded();
  await sleep(250);
  const box = await loc.boundingBox();
  if (!box) throw new Error('no box for ' + loc);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 16 });
  await sleep(180);
  await page.mouse.down(); await sleep(70); await page.mouse.up();
  await sleep(120);
  await blur(page);
}
const chip = async (page, n, glideIt = false) => {
  const loc = page.locator(`button[title^="S${n} "]`).first();
  if (glideIt) await glide(page, loc); else { await loc.click(); await blur(page); }
};
const zoom = (page, s, ox, oy, ms = 1800) => page.evaluate(([s, ox, oy, ms]) => { const r = document.getElementById('root'); if (!r) return; r.style.transition = `transform ${ms}ms ease-in-out`; r.style.transformOrigin = `${ox}px ${oy}px`; r.style.transform = s === 1 ? '' : `scale(${s})`; }, [s, ox, oy, ms]);
const press = (page, k) => page.keyboard.press(k);
async function gotoApp(page, base, route) {
  await page.goto(base + route, { waitUntil: 'load' });
  await page.waitForSelector('button[title^="S1 "]', { timeout: 20000 });
  await sleep(1200);
  await page.evaluate(() => window.scrollTo(0, 0));
  await blur(page);
}
async function loadTrace(base, id) {
  const list = await (await fetch(base + 'api/traces')).json();
  return await (await fetch(base + 'api/traces/' + list[0].trace_id)).json();
}
function startedIndex(events, n) {
  let i = events.findIndex((e) => e.session === n);
  while (i + 1 < events.length && events[i + 1].type === 'session_start' && events[i + 1].session === n) i++;
  return i;
}
const clockFor = (m) => { const t0 = Date.now(); return { t0, now: () => (Date.now() - t0) / 1000, until: async (s) => { const d = t0 + s * 1000 - Date.now(); if (d > 0) await sleep(d); } }; };

// ------------------------------------------------------------------------------------------------ main
fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--force-device-scale-factor=1', '--hide-scrollbars', '--disable-features=Translate'] });
const ctx = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1, colorScheme: 'dark' });
await ctx.addInitScript(INIT);
const page = await ctx.newPage();
page.on('pageerror', (e) => log('pageerror', e.message));
const cdp = await ctx.newCDPSession(page);
const cap = new Capture(cdp);
await cap.init();
const timeline = [];
const want = (id) => !WANTED || WANTED.has(id);

async function shot(id, fn) {
  const m = manifest.shots.find((s) => s.id === id);
  if (!want(id)) return;
  log('shot', id, m.title, `min ${m.min_s.toFixed(1)}s`);
  cap.begin(id);
  const T = clockFor(m);
  try { await fn(T, m); } catch (e) { log('ERROR in', id, e.message); }
  await T.until(m.min_s);
  const r = await cap.end();
  timeline.push({ id, dur_wall: r.t_end - r.t_begin, frames: r.frames, t_begin: r.t_begin, t_end: r.t_end });
  log('  captured', r.frames.length, 'frames', (r.t_end - r.t_begin).toFixed(1) + 's');
}
const cardUrl = (name, q = '') => pathToFileURL(path.join(HERE, 'cards', name)).href + q;
const S = (id) => manifest.shots.find((s) => s.id === id);

// ---- S0 title ----
if (want('S0')) { await page.goto(cardUrl('title.html')); await sleep(300); }
await shot('S0', async (T) => { await T.until(S('S0').min_s); });

// ---- S1 problem ----
if (want('S1')) { await page.goto(cardUrl('problem.html', `?d=${(S('S1').min_s - 4).toFixed(1)}`)); }
await shot('S1', async (T) => { await T.until(S('S1').min_s); });

// ---- Act 2 arena: S2 ----
const tr2 = await loadTrace(URL2);
const ev2 = tr2.events;
const beatIdx = ev2.findIndex((e) => e.type === 'beat' && e.payload.pause);
const gate3 = ev2.findIndex((e) => e.type === 'gate' && e.payload.decision !== 'allow' && /billing@acme-payments/.test(JSON.stringify(e.payload.args[0].value)));
const poisonRec = ev2.find((e) => e.type === 'memory_write' && e.side === 'custos' && e.payload.decision?.rule === 'R5_LOWER_TIER_CONFLICT' && e.payload.claims?.[0]?.r === 'bank_account').payload.id;
log('act2: beat at', beatIdx, 'gate3 at', gate3, 'poisoned claim', poisonRec);

if (want('S2')) { await gotoApp(page, URL2, '#/arena?act=2'); await press(page, '0'); await press(page, '1'); }
await shot('S2', async (T) => {
  await T.until(S('S2').sentences[1].start - 0.3);
  await zoom(page, 1.9, 0, 200, 2200);                   // left pane header: model, store, tools
  await T.until(S('S2').sentences[2].start - 0.3);
  await zoom(page, 1.9, 1920, 200, 2200);               // right pane header: the same model and tools
  await T.until(S('S2').min_s - 2.2);
  await zoom(page, 1, 960, 200, 1500);
});

// ---- S3 obvious attack (Act 1, its own server) ----
if (want('S3')) { await gotoApp(page, URL1, '#/arena?act=1'); await press(page, '1'); await chip(page, 3); await sleep(500); }
await shot('S3', async (T) => {
  await T.until(S('S3').sentences[1].start - 0.2);      // "An email says ..."
  await press(page, 'Space');                            // play session 3 at 1x; the replay pauses itself at the verdict beat
  const ok = await waitPaused(page, 40000);
  log('  S3 paused at beat:', ok, 'at', T.now().toFixed(1) + 's');
  await T.until(S('S3').sentences[4].start);             // "The claim contradicts the ERP record"
  await zoom(page, 1.5, 1440, 560, 2000);
  await T.until(S('S3').sentences[6].start - 0.3);
  await zoom(page, 1, 1440, 560, 1500);
});

// ---- S4 smarter attack (Act 2) ----
if (want('S4')) { await gotoApp(page, URL2, '#/arena?act=2'); await press(page, '1'); await chip(page, 3); await sleep(500); }
await shot('S4', async (T) => {
  await T.until(S('S4').sentences[0].start + 0.5);
  await press(page, 'Space');
  const ok = await waitPaused(page, 40000);
  log('  S4 paused at beat:', ok, 'at', T.now().toFixed(1) + 's');
  await T.until(S('S4').sentences[2].start);
  await zoom(page, 1.5, 1440, 560, 2000);
  await T.until(S('S4').min_s - 1.8);
  await zoom(page, 1, 1440, 560, 1200);
});

// ---- S5 days pass: scrub S4..S8 ----
await shot('S5', async (T) => {
  for (const n of [4, 5, 6, 7, 8]) { await chip(page, n); await sleep(850); }
});

// ---- S6 session nine ----
await shot('S6', async (T) => {
  await chip(page, 9);
  await press(page, '2');                                // 2x: both agents finish session 9 before the narrator says "pays nobody"
  await T.until(S('S6').sentences[1].start);
  await press(page, 'Space');                            // plays session 9; pauses at the end of the trace
  const ok = await waitPaused(page, 60000);
  log('  S6 paused at end:', ok, 'at', T.now().toFixed(1) + 's');
});

// ---- S7 gate (cut: jump to session 3, open Inspector, step to just before the attacker-address gate) ----
if (want('S7')) {
  await gotoApp(page, URL2, '#/arena?act=2'); await press(page, '1');
  await press(page, ']'); await sleep(900);
  await chip(page, 3); await sleep(400);
  const start3 = startedIndex(ev2, 3);
  for (let i = start3 + 1; i < gate3; i++) { await press(page, 'Shift+ArrowRight'); await sleep(25); }
  await sleep(500);
  await press(page, 'Escape');                           // close modals that popped for the earlier blocked calls
  await sleep(900);
}
await shot('S7', async (T) => {
  await T.until(0.8);
  await press(page, 'Shift+ArrowRight');                 // the attacker-address gate decision arrives, the card pops
  await T.until(S('S7').sentences[3].start);             // "This recipient is the attacker's address"
  await zoom(page, 1.3, 960, 520, 1800);
  await T.until(S('S7').sentences[4].start - 0.4);       // leave-one-out
  await zoom(page, 1.4, 960, 700, 1800);
});
if (want('S7')) {                                        // deck screenshot, 16:9, in its own context (the recorded page keeps its viewport)
  const ctx2 = await browser.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1, colorScheme: 'dark' });
  await ctx2.addInitScript(INIT);
  const p2 = await ctx2.newPage();
  await gotoApp(p2, URL2, '#/arena?act=2');
  await press(p2, ']'); await sleep(900);
  await chip(p2, 3); await sleep(400);
  for (let i = startedIndex(ev2, 3) + 1; i <= gate3; i++) { await press(p2, 'Shift+ArrowRight'); await sleep(25); }
  await sleep(2200);
  await p2.screenshot({ path: path.join(ROOT, 'deliverables', 'demo_screenshot.png') });
  await ctx2.close();
  log('wrote deliverables/demo_screenshot.png');
}

// ---- S8 chain of custody ----
const drawerScroll = (y) => page.evaluate((y) => { const el = document.querySelector('aside[aria-label^="Record"] .overflow-y-auto'); if (el) el.scrollTo({ top: y, behavior: 'smooth' }); }, y);
await shot('S8', async (T) => {
  await zoom(page, 1, 960, 520, 10);
  await press(page, 'Escape'); await sleep(900);         // dock the gate card; the graph focuses the decisive source
  const node = page.locator(`.react-flow__node[data-id="${poisonRec}"]`).first();
  if (!(await node.count())) { await page.locator('button:has-text("3 hops")').first().click(); await sleep(900); }
  await T.until(S('S8').sentences[0].start + 1.5);
  await glide(page, page.locator(`.react-flow__node[data-id="${poisonRec}"]`).first());
  await T.until(S('S8').sentences[1].start);
  await zoom(page, 1.3, 1920, 330, 1800);
  await T.until(S('S8').sentences[2].start - 0.2);
  await drawerScroll(330);
  await T.until(S('S8').sentences[3].start - 0.2);
  await drawerScroll(2000);
  await T.until(S('S8').min_s - 1.6);
  await zoom(page, 1, 1920, 330, 1200);
});

// ---- S9 rollback ----
await shot('S9', async (T) => {
  await drawerScroll(0);
  await T.until(S('S9').sentences[0].start + 0.6);
  await glide(page, page.locator('button:has-text("Roll back src_")').first());   // the gate card's button: root = the poisoned email source
  await page.waitForSelector('button:has-text("Export exposure report"):not([disabled])', { timeout: 20000 }).catch(() => log('  rollback did not finish'));
});

// ---- S10 ledger ----
await shot('S10', async (T) => {
  if (await page.locator('button:has-text("Close")').count()) { await glide(page, page.locator('button:has-text("Close")').last()); await sleep(700); }
  await glide(page, page.locator('[role="tab"]:has-text("Ledger")').first());
  await T.until(S('S10').sentences[1].start - 0.5);
  await glide(page, page.locator('button:has-text("Verify chain")').first());
  await page.waitForSelector('text=/entries verified/', { timeout: 15000 }).catch(() => log('  verify pill missing'));
  await T.until(S('S10').sentences[2].start - 0.3);
  await glide(page, page.locator('button:has-text("Tamper demo")').first());
  await page.waitForSelector('text=/broken at/', { timeout: 15000 }).catch(() => log('  tamper pill missing'));
});

// ---- S11 bench (sample data, short) ----
await shot('S11', async (T) => {
  await glide(page, page.locator('nav[aria-label="Views"] button:has-text("Bench")').first());
  await T.until(S('S11').min_s);
});

// ---- S12 end card ----
if (want('S12')) { await page.goto(cardUrl('end.html')); await sleep(300); }
await shot('S12', async (T) => { await T.until(S('S12').min_s); });

// ------------------------------------------------------------------------------------------------ write timeline
const tlPath = path.join(WORK, 'timeline.json');
const prev = fs.existsSync(tlPath) && ONLY ? JSON.parse(fs.readFileSync(tlPath, 'utf8')) : [];
const merged = [...prev.filter((p) => !timeline.some((t) => t.id === p.id)), ...timeline].sort((a, b) => manifest.shots.findIndex((s) => s.id === a.id) - manifest.shots.findIndex((s) => s.id === b.id));
fs.writeFileSync(tlPath, JSON.stringify(merged));
await cdp.send('Page.stopScreencast').catch(() => {});
await browser.close();
log('done:', merged.map((t) => `${t.id}:${t.dur_wall.toFixed(1)}s`).join(' '));

// Cross-cutting actions: they combine the API, the replay engine and the UI state.
import { useCallback, useRef } from 'react';
import type { MemoryRecord } from '../types';
import { getRollbackExtras, postQuarantine, postRollback } from '../api';
import type { QuarantineAction } from '../api';
import type { SynthContext } from '../lib/rollbackSynth';
import { isoIST } from '../lib/format';
import { useData, useReplay } from './app';
import { useRoute } from './route';
import { useUi } from './ui';

// One rollback overlay exists at a time, so run bookkeeping can live at module level.
let runToken = 0;
const sleep = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms));

export interface Actions {
  openRecord: (id: string, opts?: { goto?: boolean }) => void;
  openGate: () => void;
  startRollback: (root: string, mode: 'revert' | 'erase') => Promise<void>;
  closeRollback: () => void;
  defaultRollbackRoot: () => string | null;
  quarantineAction: (rec: MemoryRecord, action: QuarantineAction) => Promise<void>;
}

export function useActions(): Actions {
  const { ui, dispatch: ud, toast } = useUi();
  const { replay, engine, dispatch: ed } = useReplay();
  const { markFixture } = useData();
  const { route, go } = useRoute();
  const actionsRef = useRef(engine.actions);
  actionsRef.current = engine.actions;

  const openRecord = useCallback((id: string, opts?: { goto?: boolean }) => {
    ud({ type: 'select', id });
    ud({ type: 'focus', id });
    if (opts?.goto !== false && route.view !== 'inspector') go('inspector');
  }, [ud, route.view, go]);

  const openGate = useCallback(() => {
    if (route.view !== 'inspector') go('inspector');
    ud({ type: 'dock', tab: 'gate' });
    ud({ type: 'gateOpen', open: true });
  }, [ud, route.view, go]);

  const defaultRollbackRoot = useCallback((): string | null => {
    const custos = replay.sides.custos.records;
    const sel = ui.selectedId && custos[ui.selectedId];
    if (sel && sel.status !== 'reverted' && sel.status !== 'erased') return sel.id;
    for (let i = replay.gates.length - 1; i >= 0; i--) {
      const g = replay.gates[i].gate;
      if (g.decision !== 'allow' && g.loo?.decisive[0] && custos[g.loo.decisive[0]]) return g.loo.decisive[0];
    }
    const held = Object.values(custos).find((r) => r.status === 'quarantined');
    if (held) return held.id;
    return null;
  }, [replay, ui.selectedId]);

  const closeRollback = useCallback(() => {
    runToken++;
    ud({ type: 'rollback', value: null });
  }, [ud]);

  const startRollback = useCallback(async (root: string, mode: 'revert' | 'erase') => {
    const token = ++runToken;
    // a re-run (e.g. switching revert <-> DPDP erase) starts from the pre-rollback state
    const prior = actionsRef.current.filter((a) => a.kind === 'rollback' && a.result.root === root).at(-1);
    if (prior) ed({ type: 'removeAction', id: prior.id });
    ud({ type: 'rollback', value: { root, mode, phase: 'loading', hop: 0, rederiveShown: false, exposureShown: false } });
    ed({ type: 'pause' });
    const synth: SynthContext = {
      records: Object.values(replay.sides.custos.records),
      reads: replay.reads.map((r) => ({ session: r.session, principal: r.principal, returned: r.returned })),
      gates: replay.gates.map((g) => ({ session: g.session, gate: g.gate })),
      ledgerTip: replay.maxLedgerSeq,
    };
    try {
      const res = await postRollback({ root, mode }, synth);
      markFixture(res.source);
      const extras = await getRollbackExtras(res.data, res.source);
      if (token !== runToken) return;
      ud({ type: 'rollbackPatch', patch: { result: res.data, extras, source: res.source, phase: 'animating' } });
      const hops = res.data.closure_by_hop.length;
      await sleep(350);
      for (let h = 1; h <= hops; h++) {
        if (token !== runToken) return;
        ud({ type: 'rollbackPatch', patch: { hop: h } });
        await sleep(400);
      }
      if (token !== runToken) return;
      ud({ type: 'rollbackPatch', patch: { rederiveShown: true } });
      await sleep(650);
      if (token !== runToken) return;
      const ts = extras.ledger[0]?.ts ?? isoIST(Date.now());
      ed({ type: 'commit', action: { kind: 'rollback', result: res.data, records: extras.records, ledger: extras.ledger, ts } });
      ud({ type: 'rollbackPatch', patch: { exposureShown: true, phase: 'done' } });
      ud({ type: 'store' });
    } catch (e) {
      if (token !== runToken) return;
      ud({ type: 'rollbackPatch', patch: { phase: 'error', error: (e as Error).message } });
    }
  }, [ud, ed, replay, markFixture]);

  const quarantineAction = useCallback(async (rec: MemoryRecord, action: QuarantineAction) => {
    try {
      if (action === 'poison') {
        const tip = replay.ledger[replay.ledger.length - 1];
        const now = isoIST(Date.parse(tip?.ts ?? new Date().toISOString()) + 3000);
        const res = await postQuarantine(rec, 'poison', { tip, now });
        markFixture(res.source);
        ed({ type: 'commit', action: { kind: 'quarantine', recordId: rec.id, action, records: res.data.records, ledger: res.data.ledger, ts: now } });
        ud({ type: 'select', id: rec.id });
        ud({ type: 'focus', id: rec.id });
        if (route.view !== 'inspector') go('inspector');
        void startRollback(rec.id, 'revert');
        return;
      }
      const tip = replay.ledger[replay.ledger.length - 1];
      const now = isoIST(Date.parse(tip?.ts ?? new Date().toISOString()) + 4000);
      const conflict = rec.conflicts_with ? replay.sides.custos.records[rec.conflicts_with.id] : undefined;
      const res = await postQuarantine(rec, action, { tip, now, conflict });
      markFixture(res.source);
      ed({ type: 'commit', action: { kind: 'quarantine', recordId: rec.id, action, records: res.data.records, ledger: res.data.ledger, ts: now } });
      ud({ type: 'store' });
      toast('ok', action === 'confirm_t3'
        ? `${rec.id} promoted to T3 after the (simulated) ERP update; the old fact was closed in time, not deleted.`
        : `${rec.id} rejected. It stays in the ledger as a rejected write.`);
    } catch (e) {
      toast('error', `Quarantine action failed: ${(e as Error).message}`);
    }
  }, [replay, ed, ud, go, route.view, markFixture, startRollback, toast]);

  return { openRecord, openGate, startRollback, closeRollback, defaultRollbackRoot, quarantineAction };
}

import { useEffect, useMemo, useState } from 'react';
import { Biohazard, CheckCheck, Pause, XCircle } from 'lucide-react';
import type { MemoryRecord } from '../../types';
import { getQuarantine } from '../../api';
import { useData, useReplay } from '../../state/app';
import { useActions } from '../../state/actions';
import { useUi } from '../../state/ui';
import { memoryLabel } from '../../lib/labels';
import { Button, TierChip } from '../../components/ui';

/** Held records: derived from the replay; for a live trace the store's GET /api/quarantine is authoritative. */
export function useQuarantineList(): MemoryRecord[] {
  const { replay } = useReplay();
  const { trace, markFixture } = useData();
  const { ui } = useUi();
  const [api, setApi] = useState<MemoryRecord[] | null>(null);
  useEffect(() => {
    let dead = false;
    getQuarantine().then((r) => { if (!dead) { setApi(r.data); markFixture(r.source); } }).catch(() => { if (!dead) setApi(null); });
    return () => { dead = true; };
  }, [ui.storeVersion, trace, markFixture]);
  return useMemo(() => {
    if (trace?.live && api) return api;
    const held = Object.values(replay.sides.custos.records).filter((r) => r.status === 'quarantined');
    // enrich with the store's copy (conflicts_with, decision.rule) when it has one
    return held.map((r) => ({ ...(api?.find((a) => a.id === r.id) ?? {}), ...r }));
  }, [replay.sides.custos.records, api, trace?.live]);
}

export function QuarantineQueue() {
  const list = useQuarantineList();
  const { replay } = useReplay();
  const { openRecord, quarantineAction } = useActions();
  const nli = (r: MemoryRecord) => r.checks?.find((c) => c.check === 'nli');
  if (list.length === 0) {
    const closed = Object.values(replay.sides.custos.records).filter((r) => r.decision?.verdict === 'quarantine' && r.status !== 'quarantined');
    return (
      <div className="grid h-full place-items-center p-6 text-center text-muted">
        <div>
          <CheckCheck size={30} className="mx-auto text-ok" />
          <div className="mt-2 text-lg font-semibold text-ink/85">Quarantine is empty</div>
          <p className="mt-1 max-w-md text-sm">{closed.length ? `${closed.length} held claim${closed.length > 1 ? 's were' : ' was'} resolved by a reviewer.` : 'No write has been held. Lower-tier claims that contradict a higher-tier fact land here (Act 1, session 3).'}</p>
        </div>
      </div>
    );
  }
  return (
    <ul className="space-y-2.5 p-3">
      {list.map((r) => (
        <li key={r.id} className="animate-pop-in rounded-xl border-2 border-dashed border-warn/70 bg-warn/8 p-3.5">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <Pause size={20} className="text-warn" strokeWidth={2.6} />
            <button type="button" onClick={() => openRecord(r.id, { goto: false })} className="mono text-base font-bold text-ink hover:underline">{r.id}</button>
            <span className="min-w-0 text-base font-semibold text-ink">{memoryLabel(r)}</span>
            {r.tier && <TierChip tier={r.tier} size="sm" />}
            <span className="text-sm font-bold tracking-wide text-warn">HELD</span>
            <span className="ml-auto text-xs text-muted">since S{r.session}</span>
          </div>
          {r.conflicts_with && (
            <div className="mt-1.5 flex flex-wrap items-center gap-2 pl-8 text-sm text-ink">
              vs <button type="button" onClick={() => openRecord(r.conflicts_with!.id, { goto: false })} className="mono font-semibold hover:underline">{r.conflicts_with.id}</button>
              <span className="text-muted">{r.conflicts_with.text}</span> <TierChip tier={r.conflicts_with.tier} size="xs" />
            </div>
          )}
          <div className="mt-1 pl-8 text-sm text-muted">
            rule <span className="mono text-ink">{r.decision?.rule ?? 'write.lower_tier_conflict'}</span>{nli(r)?.score !== undefined && <> · NLI {nli(r)!.score!.toFixed(2)}</>}
          </div>
          <div className="mt-3 flex flex-wrap gap-2 pl-8">
            <Button variant="primary" icon={CheckCheck} onClick={() => void quarantineAction(r, 'confirm_t3')} title="Simulates the ERP vendor update, promotes this claim to T3 and closes the old fact in time">Confirm via T3</Button>
            <Button icon={XCircle} onClick={() => void quarantineAction(r, 'reject')}>Reject</Button>
            <Button variant="danger" icon={Biohazard} onClick={() => void quarantineAction(r, 'poison')}>Mark poison</Button>
          </div>
        </li>
      ))}
    </ul>
  );
}

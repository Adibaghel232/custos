// Policy view: the tier map and the gate rules as code (nice-to-have, kept small).
import { RULES } from '../../lib/policy';
import { TIERS, TIER_META } from '../../lib/tiers';
import { TierChip } from '../../components/ui';

export function PolicyView() {
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto grid max-w-[110rem] gap-4 p-4 2xl:grid-cols-[28rem_1fr]">
        <section className="card p-5">
          <h2 className="caps mb-3">Tier map (channel → tier)</h2>
          <ul className="space-y-3">
            {[...TIERS].reverse().map((t) => (
              <li key={t} className="flex items-start gap-3">
                <TierChip tier={t} size="md" className="mt-0.5" />
                <div>
                  <div className="text-base font-semibold text-ink">{TIER_META[t].name}</div>
                  <div className="text-sm text-muted">{TIER_META[t].desc}</div>
                </div>
              </li>
            ))}
          </ul>
          <p className="mt-4 text-sm text-muted">Tiers come from the channel, not the content. An unknown channel is T0 (fails closed). A derived record takes the lowest tier among its parents.</p>
        </section>
        <section className="space-y-3">
          {RULES.map((r) => (
            <article key={r.id} className="card p-4">
              <div className="flex flex-wrap items-baseline gap-3">
                <code className="mono text-base font-bold text-ink">{r.id}</code>
                <span className="chip">{r.stage}</span>
                <span className="text-base font-semibold text-ink/90">{r.title}</span>
              </div>
              <p className="mt-1 text-sm text-muted">{r.text}</p>
              <pre className="mono mt-2 overflow-x-auto rounded-lg border border-border bg-bg/60 p-3 text-sm text-ink">{r.code}</pre>
            </article>
          ))}
        </section>
      </div>
    </div>
  );
}

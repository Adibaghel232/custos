import {
  ChartColumn, Columns2, FastForward, Keyboard, Moon, Network, Pause, Play, RotateCcw, Scale, SkipBack, SkipForward, StepForward, Sun, Unplug,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useData, useReplay } from '../state/app';
import { SPEEDS } from '../state/app';
import { VIEWS, useRoute } from '../state/route';
import type { View } from '../state/route';
import { useUi } from '../state/ui';
import { formatISTDateTime, cx } from '../lib/format';
import { maxCursor, sessionStartedIndex } from '../state/engine';
import { Button, IconButton, Kbd, Segmented, Toggle } from './ui';

export const prettyModel = (id: string | undefined): string => {
  if (!id) return 'Nemotron';
  const m = id.replace(/^nvidia\//, '').replace(/-/g, ' ');
  return m
    .replace(/^nemotron/i, 'Nemotron')
    .replace(/\blightning\b/i, 'Lightning')
    .replace(/\bsuper\b/i, 'Super')
    .replace(/(\d+)b a(\d+)b/i, (_x, a: string, b: string) => `${a}B-A${b}B`)
    .replace(/(\d+)b\b/i, '$1B');
};

const TAB_META: Record<View, { label: string; icon: LucideIcon; hint: string }> = {
  arena: { label: 'Arena', icon: Columns2, hint: 'Undefended vs Custos, side by side' },
  inspector: { label: 'Inspector', icon: Network, hint: 'Custody graph, gate, quarantine, ledger' },
  bench: { label: 'Bench', icon: ChartColumn, hint: 'Custos-Bench results' },
  policy: { label: 'Policy', icon: Scale, hint: 'Tier map and gate rules as code' },
};

export function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden>
        <path d="M16 3.5l10 4.4v8c0 5.6-4 10-10 12.1C10 25.900 6 21.500 6 15.900v-8z" fill="none" stroke="rgb(var(--ok))" strokeWidth="2.4" strokeLinejoin="round" />
        <circle cx="16" cy="15" r="3.2" fill="rgb(var(--ok))" />
        <path d="M16 18v5" stroke="rgb(var(--ok))" strokeWidth="2.4" strokeLinecap="round" />
      </svg>
      <div className="whitespace-nowrap leading-none">
        <div className="text-xl font-extrabold tracking-[0.16em] text-ink">CUSTOS</div>
        <div className="mt-0.5 hidden whitespace-nowrap text-[0.6875rem] font-medium tracking-wide text-muted min-[2100px]:block">chain of custody for agent memory</div>
      </div>
    </div>
  );
}

function StatusBlock() {
  const { trace, traceSource, backend, retryBackend, fixtureMode } = useData();
  const { engine } = useReplay();
  const isFixtureTrace = traceSource === 'fixture';
  const live = !!trace?.live;
  const model = prettyModel(trace?.models.agent);
  return (
    <div className="flex items-center gap-3">
      <div className="whitespace-nowrap text-right leading-tight">
        <div className={cx('flex items-center justify-end gap-2 text-sm font-bold tracking-wide', live ? 'text-dangerink' : 'text-ink')}>
          <span className={cx('inline-block h-2.5 w-2.5 rounded-full', live ? 'dot-live bg-danger' : 'bg-info')} aria-hidden />
          {live ? 'LIVE' : isFixtureTrace ? 'REPLAY · FIXTURE TRACE' : 'REPLAY of a live run'}
          <span className="font-semibold text-muted">· {engine.speed}×</span>
        </div>
        <div className="mt-0.5 hidden max-w-[25rem] truncate text-xs text-muted min-[1700px]:block">
          {trace ? (isFixtureTrace ? 'hand-scripted sample' : `recorded ${formatISTDateTime(trace.recorded_at)} IST`) : 'loading…'} · {model}
        </div>
      </div>
      {fixtureMode && (
        <button
          type="button"
          onClick={() => void retryBackend()}
          title={backend === 'down' ? 'FastAPI backend not reachable at /api. Showing static fixtures. Click to retry.' : 'Some data on screen comes from static fixtures. Click to re-check the backend.'}
          className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-warn/70 bg-warn/12 px-2.5 py-1.5 text-xs font-extrabold tracking-wide text-ink hover:bg-warn/20"
        >
          <Unplug size={16} className="text-warn" />
          FIXTURE DATA
        </button>
      )}
    </div>
  );
}

export function Header() {
  const { route, go } = useRoute();
  const { act, setAct, traces, scenario } = useData();
  const { ui, setTheme, dispatch } = useUi();
  const scenarios = [...new Set(traces.map((t) => t.scenario))];
  const scenarioLabel = (s: string) => (s === 'vendor_fraud' ? 'Vendor fraud' : s.replace(/_/g, ' '));
  return (
    <header className="flex h-16 shrink-0 items-center gap-4 border-b border-border bg-panel px-4 2xl:gap-6 2xl:px-6">
      <Logo />
      <nav className="ml-1 flex shrink-0 items-center gap-1 2xl:ml-3" aria-label="Views">
        {VIEWS.map((v) => {
          const { label, icon: Icon, hint } = TAB_META[v];
          const on = route.view === v;
          return (
            <button
              key={v}
              type="button"
              title={hint}
              onClick={() => go(v)}
              className={cx(
                'flex items-center gap-2 whitespace-nowrap rounded-lg px-3 py-2 text-base font-semibold transition-colors 2xl:px-3.5',
                on ? 'bg-panel2 text-ink shadow-[0_0_0_1px_rgb(var(--border))]' : 'text-muted hover:bg-panel2/60 hover:text-ink',
              )}
              aria-current={on ? 'page' : undefined}
            >
              <Icon size={19} strokeWidth={2.2} className={on ? 'text-ok' : ''} />
              <span className="hidden min-[1500px]:inline">{label}</span>
            </button>
          );
        })}
      </nav>
      <div className="ml-auto hidden items-center gap-3 whitespace-nowrap lg:flex">
        <label className="hidden items-center gap-2 text-sm text-muted min-[2100px]:flex">
          <span className="hidden min-[1900px]:inline">Scenario</span>
          <select
            value={scenario}
            onChange={() => undefined}
            className="rounded-lg border border-border bg-panel2 px-3 py-1.5 text-sm font-semibold text-ink"
            aria-label="Scenario"
          >
            {(scenarios.length ? scenarios : [scenario]).map((s) => <option key={s} value={s}>{scenarioLabel(s)}</option>)}
          </select>
        </label>
        <Segmented<1 | 2>
          label="Act"
          value={act}
          onChange={setAct}
          options={[
            { value: 1, label: <span className="whitespace-nowrap">Act 1 <span className="hidden text-faint min-[1800px]:inline">· obvious attacker</span></span>, title: 'Act 1: the obvious attacker. A T0 email contradicts the T3 ERP record, so it is quarantined.' },
            { value: 2, label: <span className="whitespace-nowrap">Act 2 <span className="hidden text-faint min-[1800px]:inline">· smart attacker</span></span>, title: 'Act 2: the smart attacker. Nothing to contradict, so it is committed as T0 and the consequence gate blocks the payment.' },
          ]}
        />
      </div>
      <div className="ml-auto shrink-0 lg:ml-2"><StatusBlock /></div>
      <div className="flex items-center gap-1">
        <IconButton icon={ui.theme === 'dark' ? Sun : Moon} label={ui.theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'} onClick={() => setTheme(ui.theme === 'dark' ? 'light' : 'dark')} />
        <IconButton icon={Keyboard} label="Keyboard shortcuts (?)" onClick={() => dispatch({ type: 'help', open: true })} />
      </div>
    </header>
  );
}

export function TimelineBar() {
  const { engine, dispatch, sessions, replay, awaitingRollback } = useReplay();
  const current = replay.currentSession;
  const done = (n: number) => n < current || (n === current && engine.cursor >= engine.events.length - 1);
  const beatActive = engine.events[engine.cursor]?.type === 'beat' && !engine.playing ? replay.beat : undefined;
  const atEnd = engine.cursor >= maxCursor(engine);

  return (
    <div className="flex h-[3.75rem] shrink-0 items-center gap-4 border-b border-border bg-panel/70 px-4 2xl:px-6">
      {/* session scrubber */}
      <ol className="flex items-center" aria-label="Sessions">
        {sessions.map((s, i) => {
          const isCur = s.n === current;
          const isDone = done(s.n);
          const started = sessionStartedIndex(engine.events, s.n) <= engine.cursor;
          return (
            <li key={s.n} className="flex items-center">
              {i > 0 && <span aria-hidden className={cx('h-0.5 w-3 2xl:w-5', started ? 'bg-ok/60' : 'bg-border')} />}
              <button
                type="button"
                title={`S${s.n} · ${s.label}\nclick: jump (paused) · shift+click: jump and play`}
                onClick={(e) => dispatch({ type: 'gotoSession', n: s.n, play: e.shiftKey })}
                className={cx(
                  'grid h-9 min-w-[2.6rem] place-items-center rounded-lg border px-1.5 text-sm font-bold tabular-nums transition-colors',
                  isCur ? 'border-ok bg-ok/20 text-ink shadow-[0_0_0_3px_rgb(var(--ok)/0.18)]' : isDone || started ? 'border-border bg-panel2 text-ink' : 'border-border/70 bg-transparent text-faint hover:text-ink',
                )}
                aria-current={isCur ? 'step' : undefined}
              >
                S{s.n}
              </button>
            </li>
          );
        })}
      </ol>
      <div className="hidden min-w-0 flex-1 2xl:block">
        {beatActive ? (
          <div className="inline-block max-w-full truncate rounded-lg border border-warn/60 bg-warn/12 px-3 py-1.5 align-middle text-sm font-semibold text-ink animate-pop-in" title={beatActive.payload.note ?? beatActive.payload.title}>
            <Pause size={15} className="mr-1.5 inline text-warn" />
            {beatActive.payload.title}
          </div>
        ) : awaitingRollback && !engine.playing ? (
          <div className="inline-block whitespace-nowrap rounded-lg border border-danger/60 bg-danger/12 px-3 py-1.5 text-sm font-semibold text-ink">
            Waiting for the reviewer: press <Kbd>R</Kbd> to roll back
          </div>
        ) : (
          <div className="truncate text-sm text-muted" title={sessions.find((x) => x.n === current)?.label}>
            {current > 0 ? <><span className="font-semibold text-ink">S{current}</span> · {sessions.find((x) => x.n === current)?.label}</> : 'Press N to start session 1, or play'}
          </div>
        )}
      </div>

      {/* transport */}
      <div className="ml-auto flex items-center gap-2">
        <IconButton icon={RotateCcw} label="Reset (0)" onClick={() => dispatch({ type: 'reset' })} />
        <IconButton icon={SkipBack} label="Previous beat (←)" onClick={() => dispatch({ type: 'prevBeat' })} />
        <Button
          variant="primary"
          icon={engine.playing ? Pause : Play}
          onClick={() => dispatch({ type: 'toggle' })}
          disabled={atEnd && !engine.playing}
          className="min-w-[7.5rem] !py-2 text-base"
          kbd="space"
        >
          {engine.playing ? 'Pause' : 'Play'}
        </Button>
        <Button icon={SkipForward} onClick={() => dispatch({ type: 'nextBeat' })} title="Jump to the next narration beat (B or →)" kbd="B">
          <span className="hidden xl:inline">Next beat</span>
        </Button>
        <Button icon={FastForward} onClick={() => dispatch({ type: 'nextSession' })} title="Next session, and play it (N)" kbd="N">
          <span className="hidden xl:inline">Next session</span>
        </Button>
        <Segmented
          label="Speed"
          size="sm"
          value={engine.speed}
          onChange={(speed) => dispatch({ type: 'speed', speed })}
          options={SPEEDS.map((sp) => ({ value: sp, label: `${sp}×`, title: `Playback speed ${sp}× (key ${sp})` }))}
        />
        <div className="hidden 2xl:block">
          <Toggle checked={engine.beats} onChange={(on) => dispatch({ type: 'beats', on })} label="Pause at beats" hint="Auto-pause at narration beats" />
        </div>
        <IconButton icon={StepForward} label="Step one event (Shift+→)" onClick={() => dispatch({ type: 'step' })} className="hidden xl:grid" />
      </div>
    </div>
  );
}

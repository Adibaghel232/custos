// Replay engine: cursor over a TraceEvent list, play / pause / 1x 2x 4x, auto-pausing beats, session
// jumps, and the "held" recorded rollback (the player never applies a recorded `revert` event by itself:
// the reviewer triggers it, and in fixture mode we play the recorded result).
import type { TraceEvent } from '../types';
import type { AppliedAction, NewAction } from './replay';

export type Speed = 1 | 2 | 4;

/** Dead air in the recording (LLM latency) is capped so a replay never stalls on screen. */
export const MAX_GAP_MS = 2200;

export interface EngineState {
  events: TraceEvent[];
  live: boolean;
  cursor: number;
  playing: boolean;
  speed: Speed;
  beats: boolean;
  actions: AppliedAction[];
  /** index of the first recorded `revert` event (held until the reviewer acts); -1 when none */
  holdIdx: number;
  released: boolean;
  epoch: number;
}

export type EngineAction =
  | { type: 'load'; events: TraceEvent[]; live: boolean }
  | { type: 'append'; event: TraceEvent }
  | { type: 'play' }
  | { type: 'pause' }
  | { type: 'toggle' }
  | { type: 'speed'; speed: Speed }
  | { type: 'beats'; on: boolean }
  | { type: 'step' }
  | { type: 'stepBack' }
  | { type: 'seek'; index: number; play?: boolean }
  | { type: 'nextSession' }
  | { type: 'prevSession' }
  | { type: 'gotoSession'; n: number; play?: boolean }
  | { type: 'nextBeat' }
  | { type: 'prevBeat' }
  | { type: 'reset' }
  | { type: 'commit'; action: NewAction }
  | { type: 'removeAction'; id: string };

export const initialEngine: EngineState = {
  events: [], live: false, cursor: -1, playing: false, speed: 1, beats: true, actions: [], holdIdx: -1, released: false, epoch: 0,
};

const findHold = (events: TraceEvent[], live: boolean): number => (live ? -1 : events.findIndex((e) => e.type === 'revert'));

export function maxCursor(s: EngineState): number {
  if (s.holdIdx >= 0 && !s.released) return s.holdIdx - 1;
  return s.events.length - 1;
}

const clamp = (s: EngineState, i: number): number => Math.max(-1, Math.min(i, maxCursor(s)));

/** First event index of session n (the undefended/custos session_start pair share it). */
export function sessionFirstIndex(events: TraceEvent[], n: number): number {
  return events.findIndex((e) => e.session === n);
}

/** Index after both session_start events of session n are applied. */
export function sessionStartedIndex(events: TraceEvent[], n: number): number {
  let i = sessionFirstIndex(events, n);
  if (i < 0) return -1;
  while (i + 1 < events.length && events[i + 1].type === 'session_start' && events[i + 1].session === n) i++;
  return i;
}

export function sessionNumbers(events: TraceEvent[]): number[] {
  const out: number[] = [];
  for (const e of events) if (e.type === 'session_start' && !out.includes(e.session)) out.push(e.session);
  return out.sort((a, b) => a - b);
}

const sessionAt = (s: EngineState): number => (s.cursor >= 0 ? s.events[s.cursor].session : 0);

let actionCounter = 0;

export function engineReducer(s: EngineState, a: EngineAction): EngineState {
  switch (a.type) {
    case 'load':
      return { ...initialEngine, speed: s.speed, beats: s.beats, events: a.events, live: a.live, holdIdx: findHold(a.events, a.live), epoch: s.epoch + 1 };
    case 'append': {
      const events = [...s.events, a.event];
      return { ...s, events, holdIdx: s.holdIdx >= 0 ? s.holdIdx : findHold(events, s.live) };
    }
    case 'play': {
      if (s.cursor >= maxCursor(s)) return { ...s, playing: false };
      return { ...s, playing: true };
    }
    case 'pause':
      return { ...s, playing: false };
    case 'toggle':
      return engineReducer(s, { type: s.playing ? 'pause' : 'play' });
    case 'speed':
      return { ...s, speed: a.speed };
    case 'beats':
      return { ...s, beats: a.on };
    case 'step': {
      const cursor = clamp(s, s.cursor + 1);
      if (cursor === s.cursor) return { ...s, playing: false };
      const ev = s.events[cursor];
      const pauses = s.beats && ev.type === 'beat' && (ev.payload as { pause?: boolean }).pause !== false;
      return { ...s, cursor, playing: s.playing && !pauses && cursor < maxCursor(s) };
    }
    case 'stepBack':
      return { ...s, cursor: clamp(s, s.cursor - 1), playing: false };
    case 'seek':
      return { ...s, cursor: clamp(s, a.index), playing: !!a.play && clamp(s, a.index) < maxCursor(s) };
    case 'nextSession': {
      const cur = sessionAt(s);
      const nums = sessionNumbers(s.events);
      const next = nums.find((n) => n > cur);
      if (next === undefined) return s;
      const first = sessionFirstIndex(s.events, next);
      return { ...s, cursor: clamp(s, first - 1), playing: true };
    }
    case 'prevSession': {
      const cur = sessionAt(s);
      const nums = sessionNumbers(s.events);
      const startedCur = cur > 0 ? sessionStartedIndex(s.events, cur) : -1;
      // mid-session: restart this session; at its start: go to the previous one
      const target = cur > 0 && s.cursor > startedCur ? cur : nums.filter((n) => n < cur).pop();
      if (target === undefined) return { ...s, cursor: -1, playing: false };
      return { ...s, cursor: clamp(s, sessionStartedIndex(s.events, target)), playing: false };
    }
    case 'gotoSession': {
      const idx = sessionStartedIndex(s.events, a.n);
      if (idx < 0) return s;
      if (a.play) return { ...s, cursor: clamp(s, sessionFirstIndex(s.events, a.n) - 1), playing: true };
      return { ...s, cursor: clamp(s, idx), playing: false };
    }
    case 'nextBeat': {
      for (let i = s.cursor + 1; i <= maxCursor(s); i++) {
        if (s.events[i].type === 'beat') return { ...s, cursor: i, playing: false };
      }
      return { ...s, cursor: maxCursor(s), playing: false };
    }
    case 'prevBeat': {
      // skip the beat we are standing on
      let i = s.cursor - (s.events[s.cursor]?.type === 'beat' ? 1 : 0);
      for (; i >= 0; i--) if (s.events[i].type === 'beat') return { ...s, cursor: i, playing: false };
      return { ...s, cursor: -1, playing: false };
    }
    case 'reset':
      return { ...s, cursor: -1, playing: false, actions: [], released: false, epoch: s.epoch + 1 };
    case 'commit': {
      const releasing = a.action.kind === 'rollback' && s.holdIdx >= 0 && !s.released;
      const released = s.released || releasing;
      const base = { ...s, released };
      const cursor = releasing ? Math.max(s.cursor, s.holdIdx) : s.cursor;
      const action = { ...a.action, id: `act_${++actionCounter}`, at: cursor } as AppliedAction;
      return { ...base, cursor, actions: [...s.actions, action], playing: false };
    }
    case 'removeAction':
      return { ...s, actions: s.actions.filter((x) => x.id !== a.id) };
  }
}

/** Delay before applying event cursor+1 (real recorded gap, capped, scaled by speed). */
export function nextDelay(s: EngineState): number | null {
  const next = s.events[s.cursor + 1];
  if (!next || s.cursor + 1 > maxCursor(s)) return null;
  const prevT = s.cursor >= 0 ? s.events[s.cursor].t_ms : next.t_ms;
  const gap = Math.max(0, Math.min(next.t_ms - prevT, MAX_GAP_MS));
  return gap / s.speed;
}

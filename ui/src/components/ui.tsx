// Small shared building blocks. Rule of the house: colour is never the only cue (tier = label, status = icon + word).
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import {
  ArrowDownToLine, Check, CircleCheck, CircleDot, CircleX, CirclePause, Clock, Copy, Eraser, Undo2,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { RecordStatus, Tier } from '../types';
import { cx } from '../lib/format';
import { STATUS_META, TIER_META } from '../lib/tiers';

// ---- tier chip ------------------------------------------------------------------------------------

const TIER_FILL: Record<Tier, string> = { T0: 'bg-t0', T1: 'bg-t1', T2: 'bg-t2', T3: 'bg-t3', T4: 'bg-t4' };
const TIER_SIZE = {
  xs: 'text-xs px-1.5 py-px rounded min-w-[1.9rem]',
  sm: 'text-sm px-2 py-0.5 rounded-md min-w-[2.4rem]',
  md: 'text-base px-2.5 py-0.5 rounded-md min-w-[2.8rem]',
  lg: 'text-xl px-3.5 py-1 rounded-lg min-w-[3.4rem]',
} as const;

export function TierChip({ tier, size = 'sm', name = false, className }: { tier: Tier; size?: keyof typeof TIER_SIZE; name?: boolean; className?: string }) {
  const meta = TIER_META[tier];
  return (
    <span
      title={`${tier} · ${meta.name}: ${meta.desc}`}
      className={cx(
        'relative inline-flex shrink-0 items-center justify-center gap-1.5 overflow-hidden whitespace-nowrap font-bold leading-tight tabular-nums text-onfill',
        TIER_FILL[tier], TIER_SIZE[size], tier === 'T4' && 'tier-ring-t4', className,
      )}
    >
      {tier === 'T0' && <span aria-hidden className="hazard absolute inset-y-0 left-0 w-1.5 opacity-90" />}
      <span className={cx(tier === 'T0' && 'pl-1')}>{tier}</span>
      {name && <span className="font-semibold opacity-90">{meta.name}</span>}
    </span>
  );
}

// ---- status badge ---------------------------------------------------------------------------------

const STATUS_ICON: Record<RecordStatus, { icon: LucideIcon; cls: string }> = {
  committed: { icon: CircleCheck, cls: 'text-ok' },
  pending: { icon: Clock, cls: 'text-info' },
  quarantined: { icon: CirclePause, cls: 'text-warn' },
  rejected: { icon: CircleX, cls: 'text-dangerink' },
  superseded: { icon: ArrowDownToLine, cls: 'text-faint' },
  reverted: { icon: Undo2, cls: 'text-revertedink' },
  erased: { icon: Eraser, cls: 'text-revertedink' },
};

export function StatusIcon({ status, size = 18, className }: { status: RecordStatus; size?: number; className?: string }) {
  const { icon: Icon, cls } = STATUS_ICON[status];
  return <Icon size={size} strokeWidth={2.4} className={cx(cls, 'shrink-0', className)} aria-label={STATUS_META[status].label} />;
}

export function StatusBadge({ status, size = 'sm', className }: { status: RecordStatus; size?: 'xs' | 'sm' | 'md'; className?: string }) {
  const { cls } = STATUS_ICON[status];
  const text = size === 'xs' ? 'text-xs' : size === 'sm' ? 'text-sm' : 'text-base';
  return (
    <span className={cx('inline-flex items-center gap-1.5 font-semibold', text, cls, className)}>
      <StatusIcon status={status} size={size === 'md' ? 20 : 17} />
      <span>{STATUS_META[status].short}</span>
    </span>
  );
}

// ---- buttons --------------------------------------------------------------------------------------

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'default' | 'primary' | 'danger' | 'ghost'; icon?: LucideIcon; kbd?: string };

export function Button({ variant = 'default', icon: Icon, kbd, className, children, ...rest }: BtnProps) {
  return (
    <button
      type="button"
      {...rest}
      className={cx('btn', variant === 'primary' && 'btn-primary', variant === 'danger' && 'btn-danger', variant === 'ghost' && 'btn-ghost', className)}
    >
      {Icon && <Icon size={18} strokeWidth={2.2} />}
      {children}
      {kbd && <span className="kbd ml-0.5">{kbd}</span>}
    </button>
  );
}

export function IconButton({ icon: Icon, label, className, active, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { icon: LucideIcon; label: string; active?: boolean }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      {...rest}
      className={cx(
        'grid h-9 w-9 place-items-center rounded-lg border border-transparent text-muted transition-colors hover:bg-panel2 hover:text-ink disabled:opacity-40',
        active && 'border-border bg-panel2 text-ink', className,
      )}
    >
      <Icon size={20} strokeWidth={2.1} />
    </button>
  );
}

export function Segmented<T extends string | number>({ value, options, onChange, size = 'md', label }: {
  value: T;
  options: Array<{ value: T; label: ReactNode; title?: string }>;
  onChange: (v: T) => void;
  size?: 'sm' | 'md';
  label?: string;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-lg border border-border bg-bg/60 p-0.5">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          title={o.title}
          aria-pressed={o.value === value}
          onClick={() => onChange(o.value)}
          className={cx(
            'whitespace-nowrap rounded-md font-semibold transition-colors',
            size === 'sm' ? 'px-2.5 py-1 text-sm' : 'px-3.5 py-1.5 text-sm',
            o.value === value ? 'bg-panel2 text-ink shadow-[0_0_0_1px_rgb(var(--border))]' : 'text-muted hover:text-ink',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="inline-flex cursor-pointer select-none items-center gap-2 whitespace-nowrap text-sm text-muted hover:text-ink" title={hint}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={cx('relative h-5 w-9 rounded-full border transition-colors', checked ? 'border-ok/70 bg-ok/30' : 'border-border bg-panel2')}
      >
        <span className={cx('absolute top-0.5 h-3.5 w-3.5 rounded-full transition-all', checked ? 'left-[1.15rem] bg-ok' : 'left-0.5 bg-faint')} />
      </button>
      {label}
    </label>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <span className="kbd">{children}</span>;
}

// ---- panels ---------------------------------------------------------------------------------------

export function PanelHeader({ title, icon: Icon, right, sub }: { title: ReactNode; icon?: LucideIcon; right?: ReactNode; sub?: ReactNode }) {
  return (
    <div className="flex min-h-[3rem] items-center gap-3 border-b border-border px-4 py-2">
      {Icon && <Icon size={20} className="shrink-0 text-muted" strokeWidth={2.1} />}
      <div className="min-w-0">
        <div className="caps !text-[0.8125rem] text-ink/90">{title}</div>
        {sub && <div className="truncate text-xs text-muted">{sub}</div>}
      </div>
      <div className="ml-auto flex items-center gap-2">{right}</div>
    </div>
  );
}

export function Pill({ children, tone = 'neutral', className, title }: { children: ReactNode; tone?: 'neutral' | 'ok' | 'warn' | 'danger' | 'info'; className?: string; title?: string }) {
  const tones = {
    neutral: 'border-border bg-panel2 text-muted',
    ok: 'border-ok/50 bg-ok/12 text-ink',
    warn: 'border-warn/60 bg-warn/12 text-ink',
    danger: 'border-danger/60 bg-danger/12 text-ink',
    info: 'border-info/50 bg-info/12 text-ink',
  } as const;
  return (
    <span title={title} className={cx('inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border px-2 py-0.5 text-xs font-semibold', tones[tone], className)}>
      {children}
    </span>
  );
}

export function CopyHash({ value, short }: { value: string; short: string }) {
  const [done, setDone] = useState(false);
  const copy = useCallback(() => {
    void navigator.clipboard?.writeText(value).then(() => { setDone(true); window.setTimeout(() => setDone(false), 1400); }).catch(() => undefined);
  }, [value]);
  return (
    <button type="button" onClick={copy} title={`${value}\nclick to copy`} className="mono inline-flex items-center gap-1.5 rounded px-1 text-sm text-ink hover:bg-panel2">
      {short}
      {done ? <Check size={14} className="text-ok" /> : <Copy size={14} className="text-faint" />}
    </button>
  );
}

// ---- tooltip (portal, so scroll containers cannot clip it) -------------------------------------------

export function Tip({ content, children, side = 'top', wide = false }: { content: ReactNode; children: ReactNode; side?: 'top' | 'bottom'; wide?: boolean }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  useLayoutEffect(() => {
    if (!open || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    setPos({ x: r.left + r.width / 2, y: side === 'top' ? r.top : r.bottom });
  }, [open, side]);
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener('scroll', close, true);
    return () => window.removeEventListener('scroll', close, true);
  }, [open]);
  return (
    <span ref={ref} className="inline-flex" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)} onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}>
      {children}
      {open &&
        createPortal(
          <div
            role="tooltip"
            style={{ left: pos.x, top: pos.y, transform: `translate(-50%, ${side === 'top' ? 'calc(-100% - 8px)' : '8px'})` }}
            className={cx('pointer-events-none fixed z-[200] rounded-lg border border-border bg-panel2 px-3 py-2 text-sm text-ink shadow-pop animate-fade-in', wide ? 'w-[26rem]' : 'max-w-[22rem]')}
          >
            {content}
          </div>,
          document.body,
        )}
    </span>
  );
}

export function Dot({ tone }: { tone: 'ok' | 'warn' | 'danger' | 'info' | 'muted' }) {
  const c = { ok: 'text-ok', warn: 'text-warn', danger: 'text-dangerink', info: 'text-info', muted: 'text-faint' }[tone];
  return <CircleDot size={14} className={cx('shrink-0', c)} strokeWidth={3} />;
}

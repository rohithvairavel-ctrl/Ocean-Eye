import { createContext, ReactNode, useContext, useEffect, useRef, useState } from 'react';
import {
  CircleCheck,
  CircleDashed,
  Clock3,
  FlaskConical,
  Gauge,
  LoaderCircle,
  Lock,
  OctagonX,
  TriangleAlert,
  Upload,
  WifiOff,
} from 'lucide-react';
import { clock, duration, label } from './api';

/* ------------------------------------------------------------------ */
/* Clock: one shared 1 Hz tick, corrected to the backend's clock      */
/* ------------------------------------------------------------------ */

export const ClockContext = createContext<{ now: number; offset: number }>({
  now: Date.now(),
  offset: 0,
});

/** Server-corrected "now" in ms. offset = serverTime - clientTime at the last status fetch. */
export function useServerNow() {
  return useContext(ClockContext).now;
}

export function useTicker(offset: number) {
  const [now, setNow] = useState(Date.now() + offset);
  useEffect(() => {
    setNow(Date.now() + offset);
    const id = setInterval(() => setNow(Date.now() + offset), 1000);
    return () => clearInterval(id);
  }, [offset]);
  return now;
}

export function useInterval(fn: () => void, ms: number | null) {
  const saved = useRef(fn);
  saved.current = fn;
  useEffect(() => {
    if (ms === null) return;
    const id = setInterval(() => saved.current(), ms);
    return () => clearInterval(id);
  }, [ms]);
}

/* ------------------------------------------------------------------ */
/* Status vocabulary: text + icon + tone (never colour alone)         */
/* ------------------------------------------------------------------ */

type Tone = 'ok' | 'active' | 'hazard' | 'warn' | 'model' | 'forecast' | 'neutral' | 'demo';

const STATUS: Record<string, { tone: Tone; icon: any; text?: string }> = {
  LIVE: { tone: 'ok', icon: CircleCheck },
  CURRENT: { tone: 'ok', icon: CircleCheck },
  OK: { tone: 'ok', icon: CircleCheck },
  OPERATIONAL: { tone: 'ok', icon: CircleCheck },
  COMPLETE: { tone: 'ok', icon: CircleCheck },
  READY_TO_IMPORT: { tone: 'ok', icon: CircleCheck, text: 'Ready' },
  ANALYZED: { tone: 'ok', icon: CircleCheck },
  ROBUST: { tone: 'ok', icon: CircleCheck },
  SYNCING: { tone: 'active', icon: LoaderCircle },
  ACTIVE: { tone: 'active', icon: LoaderCircle },
  RUNNING: { tone: 'active', icon: LoaderCircle },
  WAITING_FOR_PASS: { tone: 'neutral', icon: Clock3, text: 'Waiting for pass' },
  NO_NEW_DATA: { tone: 'neutral', icon: Clock3, text: 'No new data' },
  WAITING: { tone: 'neutral', icon: Clock3 },
  WAITING_FOR_DATA: { tone: 'neutral', icon: Clock3, text: 'Waiting for data' },
  PENDING: { tone: 'neutral', icon: Clock3 },
  NOT_CHALLENGED: { tone: 'neutral', icon: CircleDashed, text: 'Not challenged' },
  DISCOVERED: { tone: 'neutral', icon: CircleCheck },
  STALE: { tone: 'warn', icon: TriangleAlert },
  VERY_STALE: { tone: 'warn', icon: TriangleAlert, text: 'Very stale' },
  MODERATE: { tone: 'warn', icon: TriangleAlert },
  DEGRADED: { tone: 'warn', icon: TriangleAlert },
  AUTH_REQUIRED: { tone: 'warn', icon: Lock, text: 'Auth required' },
  BLOCKED: { tone: 'warn', icon: Lock },
  PAUSED: { tone: 'neutral', icon: Clock3 },
  RATE_LIMITED: { tone: 'hazard', icon: Gauge, text: 'Rate limited' },
  OFFLINE: { tone: 'hazard', icon: WifiOff },
  ERROR: { tone: 'hazard', icon: OctagonX },
  FETCH_FAILED: { tone: 'hazard', icon: OctagonX, text: 'Fetch failed' },
  ANALYSIS_FAILED: { tone: 'hazard', icon: OctagonX, text: 'Analysis failed' },
  FAILED: { tone: 'hazard', icon: OctagonX },
  FRAGILE: { tone: 'hazard', icon: TriangleAlert },
  INDETERMINATE: { tone: 'model', icon: CircleDashed },
  DEMO: { tone: 'demo', icon: FlaskConical },
  SYNTHETIC: { tone: 'demo', icon: FlaskConical },
  UPLOADED: { tone: 'neutral', icon: Upload },
  REAL: { tone: 'ok', icon: CircleCheck },
  NOT_CONFIGURED: { tone: 'neutral', icon: CircleDashed, text: 'Not configured' },
  NOT_INSTALLED: { tone: 'neutral', icon: CircleDashed, text: 'Not installed' },
  UNAVAILABLE: { tone: 'neutral', icon: CircleDashed },
  UNKNOWN: { tone: 'neutral', icon: CircleDashed },
};

export function toneOf(state?: string | null): Tone {
  return (state && STATUS[state]?.tone) || 'neutral';
}

export function Status({
  state,
  text,
  size = 'md',
}: {
  state?: string | null;
  text?: string;
  size?: 'sm' | 'md';
}) {
  const key = state || 'UNKNOWN';
  const def = STATUS[key] || { tone: 'neutral' as Tone, icon: CircleDashed };
  const Icon = def.icon;
  const spinning = key === 'SYNCING' || key === 'RUNNING';
  return (
    <span className={`status tone-${def.tone} status-${size}`}>
      <Icon size={size === 'sm' ? 11 : 13} className={spinning ? 'spin' : ''} aria-hidden />
      <span>{text || def.text || label(key).toLowerCase().replace(/^\w/, (c) => c.toUpperCase())}</span>
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Layout primitives                                                  */
/* ------------------------------------------------------------------ */

export function PageHeader({
  eyebrow,
  title,
  question,
  actions,
  children,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  question?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="page-header-text">
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {question && <p className="page-question">{question}</p>}
        {children}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function Section({
  title,
  note,
  actions,
  children,
  surface = false,
  className = '',
  id,
}: {
  title?: ReactNode;
  note?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  surface?: boolean;
  className?: string;
  id?: string;
}) {
  return (
    <section className={`section ${surface ? 'surface' : ''} ${className}`} id={id}>
      {(title || actions) && (
        <div className="section-head">
          <div>
            {title && <h2>{title}</h2>}
            {note && <p className="section-note">{note}</p>}
          </div>
          {actions && <div className="section-actions">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Metric({
  label: name,
  value,
  unit,
  hint,
  tone,
}: {
  label: ReactNode;
  value: ReactNode;
  unit?: string;
  hint?: ReactNode;
  tone?: Tone;
}) {
  return (
    <div className={`metric ${tone ? 'tone-' + tone : ''}`}>
      <span className="metric-label">{name}</span>
      <span className="metric-value">
        {value}
        {unit && <small>{unit}</small>}
      </span>
      {hint && <span className="metric-hint">{hint}</span>}
    </div>
  );
}

export function Tag({ children, tone = 'neutral' }: { children: ReactNode; tone?: Tone }) {
  return <span className={`tag tone-${tone}`}>{children}</span>;
}

/* ------------------------------------------------------------------ */
/* Loading / empty / waiting / stale / auth / error                    */
/* ------------------------------------------------------------------ */

const STATE_ICON: Record<string, any> = {
  loading: LoaderCircle,
  empty: CircleDashed,
  waiting: Clock3,
  stale: TriangleAlert,
  auth: Lock,
  error: OctagonX,
};

export function StateBlock({
  kind,
  title,
  children,
  action,
  compact = false,
}: {
  kind: 'loading' | 'empty' | 'waiting' | 'stale' | 'auth' | 'error';
  title: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
}) {
  const Icon = STATE_ICON[kind];
  return (
    <div
      className={`state-block state-${kind} ${compact ? 'compact' : ''}`}
      role={kind === 'error' ? 'alert' : 'status'}
      aria-live="polite"
    >
      <Icon size={compact ? 16 : 20} className={kind === 'loading' ? 'spin' : ''} aria-hidden />
      <div>
        <strong>{title}</strong>
        {children && <div className="state-body">{children}</div>}
        {action && <div className="state-action">{action}</div>}
      </div>
    </div>
  );
}

export function Skeleton({ lines = 3, height }: { lines?: number; height?: number }) {
  if (height) return <div className="skeleton" style={{ height }} aria-hidden />;
  return (
    <div className="skeleton-lines" aria-hidden>
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="skeleton" style={{ width: `${90 - i * 14}%` }} />
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Live clocks                                                         */
/* ------------------------------------------------------------------ */

/** Counts down to a backend-provided instant. Reports zero; never claims success. */
export function Countdown({
  to,
  onZero,
  zeroText = 'Due — awaiting scheduler',
}: {
  to: string | null | undefined;
  onZero?: () => void;
  zeroText?: string;
}) {
  const now = useServerNow();
  const fired = useRef<string | null>(null);
  const target = to ? Date.parse(to) : NaN;
  const remaining = (target - now) / 1000;
  useEffect(() => {
    if (!to || isNaN(target)) return;
    if (remaining <= 0 && fired.current !== to) {
      fired.current = to;
      onZero?.();
    }
  }, [remaining <= 0, to]);
  if (!to || isNaN(target)) return <span className="mono muted">—</span>;
  if (remaining <= 0) return <span className="mono countdown due">{zeroText}</span>;
  return (
    <span className="mono countdown" aria-label={`in ${duration(remaining)}`}>
      {clock(remaining)}
    </span>
  );
}

/** Ticking age of a data timestamp, e.g. "07h 18m". */
export function Age({ from, prefix }: { from: string | null | undefined; prefix?: string }) {
  const now = useServerNow();
  if (!from) return <span className="mono muted">—</span>;
  const seconds = (now - Date.parse(from)) / 1000;
  return (
    <span className="mono age">
      {prefix}
      {duration(seconds)}
    </span>
  );
}

export function Bar({ value, tone = 'active' }: { value: number; tone?: Tone }) {
  return (
    <span className={`bar tone-${tone}`} aria-hidden>
      <i style={{ width: Math.max(0, Math.min(100, value)) + '%' }} />
    </span>
  );
}

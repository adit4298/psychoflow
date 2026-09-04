import { useCallback, useEffect, useRef, useState } from 'react';
import s from './CycleEditor.module.css';

export const CYCLE_S = 60;
export const AMBER_S = 4;
/** §7.3. Below this a green is not worth giving — the drag resists rather
 *  than the value silently clamping somewhere the officer can't see. */
export const MIN_GREEN_S = 7;

export interface CyclePlan { ewGreen: number; nsGreen: number; amber: number }

export const DEFAULT_PLAN: CyclePlan = {
  ewGreen: 32,
  nsGreen: CYCLE_S - 32 - AMBER_S * 2,
  amber: AMBER_S,
};

interface Props {
  plan: CyclePlan;
  onChange: (plan: CyclePlan) => void;
  /** 0–1 through the current cycle. Drives the sweeping playhead. */
  progress: number;
  disabled?: boolean;
  label: string;
}

type Handle = 'ew' | 'ns';

/** The two greens share the 52 s the ambers leave. Dragging handle A sets the
 *  E–W green and N–S absorbs the difference; handle B does the reverse. */
function withGreen(plan: CyclePlan, which: Handle, seconds: number): CyclePlan {
  const budget = CYCLE_S - plan.amber * 2;
  const max = budget - MIN_GREEN_S;
  const value = Math.round(Math.min(max, Math.max(MIN_GREEN_S, seconds)));
  return which === 'ew'
    ? { ...plan, ewGreen: value, nsGreen: budget - value }
    : { ...plan, nsGreen: value, ewGreen: budget - value };
}

export function CycleEditor({ plan, onChange, progress, disabled, label }: Props) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState<Handle | null>(null);

  const budget = CYCLE_S - plan.amber * 2;
  const pct = (seconds: number) => (seconds / CYCLE_S) * 100;

  // Boundary positions in seconds from the start of the cycle.
  const bEw = plan.ewGreen;
  const bNs = plan.ewGreen + plan.amber + plan.nsGreen;

  const secondsAt = useCallback((clientX: number) => {
    const el = trackRef.current;
    if (!el) return 0;
    const { left, width } = el.getBoundingClientRect();
    return ((clientX - left) / width) * CYCLE_S;
  }, []);

  const onPointerDown = (which: Handle) => (e: React.PointerEvent<HTMLDivElement>) => {
    if (disabled) return;
    e.preventDefault();
    // Capture keeps the drag alive when the pointer leaves the 18px handle,
    // which it does immediately. It can throw if the pointer is already gone
    // (or is synthetic), and losing capture is survivable — the window-level
    // pointerup below still ends the drag — so never let it break the press.
    try {
      (e.target as HTMLElement).setPointerCapture(e.pointerId);
    } catch { /* no capture; the drag still tracks while the button is down */ }
    setDragging(which);
  };

  const onPointerMove = (which: Handle) => (e: React.PointerEvent<HTMLDivElement>) => {
    if (dragging !== which) return;
    const at = secondsAt(e.clientX);
    // Handle A sits at the end of the E-W green, so its position IS that
    // green's length. Handle B sits at the end of the N-S green, so subtract
    // everything before it. withGreen() rounds to 1 s and clamps.
    onChange(withGreen(plan, which, which === 'ew' ? at : at - bEw - plan.amber));
  };

  const endDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).hasPointerCapture?.(e.pointerId)) {
      (e.target as HTMLElement).releasePointerCapture(e.pointerId);
    }
    setDragging(null);
  };

  const onKeyDown = (which: Handle) => (e: React.KeyboardEvent) => {
    if (disabled) return;
    const current = which === 'ew' ? plan.ewGreen : plan.nsGreen;
    const step = e.shiftKey ? 5 : 1;
    let next: number | null = null;
    if (e.key === 'ArrowRight' || e.key === 'ArrowUp') next = current + step;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') next = current - step;
    if (e.key === 'Home') next = MIN_GREEN_S;
    if (e.key === 'End') next = budget - MIN_GREEN_S;
    if (next === null) return;
    e.preventDefault();
    onChange(withGreen(plan, which, next));
  };

  // A pointer released outside the window would otherwise leave a stuck drag.
  useEffect(() => {
    if (!dragging) return;
    const clear = () => setDragging(null);
    window.addEventListener('pointerup', clear);
    window.addEventListener('pointercancel', clear);
    return () => {
      window.removeEventListener('pointerup', clear);
      window.removeEventListener('pointercancel', clear);
    };
  }, [dragging]);

  const handle = (which: Handle, atSeconds: number, value: number) => (
    <div
      key={which}
      role="slider"
      tabIndex={disabled ? -1 : 0}
      aria-label={`${which === 'ew' ? 'East\u2013west' : 'North\u2013south'} green, ${label}`}
      aria-valuemin={MIN_GREEN_S}
      aria-valuemax={budget - MIN_GREEN_S}
      aria-valuenow={value}
      aria-valuetext={`${value} seconds`}
      aria-disabled={disabled || undefined}
      className={s.handle}
      data-dragging={dragging === which || undefined}
      style={{ left: `${pct(atSeconds)}%` }}
      onPointerDown={onPointerDown(which)}
      onPointerMove={onPointerMove(which)}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onKeyDown={onKeyDown(which)}
    >
      <span className={s.grip} aria-hidden="true" />
    </div>
  );

  return (
    <div className={s.wrap} data-disabled={disabled || undefined}>
      <div className={s.track} ref={trackRef}>
        <span className={s.seg} data-tone="go"
              style={{ left: 0, width: `${pct(plan.ewGreen)}%` }} />
        <span className={s.seg} data-tone="wait"
              style={{ left: `${pct(bEw)}%`, width: `${pct(plan.amber)}%` }} />
        <span className={s.seg} data-tone="go"
              style={{ left: `${pct(bEw + plan.amber)}%`, width: `${pct(plan.nsGreen)}%` }} />
        <span className={s.seg} data-tone="wait"
              style={{ left: `${pct(bNs)}%`, width: `${pct(plan.amber)}%` }} />

        <span className={s.segLabel} style={{ left: 0, width: `${pct(plan.ewGreen)}%` }}>
          E&ndash;W
        </span>
        <span className={s.segLabel}
              style={{ left: `${pct(bEw + plan.amber)}%`, width: `${pct(plan.nsGreen)}%` }}>
          N&ndash;S
        </span>

        {handle('ew', bEw, plan.ewGreen)}
        {handle('ns', bNs, plan.nsGreen)}

        {/* Full-track width with a 2px left border, so translateX() in percent
            is a percentage OF THE TRACK and the sweep stays on transform only.
            It carries information, so it keeps moving under reduced motion. */}
        <span
          className={s.playhead}
          data-motion="informational"
          style={{ transform: `translateX(${(progress * 100).toFixed(2)}%)` }}
          aria-hidden="true"
        />
      </div>

      <p className={`mono ${s.readout}`}>
        E&ndash;W {plan.ewGreen}s <i>·</i> amber {plan.amber}s <i>·</i>{' '}
        N&ndash;S {plan.nsGreen}s <i>·</i> amber {plan.amber}s
      </p>
    </div>
  );
}

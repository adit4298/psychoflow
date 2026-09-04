import { useMemo } from 'react';
import { Tooltip } from '@base-ui/react/tooltip';
import type { Frame, JunctionId } from '../data/types';
import { JUNCTION_IDS } from '../data/types';
import { reasonLabel, SIGNAL_GLYPH } from '../data/format';
import s from './SignalTimeline.module.css';

export const WINDOW_S = 90;

interface Block {
  key: string;
  start: number;
  end: number;
  phase: number;
  /** The decision that produced this run, when one landed on it. */
  reason?: string;
}

/** Collapse the per-frame `current_phase` into runs. A row of 18 identical
 *  5-second cells reads as noise; four blocks read as a cycle. */
function blocksFor(history: Frame[], j: JunctionId, now: number): Block[] {
  const from = now - WINDOW_S;
  const out: Block[] = [];
  for (const f of history) {
    if (f.sim_time < from - 5) continue;
    const phase = f.digital_twin.junctions[j]?.current_phase;
    if (phase === undefined) continue;
    const last = out[out.length - 1];
    if (last && last.phase === phase) {
      last.end = f.sim_time;
    } else {
      out.push({ key: `${j}-${f.sim_time}`, start: f.sim_time - 5, end: f.sim_time, phase });
    }
    if (f.decision?.junction_id === j) {
      out[out.length - 1].reason = f.decision.reason;
    }
  }
  return out;
}

/** Colour reads the CORRIDOR's through-movement, which is what an officer on
 *  a linear corridor is watching. Phase 0 greens east-west, so the corridor is
 *  flowing; an even phase above 0 greens the cross street, so the corridor is
 *  held; odd phases are netconvert's yellows. (The 5 s decision grid rarely
 *  samples one, so amber blocks are legitimately uncommon.) */
function toneOf(phase: number): 'go' | 'wait' | 'stop' {
  if (phase === 0) return 'go';
  if (phase % 2 === 1) return 'wait';
  return 'stop';
}

const TONE_WORD = {
  go: 'corridor green · east–west',
  wait: 'changing',
  stop: 'corridor held · cross street served',
};

export function SignalTimeline({ history }: { history: Frame[] }) {
  const now = history.length ? history[history.length - 1].sim_time : 0;

  const rows = useMemo(
    () => JUNCTION_IDS.map((j) => ({ j, blocks: blocksFor(history, j, now) })),
    [history, now],
  );

  const pct = (t: number) => ((t - (now - WINDOW_S)) / WINDOW_S) * 100;

  return (
    <Tooltip.Provider delay={120} closeDelay={60}>
      <div className={s.wrap}>
        {rows.map(({ j, blocks }) => (
          <div className={s.row} key={j}>
            <span className={s.rowLabel}>{j}</span>
            <div className={s.track}>
              {blocks.length === 0 && <span className={s.rowEmpty}>waiting for frames</span>}
              {blocks.map((b) => {
                const left = Math.max(0, pct(b.start));
                const right = Math.min(100, pct(b.end));
                const width = right - left;
                if (width <= 0) return null;
                const tone = toneOf(b.phase);
                return (
                  <Tooltip.Root key={b.key}>
                    <Tooltip.Trigger
                      render={
                        <button
                          type="button"
                          className={s.block}
                          data-tone={tone}
                          style={{ left: `${left}%`, width: `${width}%` }}
                          aria-label={`${j} phase ${b.phase}, ${TONE_WORD[tone]}`}
                        />
                      }
                    />
                    <Tooltip.Portal>
                      <Tooltip.Positioner side="top" sideOffset={8}>
                        <Tooltip.Popup className={s.tip}>
                          <span className={s.tipHead}>
                            {SIGNAL_GLYPH[tone]} {j} · phase {b.phase}
                          </span>
                          <span className={s.tipBody}>
                            {b.reason ? reasonLabel(b.reason) : TONE_WORD[tone]}
                          </span>
                          <span className={`mono ${s.tipMeta}`}>
                            {Math.round(b.end - b.start)}s held
                          </span>
                        </Tooltip.Popup>
                      </Tooltip.Positioner>
                    </Tooltip.Portal>
                  </Tooltip.Root>
                );
              })}
              {/* The "now" edge. Sits flush right; it is the axis, not motion. */}
              <span className={s.now} aria-hidden="true" />
            </div>
          </div>
        ))}

        <div className={s.axis} aria-hidden="true">
          <span className={s.rowLabel} />
          <div className={s.axisTicks}>
            <span>−90s</span><span>−60s</span><span>−30s</span><span data-now>now</span>
          </div>
        </div>

        {/* Signal state is never colour-only — each key carries its glyph (§10). */}
        <ul className={s.legend}>
          {(['go', 'wait', 'stop'] as const).map((tone) => (
            <li key={tone} className={s.legendItem} data-tone={tone}>
              <span aria-hidden="true">{SIGNAL_GLYPH[tone]}</span>
              {TONE_WORD[tone]}
            </li>
          ))}
        </ul>
      </div>
    </Tooltip.Provider>
  );
}

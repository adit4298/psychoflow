import { useMemo } from 'react';
import type { Frame, JunctionId } from '../data/types';
import { reasonClass, reasonLabel, simClock, SIGNAL_GLYPH } from '../data/format';
import s from './PhaseHistory.module.css';

const WINDOW_S = 90;

/** The Overview timeline's single row, given room to breathe: each held phase
 *  gets its own labelled band with the decision that started it. */
export function PhaseHistory({ history, junction }: { history: Frame[]; junction: JunctionId }) {
  const now = history.length ? history[history.length - 1].sim_time : 0;

  const runs = useMemo(() => {
    const out: { start: number; end: number; phase: number; reason?: string }[] = [];
    for (const f of history) {
      if (f.sim_time < now - WINDOW_S - 5) continue;
      const phase = f.digital_twin.junctions[junction]?.current_phase;
      if (phase === undefined) continue;
      const last = out[out.length - 1];
      if (last && last.phase === phase) last.end = f.sim_time;
      else out.push({ start: f.sim_time - 5, end: f.sim_time, phase });
      if (f.decision?.junction_id === junction) out[out.length - 1].reason = f.decision.reason;
    }
    return out.reverse();
  }, [history, junction, now]);

  if (runs.length === 0) {
    return <p className={s.empty}>Waiting for enough frames to draw a history.</p>;
  }

  const longest = Math.max(...runs.map((r) => r.end - r.start), 1);

  return (
    <ul className={s.list}>
      {runs.map((r) => {
        const tone = r.phase === 0 ? 'go' : r.phase % 2 === 1 ? 'wait' : 'stop';
        const held = Math.round(r.end - r.start);
        return (
          <li className={s.row} key={`${r.start}`}>
            <span className={`mono ${s.time}`}>{simClock(r.start)}</span>
            <span className={s.bar}>
              <span className={s.fill} data-tone={tone}
                    style={{ width: `${Math.max(6, (held / longest) * 100)}%` }} />
            </span>
            <span className={s.held}>
              <span className={s.glyph} data-tone={tone} aria-hidden="true">
                {SIGNAL_GLYPH[tone]}
              </span>
              <span className="mono">{held}s</span>
            </span>
            <span className={s.reason} data-class={r.reason ? reasonClass(r.reason) : 'normal'}>
              {r.reason ? reasonLabel(r.reason) : `phase ${r.phase}`}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

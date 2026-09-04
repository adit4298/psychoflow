import { Info } from 'lucide-react';
import { JunctionControlCard } from '../components/JunctionControlCard';
import { CYCLE_S } from '../components/CycleEditor';
import { Assistant } from '../components/Assistant';
import { useStore } from '../store/store';
import { useCommands } from '../assistant/useCommands';
import { JUNCTION_IDS } from '../data/types';
import s from './Manual.module.css';

export function Manual() {
  const frame = useStore((st) => st.frame);
  const mode = useStore((st) => st.mode);
  const { apply } = useCommands();
  const manual = mode === 'manual';

  const untilBoundary = frame
    ? Math.ceil(CYCLE_S - (frame.sim_time % CYCLE_S))
    : CYCLE_S;

  return (
    <div className={s.screen}>
      <div className={s.banner} data-manual={manual || undefined} role="status">
        <Info size={16} strokeWidth={2} aria-hidden="true" />
        {manual ? (
          <>
            <span className={s.bannerText}>
              <strong>You have control</strong> — Auto is paused. Changes take
              effect on the next 60-second cycle.
            </span>
            <span className={`mono ${s.countdown}`} data-motion="informational">
              {String(untilBoundary).padStart(2, '0')}s
            </span>
          </>
        ) : (
          <>
            <span className={s.bannerText}>
              Auto is driving the corridor. Switch to Manual to take control.
            </span>
            <button
              type="button"
              className={s.take}
              onClick={() => apply({ fn: 'set_mode', mode: 'manual' })}
            >
              Switch to Manual
            </button>
          </>
        )}
      </div>

      <div className={s.grid}>
        <div className={s.cards}>
          {JUNCTION_IDS.map((j) => (
            <JunctionControlCard key={j} junction={j} frame={frame} disabled={!manual} />
          ))}
        </div>
        <aside className={s.side}>
          <Assistant />
          <p className={s.note}>
            Cycle plans are staged locally — the backend control API does not
            expose a cycle-plan call yet, so the plan is shown as it would run
            and sent once that lands.
          </p>
        </aside>
      </div>
    </div>
  );
}

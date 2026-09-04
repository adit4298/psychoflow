import { useState } from 'react';
import { ChevronRight } from 'lucide-react';
import type { Decision } from '../data/types';
import { APPROACH_SHORT, reasonClass, reasonLabel, simClock } from '../data/format';
import s from './DecisionRow.module.css';

function ScoreBars({ scores, selected }: { scores: Record<string, number>; selected: number }) {
  const entries = Object.entries(scores);
  if (entries.length === 0) {
    return <p className={s.none}>No alternatives were scored — the policy chose directly.</p>;
  }
  const max = Math.max(...entries.map(([, v]) => Math.abs(v)), 1);
  return (
    <ul className={s.bars}>
      {entries.map(([key, value]) => {
        const isChosen = key === `phase_${selected}`;
        return (
          <li key={key} className={s.bar} data-chosen={isChosen || undefined}>
            <span className={s.barKey}>{key.replace('phase_', 'Phase ')}</span>
            <span className={s.barTrack}>
              <span className={s.barFill}
                    style={{ width: `${(Math.abs(value) / max) * 100}%` }} />
            </span>
            <span className={`mono ${s.barVal}`}>{value.toFixed(2)}</span>
          </li>
        );
      })}
    </ul>
  );
}

export function DecisionRow({ decision }: { decision: Decision }) {
  const [open, setOpen] = useState(false);
  const cls = reasonClass(decision.reason);

  return (
    <li className={s.row} data-open={open || undefined}>
      <button type="button" className={s.summary} onClick={() => setOpen((o) => !o)}
              aria-expanded={open}>
        <ChevronRight size={14} strokeWidth={2.2} className={s.chevron} aria-hidden="true" />
        <span className={s.dot} data-class={cls} aria-hidden="true" />
        <span className={`mono ${s.time}`}>{simClock(decision.sim_time)}</span>
        <span className={s.junction}>{decision.junction_id}</span>
        <span className={`mono ${s.phase}`}>P{decision.phase_selected}</span>
        <span className={`mono ${s.lane}`}>{decision.lane_id}</span>
        <span className={s.dir}>{APPROACH_SHORT[decision.direction] ?? decision.direction}</span>
        <span className={s.reason} data-class={cls}>{reasonLabel(decision.reason)}</span>
      </button>

      {open && (
        <div className={s.detail}>
          <div className={s.detailCol}>
            <p className="label">Score breakdown</p>
            <ul className={s.breakdown}>
              {Object.entries(decision.score_breakdown).map(([k, v]) => (
                <li key={k}>
                  <span>{k.replace(/_/g, ' ')}</span>
                  <span className="mono">{v.toFixed(2)}</span>
                </li>
              ))}
            </ul>
          </div>
          <div className={s.detailCol}>
            <p className="label">Alternatives considered</p>
            <ScoreBars scores={decision.alternative_scores} selected={decision.phase_selected} />
          </div>
        </div>
      )}
    </li>
  );
}

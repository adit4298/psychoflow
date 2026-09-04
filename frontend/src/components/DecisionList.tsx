import { useMemo } from 'react';
import type { Frame } from '../data/types';
import { APPROACH_SHORT, reasonClass, reasonLabel, simClock } from '../data/format';
import s from './DecisionList.module.css';

/** Newest first. Successive frames repeat the same decision until a new one
 *  lands, so rows are keyed on the decision's own sim_time. */
export function DecisionList({ history, limit = 40 }: { history: Frame[]; limit?: number }) {
  const rows = useMemo(() => {
    const seen = new Set<string>();
    const out = [];
    for (let i = history.length - 1; i >= 0 && out.length < limit; i--) {
      const d = history[i].decision;
      if (!d) continue;
      const key = `${d.junction_id}-${d.sim_time}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ key, d });
    }
    return out;
  }, [history, limit]);

  if (rows.length === 0) {
    return <p className={s.empty}>No decisions yet — the first lands within a few seconds.</p>;
  }

  return (
    <ul className={s.list}>
      {rows.map(({ key, d }) => (
        <li className={s.row} key={key}>
          <span className={s.dot} data-class={reasonClass(d.reason)} aria-hidden="true" />
          <span className={`mono ${s.time}`}>{simClock(d.sim_time)}</span>
          <span className={s.junction}>{d.junction_id}</span>
          <span className={`mono ${s.phase}`}>
            P{d.phase_selected}
            <span className={s.dir}>{APPROACH_SHORT[d.direction] ?? '·'}</span>
          </span>
          <span className={s.reason} data-class={reasonClass(d.reason)}>
            {reasonLabel(d.reason)}
          </span>
        </li>
      ))}
    </ul>
  );
}

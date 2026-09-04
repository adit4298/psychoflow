import { useMemo, useState } from 'react';
import { Virtuoso } from 'react-virtuoso';
import { Panel } from '../components/Panel';
import { DecisionRow } from '../components/DecisionRow';
import { useHistory, useLatestPredictions, useResponderMessages } from '../store/store';
import type { Decision, JunctionId } from '../data/types';
import { JUNCTION_IDS } from '../data/types';
import {
  clearanceText, isClaimable, reasonClass, simClock, type ReasonClass,
} from '../data/format';
import s from './Logs.module.css';

const CLASSES: { value: ReasonClass | 'all'; label: string }[] = [
  { value: 'all', label: 'All reasons' },
  { value: 'normal', label: 'Demand' },
  { value: 'starvation', label: 'Fairness' },
  { value: 'emergency', label: 'Emergency' },
  { value: 'manual', label: 'Operator' },
];

const RANGES = [
  { value: 0, label: 'Whole session' },
  { value: 300, label: 'Last 5 min' },
  { value: 60, label: 'Last minute' },
];

export function Logs() {
  const history = useHistory();
  const predictions = useLatestPredictions();
  const responder = useResponderMessages();

  const [junction, setJunction] = useState<JunctionId | 'all'>('all');
  const [cls, setCls] = useState<ReasonClass | 'all'>('all');
  const [range, setRange] = useState(0);

  const all = useMemo(() => {
    const seen = new Set<string>();
    const out: Decision[] = [];
    for (let i = history.length - 1; i >= 0; i--) {
      const d = history[i].decision;
      if (!d) continue;
      const key = `${d.junction_id}-${d.sim_time}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(d);
    }
    return out;
  }, [history]);

  const now = history.length ? history[history.length - 1].sim_time : 0;

  const rows = useMemo(() => all.filter((d) => {
    if (junction !== 'all' && d.junction_id !== junction) return false;
    if (cls !== 'all' && reasonClass(d.reason) !== cls) return false;
    if (range && d.sim_time < now - range) return false;
    return true;
  }), [all, junction, cls, range, now]);

  return (
    <div className={s.screen}>
      <div className={s.grid}>
        <Panel
          flush
          title="Decision log"
          note="Every decision this session. Expand a row for its scoring."
          aside={<span className={`mono ${s.count}`}>{rows.length}</span>}
        >
          <div className={s.filters}>
            <select className={s.filter} value={junction} aria-label="Filter by junction"
                    onChange={(e) => setJunction(e.target.value as JunctionId | 'all')}>
              <option value="all">All junctions</option>
              {JUNCTION_IDS.map((j) => <option key={j} value={j}>{j}</option>)}
            </select>
            <select className={s.filter} value={cls} aria-label="Filter by reason"
                    onChange={(e) => setCls(e.target.value as ReasonClass | 'all')}>
              {CLASSES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
            <select className={s.filter} value={range} aria-label="Filter by time range"
                    onChange={(e) => setRange(Number(e.target.value))}>
              {RANGES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </select>
          </div>

          {rows.length === 0 ? (
            <p className={s.empty}>
              {all.length === 0
                ? 'No decisions recorded yet.'
                : 'No decision matches these filters.'}
            </p>
          ) : (
            <Virtuoso
              className={s.virtual}
              /* Fits the content until it would outgrow the viewport, so a
                 short session doesn't leave a tall empty panel. */
              style={{ height: Math.min(620, rows.length * 42 + 8) }}
              data={rows}
              components={{ List: ListShell }}
              itemContent={(_, d) => <DecisionRow decision={d} />}
            />
          )}
        </Panel>

        <div className={s.side}>
          <Panel title="Predictions" note="Spillover forecast between junctions">
            {predictions.length === 0 ? (
              <p className={s.quiet}>No forecast on the stream yet.</p>
            ) : (
              <ul className={s.feed}>
                {predictions.map((p) => (
                  <li key={`${p.from_junction}${p.to_junction}`} className={s.pred}>
                    <span className={s.predRoute}>
                      {p.from_junction} <i aria-hidden="true">→</i> {p.to_junction}
                    </span>
                    <span className={`mono ${s.predDelta}`}
                          data-sign={p.predicted_queue_delta >= 0 ? 'up' : 'down'}>
                      {p.predicted_queue_delta >= 0 ? '+' : '−'}
                      {Math.abs(p.predicted_queue_delta).toFixed(0)}
                    </span>
                    <span className={s.predMeta}>
                      queue over <span className="mono">{p.horizon_s}s</span> ·{' '}
                      <span className="mono">{Math.round(p.confidence * 100)}%</span> confidence
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel title="Responder messages" note="Emergency clearance events">
            {responder.length === 0 ? (
              <p className={s.quiet}>No clearance event yet this session.</p>
            ) : (
              <ul className={s.feed}>
                {responder.map((m) => (
                  <li key={`${m.junction_id}${m.lane_id}${m.sim_time}`} className={s.msg}>
                    <span className={s.msgHead}>
                      <span className={s.msgJ}>{m.junction_id}</span>
                      <span className={`mono ${s.msgTime}`}>{simClock(m.sim_time)}</span>
                      <span className={s.msgSrc} data-src={m.trigger_source}>
                        {m.trigger_source}
                      </span>
                    </span>
                    <span className={s.msgClear} data-claim={isClaimable(m) || undefined}>
                      {clearanceText(m)}
                    </span>
                    {/* §6: baseline is a labelled model estimate, and the 100 %
                        improvement on operator rows is never shown as a claim. */}
                    <span className={s.msgBaseline}>
                      baseline <span className="mono">
                        ~{m.baseline_clearance_time_s.toFixed(0)}s
                      </span>{' '}
                      {m.baseline_is_estimate && <i>estimated, worst case</i>}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}

/** Virtuoso renders a plain div by default; the rows are <li>, so give them a
 *  list to live in rather than nesting invalid markup. */
const ListShell = ({ style, children, ...rest }: React.HTMLAttributes<HTMLUListElement>) => (
  <ul style={style} className={s.list} {...rest}>{children}</ul>
);

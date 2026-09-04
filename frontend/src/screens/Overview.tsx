import { useMemo } from 'react';
import { Panel } from '../components/Panel';
import { StatTile } from '../components/StatTile';
import { SignalTimeline } from '../components/SignalTimeline';
import { CorridorMap } from '../components/CorridorMap';
import { DecisionList } from '../components/DecisionList';
import { DetectionFeed } from '../components/DetectionFeed';
import { Assistant } from '../components/Assistant';
import { useHistory, useLatestAlerts, useStore } from '../store/store';
import { sanitize } from '../data/format';
import s from './Overview.module.css';

/** ~5 min of sim time back, at 5 s per frame. */
const DELTA_FRAMES = 60;
const SPARK_FRAMES = 30;

export function Overview() {
  const history = useHistory();
  const frame = useStore((st) => st.frame);
  const alerts = useLatestAlerts();

  const series = useMemo(() => {
    const tail = history.slice(-SPARK_FRAMES);
    const pick = (f: (typeof history)[number]) => f.metrics_snapshot;
    return {
      wait: tail.map((f) => pick(f).mean_wait_max),
      variance: tail.map((f) => pick(f).wait_time_variance_across_lanes),
      throughput: tail.map((f) => pick(f).throughput_total),
      starvation: tail.map((f) => pick(f).starvation_events_total),
    };
  }, [history]);

  const prev = history.length > DELTA_FRAMES
    ? history[history.length - 1 - DELTA_FRAMES].metrics_snapshot
    : undefined;
  const m = frame?.metrics_snapshot;

  return (
    <div className={s.screen}>
      {/* Band A */}
      <div className={s.tiles}>
        <StatTile label="Mean wait" unit="s" decimals={1} lowerIsBetter
                  value={m?.mean_wait_max ?? 0} previous={prev?.mean_wait_max}
                  history={series.wait} />
        <StatTile label="Fairness · wait variance" decimals={1} lowerIsBetter
                  value={m?.wait_time_variance_across_lanes ?? 0}
                  previous={prev?.wait_time_variance_across_lanes}
                  history={series.variance} />
        <StatTile label="Throughput · cleared"
                  value={m?.throughput_total ?? 0} previous={prev?.throughput_total}
                  history={series.throughput} />
        <StatTile label="Starvation events" lowerIsBetter
                  value={m?.starvation_events_total ?? 0}
                  previous={prev?.starvation_events_total}
                  history={series.starvation}
                  alarm={(m?.starvation_events_total ?? 0) > 0} />
      </div>

      {/* Band B */}
      <div className={s.bandB}>
        <Panel
          title="Signal timeline"
          note="Last 90 seconds of sim time. Hover a block for the decision behind it."
        >
          <SignalTimeline history={history} />
          {frame?.narration && (
            <p className={s.narration}>{sanitize(frame.narration)}</p>
          )}
        </Panel>

        <Panel title="Corridor" note="J1 → J2 → J3, live V2X positions">
          <CorridorMap frame={frame} />
        </Panel>
      </div>

      {/* Band C — three cards directly on --ground, never wrapped in a panel */}
      <div className={s.bandC}>
        <Panel title="Recent decisions" note="Newest first">
          <DecisionList history={history} limit={24} />
        </Panel>

        <Panel
          title="Live detection"
          aside={<span className={s.tag}>J2</span>}
          note="Per-approach counts from the vision envelope"
        >
          <DetectionFeed
            frame={frame}
            junction="J2"
            alert={alerts.find((a) => a.junction === 'J2')}
          />
        </Panel>

        <Assistant />
      </div>
    </div>
  );
}

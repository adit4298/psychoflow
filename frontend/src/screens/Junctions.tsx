import { useMemo, useState } from 'react';
import { Tabs } from '@base-ui/react/tabs';
import { Panel } from '../components/Panel';
import { LaneTable } from '../components/LaneTable';
import { DetectionFeed } from '../components/DetectionFeed';
import { PhaseHistory } from '../components/PhaseHistory';
import { useHistory, useLatestAlerts, useStore } from '../store/store';
import { JUNCTION_IDS, type JunctionId } from '../data/types';
import { lanesOf, simClock } from '../data/format';
import s from './Junctions.module.css';

export function Junctions() {
  const frame = useStore((st) => st.frame);
  const history = useHistory();
  const alerts = useLatestAlerts();
  const [active, setActive] = useState<JunctionId>('J2');

  const lanes = useMemo(
    () => (frame ? lanesOf(frame, active) : []),
    [frame, active],
  );

  const incidents = (frame?.digital_twin.active_incidents ?? [])
    .filter((i) => i.location.junction_id === active);
  const alert = alerts.find((a) => a.junction === active);
  const starved = lanes.filter((l) => l.starvation_flag).length;

  return (
    <Tabs.Root
      value={active}
      onValueChange={(v: string | null) => v && setActive(v as JunctionId)}
      className={s.screen}
    >
      <Tabs.List className={s.tabs}>
        {JUNCTION_IDS.map((j) => (
          <Tabs.Tab key={j} value={j} className={s.tab}>{j}</Tabs.Tab>
        ))}
        <Tabs.Indicator className={s.indicator} />
      </Tabs.List>

      <Tabs.Panel value={active} className={s.panelWrap} keepMounted={false}>
        <div className={s.grid}>
          <Panel
            flush
            title={`${active} lanes`}
            note={starved > 0
              ? `${starved} lane${starved > 1 ? 's' : ''} past the starvation threshold`
              : 'No lane is starved'}
            aside={<span className={`mono ${s.count}`}>{lanes.length}</span>}
            className={s.wide}
          >
            <LaneTable lanes={lanes} />
          </Panel>

          <Panel title="Phase history" note="Last 90 seconds at this junction">
            <PhaseHistory history={history} junction={active} />
          </Panel>

          <Panel title="Detection feed" note={`Vision envelope at ${active}`}>
            <DetectionFeed frame={frame} junction={active} alert={alert} />
          </Panel>

          <Panel title="Active incidents"
                 note={incidents.length ? undefined : 'Nothing reported here'}>
            {incidents.length === 0 ? (
              <p className={s.quiet}>
                No incident is active at {active}. Reports arrive from the
                incident intake feed.
              </p>
            ) : (
              <ul className={s.incidents}>
                {incidents.map((i) => (
                  <li key={i.incident_id} className={s.incident}>
                    <span className={s.incType}>{i.type}</span>
                    <span className={s.incSev} data-sev={i.severity}>{i.severity}</span>
                    <span className={`mono ${s.incLane}`}>{i.location.lane_id}</span>
                    <span className={s.incMeta}>
                      reported {simClock(i.reported_at_sim_time)} ·{' '}
                      <span className="mono">{Math.round(i.estimated_duration_s)}s</span> est.
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </Tabs.Panel>
    </Tabs.Root>
  );
}

import type { Lane } from '../data/types';
import { TypeMix } from './TypeMix';
import { APPROACH_SHORT } from '../data/format';
import s from './LaneTable.module.css';

export function LaneTable({ lanes }: { lanes: Lane[] }) {
  if (lanes.length === 0) {
    return <p className={s.empty}>No lanes reported for this junction yet.</p>;
  }

  return (
    <div className={s.scroll}>
      <table className={s.table}>
        <thead>
          <tr>
            <th scope="col">Lane</th>
            <th scope="col">App.</th>
            <th scope="col" className={s.num}>Veh.</th>
            <th scope="col" className={s.num}>Halted</th>
            <th scope="col">Mix</th>
            <th scope="col" className={s.num}>Wait</th>
            <th scope="col" className={s.num}>Max</th>
            <th scope="col">Starved</th>
          </tr>
        </thead>
        <tbody>
          {lanes.map((lane) => (
            <tr key={lane.lane_id} data-starved={lane.starvation_flag || undefined}>
              <td className="mono">{lane.lane_id}</td>
              <td>{APPROACH_SHORT[lane.approach] ?? lane.approach}</td>
              <td className={`mono ${s.num}`}>{lane.vehicle_count}</td>
              <td className={`mono ${s.num}`}>{lane.halted_count}</td>
              <td><TypeMix mix={lane.type_composition} /></td>
              <td className={`mono ${s.num}`}>{lane.wait_time_current.toFixed(1)}s</td>
              <td className={`mono ${s.num}`}>{lane.wait_time_max_single_vehicle.toFixed(1)}s</td>
              <td>
                {lane.starvation_flag ? (
                  <span className={s.starved}>
                    <i className={s.pip} aria-hidden="true" /> starved
                  </span>
                ) : (
                  <span className={s.ok}>ok</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

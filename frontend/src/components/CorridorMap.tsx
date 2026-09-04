import type { Frame } from '../data/types';
import { JUNCTION_IDS } from '../data/types';
import s from './CorridorMap.module.css';

/* Netconvert-shifted frame — J1 sits at (150,150), not the authored (0,0).
   These are used as-is; nothing here re-derives a coordinate. */
const VIEW_W = 920;
const VIEW_H = 300;
const JUNCTION_XY: Record<string, [number, number]> = {
  J1: [150, 150], J2: [450, 150], J3: [750, 150],
};

export function CorridorMap({ frame }: { frame: Frame | null }) {
  const vehicles = frame?.digital_twin.v2x_messages_recent ?? [];
  const adjacency = frame?.digital_twin.corridor_adjacency ?? [];
  const phases = frame?.digital_twin.junctions;

  return (
    <div className={s.wrap}>
      <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} className={s.svg} role="img"
           aria-label="Top-down corridor schematic with live vehicle positions">
        {/* cross streets */}
        {JUNCTION_IDS.map((j) => {
          const [x] = JUNCTION_XY[j];
          return <line key={`c${j}`} x1={x} y1={40} x2={x} y2={260}
                       className={s.cross} />;
        })}

        {/* corridor links, drawn from corridor_adjacency rather than assumed */}
        {adjacency.map(([a, b]) => (
          <line key={`${a}${b}`}
                x1={JUNCTION_XY[a][0]} y1={JUNCTION_XY[a][1]}
                x2={JUNCTION_XY[b][0]} y2={JUNCTION_XY[b][1]}
                className={s.link} />
        ))}
        <line x1={30} y1={150} x2={150} y2={150} className={s.link} />
        <line x1={750} y1={150} x2={890} y2={150} className={s.link} />

        {vehicles.map((v, i) => {
          const isAmbulance = v.vehicle_id.includes('amb');
          return (
            <circle
              // v2x_messages_recent is a MESSAGE list, not a vehicle list:
              // 4 of the 200 recorded frames report the same vehicle_id twice
              // (two roadside units seeing it). The index disambiguates.
              key={`${v.vehicle_id}-${i}`}
              cx={v.position.x}
              cy={v.position.y}
              r={isAmbulance ? 6 : 3.4}
              className={isAmbulance ? s.ambulance : s.vehicle}
              opacity={v.dropped ? 0.35 : 1}
            />
          );
        })}

        {JUNCTION_IDS.map((j) => {
          const [x, y] = JUNCTION_XY[j];
          const phase = phases?.[j]?.current_phase ?? 0;
          return (
            <g key={j}>
              <rect x={x - 15} y={y - 15} width={30} height={30} rx={9}
                    className={s.node} data-phase={phase === 0 ? 'go' : 'stop'} />
              <text x={x} y={y + 4} className={s.nodeLabel}>{j}</text>
            </g>
          );
        })}
      </svg>

      <p className={s.legend}>
        <span className={s.key}><i className={s.dotVehicle} />{vehicles.length} vehicles</span>
        <span className={s.key}><i className={s.dotAmb} />ambulance</span>
        <span className={s.keyMuted}>V2X shaped data</span>
      </p>
    </div>
  );
}

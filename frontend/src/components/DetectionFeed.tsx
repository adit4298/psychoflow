import { useMemo } from 'react';
import { TriangleAlert } from 'lucide-react';
import type { Frame, IncidentAlert, JunctionId, VisionLane } from '../data/types';
import { APPROACH_SHORT } from '../data/format';
import s from './DetectionFeed.module.css';

/** Deterministic pseudo-boxes derived from the lane's own counts, so the frame
 *  is stable while the counts hold and moves when they change. The live build
 *  replaces this whole block with real detections; the layout does not change. */
/** Deterministic pseudo-boxes derived from the lane's own counts, laid out on
 *  a coarse grid so they read as separate vehicles rather than a pile. The live
 *  build replaces this whole block with real detections; the layout around it
 *  does not change. */
const LANES_ACROSS = 4;
const ROWS = 4;

function boxesFor(lanes: VisionLane[]) {
  const out: {
    key: string; x: number; y: number; w: number; h: number; cls: string; conf: number;
  }[] = [];
  const taken = new Set<number>();

  lanes.forEach((lane, li) => {
    for (const [cls, count] of Object.entries(lane.type_composition) as [string, number][]) {
      for (let k = 0; k < Math.min(count, 2); k++) {
        if (out.length >= 12) return;
        // Walk to the next free cell rather than overlapping an occupied one.
        let cell = (li * 5 + k * 7 + cls.length * 3) % (LANES_ACROSS * ROWS);
        for (let tries = 0; taken.has(cell) && tries < LANES_ACROSS * ROWS; tries++) {
          cell = (cell + 1) % (LANES_ACROSS * ROWS);
        }
        if (taken.has(cell)) return;
        taken.add(cell);

        const col = cell % LANES_ACROSS;
        const row = Math.floor(cell / LANES_ACROSS);
        const depth = 0.45 + (row / (ROWS - 1)) * 0.55;  // nearer rows read bigger
        const w = (cls === 'truck' ? 13 : cls === 'bike' ? 5.5 : 9) * depth;
        const h = (cls === 'truck' ? 26 : cls === 'bike' ? 13 : 18) * depth;
        out.push({
          key: `${lane.lane_id}-${cls}-${k}`,
          x: 8 + col * 21 + (col % 2) * 2,
          y: Math.min(30 + row * 15, 92 - h),
          w, h, cls, conf: lane.confidence,
        });
      }
    }
  });
  return out;
}

interface Props {
  frame: Frame | null;
  junction: JunctionId;
  alert?: IncidentAlert;
}

export function DetectionFeed({ frame, junction, alert }: Props) {
  const vision = frame?.digital_twin.junctions[junction]?.vision;
  const lanes = useMemo(() => (vision ? Object.values(vision) : []), [vision]);
  const boxes = useMemo(() => boxesFor(lanes), [lanes]);

  const perApproach = useMemo(() => {
    const acc: Record<string, number> = { east: 0, west: 0, north: 0, south: 0 };
    for (const l of lanes) {
      acc[l.approach] = (acc[l.approach] ?? 0)
        + Object.values(l.type_composition).reduce((a, b) => a + b, 0);
    }
    return acc;
  }, [lanes]);

  const source = lanes[0]?.source ?? 'no signal';

  if (!frame) {
    return <div className={s.frame} data-empty="true"><span>Waiting for the first frame</span></div>;
  }

  return (
    <div className={s.wrap}>
      {alert && (
        <div className={s.banner} role="status">
          <TriangleAlert size={15} strokeWidth={2.1} aria-hidden="true" />
          <span className={s.bannerType}>{alert.type}</span>
          <span className={s.bannerSep} aria-hidden="true">·</span>
          <span>{alert.junction} {alert.approach} lane {alert.lane_index}</span>
          <span className={s.bannerSep} aria-hidden="true">·</span>
          {/* §6 trap: distance_m is null until vision calibration lands. */}
          <span className={alert.distance_m === null ? s.calibrating : 'mono'}>
            {alert.distance_m === null
              ? 'range calibrating'
              : `${Math.round(alert.distance_m)} m to stop line`}
          </span>
          <span className={s.severity} data-sev={alert.severity}>{alert.severity}</span>
        </div>
      )}

      <div className={s.frame}>
        <div className={s.scene} aria-hidden="true">
          <span className={s.road} />
          <span className={s.stopLine} />
        </div>
        {boxes.map((b) => (
          <div
            key={b.key}
            className={s.box}
            data-cls={b.cls}
            style={{ left: `${b.x}%`, top: `${b.y}%`, width: `${b.w}%`, height: `${b.h}%` }}
          >
            <span className={s.boxLabel}>
              {b.cls} <i className="mono">{b.conf.toFixed(2)}</i>
            </span>
          </div>
        ))}
        <span className={s.sourceTag} data-live={source === 'camera' || undefined}>
          {source}
        </span>
      </div>

      <ul className={s.counts}>
        {(['east', 'west', 'north', 'south'] as const).map((a) => (
          <li key={a} className={s.count}>
            <span className="label">{APPROACH_SHORT[a]}</span>
            <span className="mono">{perApproach[a] ?? 0}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

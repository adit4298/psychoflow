import { useMemo } from 'react';
import { Select } from '@base-ui/react/select';
import { ChevronDown, Check, Lock, SkipForward, Undo2 } from 'lucide-react';
import { CycleEditor, CYCLE_S, DEFAULT_PLAN, type CyclePlan } from './CycleEditor';
import { AXIS_PHASE } from '../assistant/intent';
import { useCommands } from '../assistant/useCommands';
import { useStore } from '../store/store';
import type { Frame, JunctionId } from '../data/types';
import { APPROACH_SHORT, lanesOf, laneNumber } from '../data/format';
import s from './JunctionControlCard.module.css';

const BIAS_WEIGHT = 2.0;      // within control_api's 0.1–10.0
const BIAS_DURATION_S = 120;  // within control_api's 10–900

interface Props { junction: JunctionId; frame: Frame | null; disabled: boolean }

export function JunctionControlCard({ junction, frame, disabled }: Props) {
  const plan = useStore((st) => st.cycles[junction]) ?? DEFAULT_PLAN;
  const hold = useStore((st) => st.holds[junction]);
  const bias = useStore((st) => st.bias[junction]);
  const setCycle = useStore((st) => st.setCycle);
  const record = useStore((st) => st.record);
  const say = useStore((st) => st.say);
  const { apply } = useCommands();

  const progress = ((frame?.sim_time ?? 0) % CYCLE_S) / CYCLE_S;
  const lanes = useMemo(() => (frame ? lanesOf(frame, junction) : []), [frame, junction]);

  const changePlan = (next: CyclePlan) => setCycle(junction, next);

  const toggleHold = (axis: 'ew' | 'ns') => {
    const phase = AXIS_PHASE[axis];
    if (hold === phase) apply({ fn: 'clear_override', junction });
    else apply({ fn: 'force_phase', junction, axis });
  };

  const applyCycle = (scope: 'junction' | 'corridor') => {
    // §11: control_api has no cycle-plan function yet. The intent is built and
    // shown optimistically; only the send is stubbed.
    const action = record({
      kind: 'cycle_plan',
      junction,
      summary: `Cycle plan · ${scope === 'corridor' ? 'whole corridor' : junction}`
        + ` · E–W ${plan.ewGreen}s / N–S ${plan.nsGreen}s`,
      detail: { scope, ...plan },
    });
    say('console',
      `Cycle plan staged for ${scope === 'corridor' ? 'the corridor' : junction}.`
      + ' It takes effect on the next 60-second boundary.', action);
  };

  return (
    <section className={s.card}>
      <header className={s.head}>
        <h2 className={s.title}>{junction}</h2>
        <span className={s.meta}>
          <span className="mono">{lanes.length}</span> lanes
        </span>
        {hold !== undefined && (
          <span className={s.holdChip}>
            <Lock size={12} strokeWidth={2.2} aria-hidden="true" />
            held on phase {hold}
          </span>
        )}
      </header>

      <CycleEditor
        plan={plan}
        onChange={changePlan}
        progress={progress}
        disabled={disabled}
        label={junction}
      />

      <div className={s.actions}>
        <button type="button" className={s.action} disabled={disabled}
                data-on={hold === AXIS_PHASE.ew || undefined}
                onClick={() => toggleHold('ew')}>
          <Lock size={14} strokeWidth={2} aria-hidden="true" />
          {hold === AXIS_PHASE.ew ? 'Release E\u2013W' : 'Hold E\u2013W green'}
        </button>
        <button type="button" className={s.action} disabled={disabled}
                data-on={hold === AXIS_PHASE.ns || undefined}
                onClick={() => toggleHold('ns')}>
          <Lock size={14} strokeWidth={2} aria-hidden="true" />
          {hold === AXIS_PHASE.ns ? 'Release N\u2013S' : 'Hold N\u2013S green'}
        </button>
        <button type="button" className={s.action} disabled={disabled}
                onClick={() => apply({ fn: 'force_phase', junction, axis: 'ns', oneShot: true })}>
          <SkipForward size={14} strokeWidth={2} aria-hidden="true" />
          Skip to N&ndash;S now
        </button>
      </div>

      <div className={s.biasRow}>
        <label className={`label ${s.biasLabel}`} id={`bias-${junction}`}>Priority lane</label>
        <Select.Root
          value={bias?.laneId ?? ''}
          onValueChange={(v: string | null) => {
            if (!v) { apply({ fn: 'clear_override', junction }); return; }
            apply({
              fn: 'set_lane_bias', junction,
              laneSlot: laneNumber(v), weight: BIAS_WEIGHT, durationS: BIAS_DURATION_S,
            });
          }}
          disabled={disabled}
        >
          <Select.Trigger className={s.select} aria-labelledby={`bias-${junction}`}>
            <Select.Value>
              {(v: string) => (v ? v : <span className={s.placeholder}>None</span>)}
            </Select.Value>
            <Select.Icon><ChevronDown size={15} strokeWidth={2} aria-hidden="true" /></Select.Icon>
          </Select.Trigger>
          <Select.Portal>
            <Select.Positioner sideOffset={6} align="start">
              <Select.Popup className={s.popup}>
                {lanes.map((lane) => (
                  <Select.Item key={lane.lane_id} value={lane.lane_id} className={s.item}>
                    <Select.ItemIndicator className={s.tick}>
                      <Check size={13} strokeWidth={2.4} aria-hidden="true" />
                    </Select.ItemIndicator>
                    <Select.ItemText>
                      <span className="mono">{lane.lane_id}</span>
                    </Select.ItemText>
                    <span className={s.itemMeta}>
                      {APPROACH_SHORT[lane.approach]} &middot; slot {laneNumber(lane.lane_id)}
                    </span>
                  </Select.Item>
                ))}
              </Select.Popup>
            </Select.Positioner>
          </Select.Portal>
        </Select.Root>
        {bias && (
          <span className={`mono ${s.biasMeta}`}>
            &times;{bias.weight} for {bias.durationS}s
          </span>
        )}
      </div>

      <footer className={s.foot}>
        <button type="button" className={s.primary} disabled={disabled}
                onClick={() => applyCycle('junction')}>
          Apply to {junction}
        </button>
        <button type="button" className={s.secondary} disabled={disabled}
                onClick={() => applyCycle('corridor')}>
          Apply to whole corridor
        </button>
        <button type="button" className={s.ghost} disabled={disabled}
                onClick={() => apply({ fn: 'set_mode', mode: 'auto' })}>
          <Undo2 size={14} strokeWidth={2} aria-hidden="true" />
          Return to Auto
        </button>
      </footer>
    </section>
  );
}

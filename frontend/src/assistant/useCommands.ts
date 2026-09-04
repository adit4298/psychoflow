import { useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useStore, type ActionRecord } from '../store/store';
import { AXIS_LABEL, AXIS_PHASE, describe, parseIntent, type Intent } from './intent';
import { secs } from '../data/format';
import { JUNCTION_IDS as JUNCTIONS } from '../data/types';

const UNPARSED =
  'Didn\u2019t catch a command \u2014 try \u201chold N\u2013S green at J2 for 20 seconds\u201d.';

/** One place where an intent becomes a control call, whether it arrived from a
 *  Manual button, a quick-action pill, typing, or the mic. Everything is
 *  optimistic: the intended result shows immediately and carries an Undo. */
export function useCommands() {
  const navigate = useNavigate();
  const st = useStore;

  const releaseAll = useCallback(() => {
    const { setHold, setBias } = st.getState();
    for (const j of JUNCTIONS) { setHold(j, null); setBias(j, null); }
  }, [st]);

  const apply = useCallback((intent: Intent): ActionRecord | null => {
    const { setHold, setBias, setMode, record, say, frame, mode } = st.getState();

    if (intent.fn === 'get_stats') {
      const m = frame?.metrics_snapshot;
      say('console', m
        ? `Mean wait ${secs(m.mean_wait_max)}, fairness ${m.wait_time_variance_across_lanes.toFixed(1)}, `
          + `${m.throughput_total} cleared, ${m.starvation_events_total} starvation events.`
        : 'No frame yet — the corridor has not reported in.');
      return null;
    }

    if (intent.fn === 'set_mode') {
      setMode(intent.mode);
      // Handing the corridor back to Auto releases every operator pin and
      // bias with it. Leaving them set would show "held on phase 2" on a
      // junction the policy is actually driving.
      if (intent.mode === 'auto') releaseAll();
      const action = record({ kind: 'set_mode', summary: describe(intent), detail: { ...intent } });
      say('console', intent.mode === 'manual'
        ? 'You have control. Auto is paused.'
        : 'Auto has the corridor again.', action);
      if (intent.mode === 'manual') navigate('/manual');
      return action;
    }

    // Everything below drives signals, and only Manual mode may do that.
    if (mode !== 'manual') {
      say('console', 'Auto is driving. Switch to Manual first and I\u2019ll run that.');
      return null;
    }

    if (intent.fn === 'force_phase') {
      const phase = AXIS_PHASE[intent.axis];
      if (intent.oneShot) {
        // A skip lands on the next decision step and then releases, so it is
        // recorded rather than pinned.
        const action = record({
          kind: 'force_phase', junction: intent.junction,
          summary: describe(intent), detail: { phase, oneShot: true },
        });
        say('console',
          `Skipping ${intent.junction} to ${AXIS_LABEL[intent.axis]} at the next decision step.`,
          action);
        return action;
      }
      setHold(intent.junction, phase);
      const action = record({
        kind: 'force_phase', junction: intent.junction,
        summary: describe(intent), detail: { phase, durationS: intent.durationS },
      });
      say('console',
        `Holding ${AXIS_LABEL[intent.axis]} at ${intent.junction}`
        + `${intent.durationS ? ` for ${intent.durationS} s` : ' until released'}.`,
        action);
      return action;
    }

    if (intent.fn === 'clear_override') {
      const targets = intent.junction ? [intent.junction] : JUNCTIONS;
      targets.forEach((j) => setHold(j, null));
      const action = record({
        kind: 'clear_override', junction: intent.junction, summary: describe(intent),
      });
      say('console', `Released ${intent.junction ?? 'every'} hold.`, action);
      return action;
    }

    // set_lane_bias
    setBias(intent.junction, {
      laneId: `slot ${intent.laneSlot}`, weight: intent.weight, durationS: intent.durationS,
    });
    const action = record({
      kind: 'set_lane_bias', junction: intent.junction,
      summary: describe(intent), detail: { ...intent },
    });
    say('console',
      `Lane ${intent.laneSlot} at ${intent.junction} weighted \u00d7${intent.weight}`
      + ` for ${intent.durationS} s. Lane numbering is 0-based, as in the log.`,
      action);
    return action;
  }, [navigate, releaseAll, st]);

  /** Free text from the field or the mic. Fails closed. */
  const submit = useCallback((text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    st.getState().say('officer', trimmed);
    const intent = parseIntent(trimmed);
    if (!intent) {
      st.getState().say('console', UNPARSED);
      return;
    }
    apply(intent);
  }, [apply, st]);

  const undo = useCallback((action: ActionRecord) => {
    const { undo: markUndone, setHold, setBias, setMode, say } = st.getState();
    const j = action.intent.junction;
    switch (action.intent.kind) {
      case 'force_phase': if (j) setHold(j, null); break;
      case 'set_lane_bias': if (j) setBias(j, null); break;
      case 'set_mode': {
        const back = action.intent.detail?.mode === 'manual' ? 'auto' : 'manual';
        setMode(back);
        if (back === 'auto') releaseAll();
        break;
      }
      // clear_override and cycle_plan have nothing to restore — the pins are
      // already gone and the cycle plan was never sent (§11).
      default: break;
    }
    markUndone(action.id);
    say('console', `Undone \u2014 ${action.intent.summary.toLowerCase()}.`);
  }, [releaseAll, st]);

  return { apply, submit, undo };
}

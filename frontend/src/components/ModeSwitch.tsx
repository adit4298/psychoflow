import { useNavigate } from 'react-router-dom';
import { useStore, type Mode } from '../store/store';
import s from './ModeSwitch.module.css';

const OPTIONS: { value: Mode; label: string }[] = [
  { value: 'auto', label: 'Auto' },
  { value: 'manual', label: 'Manual' },
];

/** §7.2. Maps to control_api set_mode(). Switching to Manual takes the officer
 *  to the controls, because handing over control without showing the controls
 *  is a dead end. */
export function ModeSwitch() {
  const mode = useStore((st) => st.mode);
  const setMode = useStore((st) => st.setMode);
  const record = useStore((st) => st.record);
  const say = useStore((st) => st.say);
  const navigate = useNavigate();

  const choose = (next: Mode) => {
    if (next === mode) return;
    setMode(next);
    const action = record({
      kind: 'set_mode',
      summary: next === 'manual' ? 'Manual control taken' : 'Returned to Auto',
      detail: { mode: next },
    });
    say('console', next === 'manual'
      ? 'You have control. Auto is paused.'
      : 'Auto has the corridor again.', action);
    if (next === 'manual') navigate('/manual');
  };

  return (
    <div className={s.wrap} role="radiogroup" aria-label="Control mode">
      <span className={s.thumb} data-pos={mode} aria-hidden="true" />
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={mode === o.value}
          className={s.option}
          onClick={() => choose(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

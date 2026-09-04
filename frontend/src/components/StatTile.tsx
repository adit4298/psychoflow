import NumberFlow from '@number-flow/react';
import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react';
import { Sparkline } from './Sparkline';
import s from './StatTile.module.css';

interface Props {
  label: string;
  value: number;
  unit?: string;
  decimals?: number;
  history: number[];
  /** true when a rise is bad — mean wait, variance, starvation. */
  lowerIsBetter?: boolean;
  alarm?: boolean;
  /** Value ~5 min of sim time ago; undefined until enough history exists. */
  previous?: number;
}

/** §8: live numbers tween, they never bounce. NumberFlow drives it with our
 *  own curve rather than its default spring. */
const TWEEN = { duration: 180, easing: 'cubic-bezier(0.23, 1, 0.32, 1)' } as const;

export function StatTile({
  label, value, unit, decimals = 0, history, lowerIsBetter, alarm, previous,
}: Props) {
  const delta = previous === undefined ? null : value - previous;
  const flat = delta !== null && Math.abs(delta) < (decimals ? 0.05 : 0.5);
  const good = delta === null || flat ? null : lowerIsBetter ? delta < 0 : delta > 0;
  const DeltaIcon = flat ? Minus : (delta ?? 0) > 0 ? ArrowUpRight : ArrowDownRight;

  return (
    <article className={s.tile}>
      <p className={`label ${s.label}`}>{label}</p>

      <div className={s.row}>
        <p className={s.value} data-alarm={alarm || undefined}>
          <NumberFlow
            value={value}
            format={{ minimumFractionDigits: decimals, maximumFractionDigits: decimals }}
            transformTiming={TWEEN}
            spinTiming={TWEEN}
            opacityTiming={TWEEN}
            respectMotionPreference
          />
          {unit && <span className={s.unit}>{unit}</span>}
        </p>
        <Sparkline values={history} tone={alarm ? 'stop' : 'quiet'} />
      </div>

      <p className={s.delta} data-good={good === null ? 'flat' : good ? 'yes' : 'no'}>
        {delta === null ? (
          <span className={s.deltaPending}>building history</span>
        ) : (
          <>
            <DeltaIcon size={13} strokeWidth={2.2} aria-hidden="true" />
            <span className="mono">
              {flat ? '0' : `${delta > 0 ? '+' : '−'}${Math.abs(delta).toFixed(decimals)}`}
            </span>
            <span className={s.deltaNote}>vs. 5 min ago</span>
          </>
        )}
      </p>
    </article>
  );
}

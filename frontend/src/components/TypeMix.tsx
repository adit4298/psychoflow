import type { TypeComposition } from '../data/types';
import s from './TypeMix.module.css';

const ORDER = ['bike', 'auto', 'car', 'truck', 'ambulance'] as const;
const NAME: Record<string, string> = {
  bike: 'two-wheeler', auto: 'auto', car: 'car', truck: 'truck', ambulance: 'ambulance',
};

/** A 5-segment bar, ~40px wide. Reads as a proportion at a glance; the exact
 *  counts live in the title so a hover still answers precisely. */
export function TypeMix({ mix }: { mix: TypeComposition }) {
  const total = ORDER.reduce((a, k) => a + (mix[k] ?? 0), 0);
  if (total === 0) return <span className={s.none}>—</span>;

  const title = ORDER.filter((k) => mix[k]).map((k) => `${mix[k]} ${NAME[k]}`).join(', ');

  return (
    <span className={s.bar} title={title} role="img" aria-label={title}>
      {ORDER.map((k) => (mix[k] ? (
        <span key={k} className={s.seg} data-type={k}
              style={{ flexGrow: mix[k] }} />
      ) : null))}
    </span>
  );
}

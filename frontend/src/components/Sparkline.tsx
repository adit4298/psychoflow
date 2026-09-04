import { useId } from 'react';
import s from './Sparkline.module.css';

interface Props {
  values: number[];
  /** Colours the fill and the line. Stat tiles use --ink-3 unless alarming. */
  tone?: 'quiet' | 'stop';
  width?: number;
  height?: number;
}

/** ~30 frames of context, drawn quietly. It exists to say "and it has been
 *  doing this", not to be read precisely — Logs is where numbers get read. */
export function Sparkline({ values, tone = 'quiet', width = 88, height = 24 }: Props) {
  const gid = useId().replace(/:/g, '');
  if (values.length < 2) return <div className={s.empty} style={{ width, height }} />;

  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = width / (values.length - 1);
  const y = (v: number) => height - 2 - ((v - min) / span) * (height - 4);

  const points = values.map((v, i) => `${(i * step).toFixed(2)},${y(v).toFixed(2)}`);
  const line = `M${points.join(' L')}`;
  const area = `${line} L${width},${height} L0,${height} Z`;

  return (
    <svg
      className={s.svg}
      data-tone={tone}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={`f${gid}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="currentColor" stopOpacity=".16" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#f${gid})`} />
      <path d={line} fill="none" stroke="currentColor" strokeWidth="1.5"
            strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

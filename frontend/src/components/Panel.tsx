import type { ReactNode } from 'react';
import clsx from 'clsx';
import s from './Panel.module.css';

interface Props {
  title?: ReactNode;
  /** Sits to the right of the heading — a count, a source tag, a control. */
  aside?: ReactNode;
  /** Reads under the heading in --ink-2. Keep it to one clause. */
  note?: ReactNode;
  children: ReactNode;
  className?: string;
  /** Drops the padding so a table or a media frame can reach the panel edge. */
  flush?: boolean;
}

/** The only surface primitive. A panel never contains another panel (§9) —
 *  groups of cards sit directly on --ground instead. */
export function Panel({ title, aside, note, children, className, flush }: Props) {
  return (
    <section className={clsx(s.panel, flush && s.flush, className)}>
      {(title || aside) && (
        <header className={clsx(s.head, flush && s.headInset)}>
          <div className={s.headText}>
            {title && <h2 className={s.title}>{title}</h2>}
            {note && <p className={s.note}>{note}</p>}
          </div>
          {aside && <div className={s.aside}>{aside}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

import { Bell, CircleCheck, TriangleAlert } from 'lucide-react';
import { Popover } from '@base-ui/react/popover';
import { ModeSwitch } from './ModeSwitch';
import { useLatestAlerts, useStore } from '../store/store';
import { simClock } from '../data/format';
import s from './TopBar.module.css';

const STATUS_TEXT: Record<string, string> = {
  connecting: 'connecting',
  live: 'live',
  ended: 'ended',
  error: 'no signal',
};

export function TopBar({ title, context }: { title: string; context: string }) {
  const alerts = useLatestAlerts();
  const frame = useStore((st) => st.frame);
  const status = useStore((st) => st.status);
  const sourceLabel = useStore((st) => st.sourceLabel);
  const alert = alerts[0];

  return (
    <header className={s.bar}>
      <div className={s.left}>
        <h1 className={s.title}>{title}</h1>
        <p className={s.context}>
          {context}
          <span className={s.dot} aria-hidden="true">·</span>
          <span className={s.source}>
            {sourceLabel} {STATUS_TEXT[status] ?? status}
          </span>
          {frame && (
            <>
              <span className={s.dot} aria-hidden="true">·</span>
              <span className="mono">{simClock(frame.sim_time)} sim</span>
            </>
          )}
        </p>
      </div>

      <div className={s.right}>
        {alert ? (
          <span className={`${s.chip} ${s.chipStop}`}>
            <TriangleAlert size={14} strokeWidth={2.1} aria-hidden="true" />
            <span className={s.chipType}>{alert.type}</span> at {alert.junction} · {alert.severity}
          </span>
        ) : (
          <span className={`${s.chip} ${s.chipGo}`}>
            <CircleCheck size={14} strokeWidth={2.1} aria-hidden="true" />
            Corridor clear
          </span>
        )}

        <ModeSwitch />

        <Popover.Root>
          <Popover.Trigger className={s.bell} aria-label="Notifications">
            <Bell size={18} strokeWidth={1.9} aria-hidden="true" />
            {alert && <span className={s.pip} aria-hidden="true" />}
          </Popover.Trigger>
          <Popover.Portal>
            <Popover.Positioner side="bottom" align="end" sideOffset={10}>
              <Popover.Popup className={s.alerts}>
                <p className={s.alertsTitle}>Alerts</p>
                {alerts.length === 0 ? (
                  <p className={s.alertsEmpty}>No active alerts.</p>
                ) : (
                  <ul className={s.alertsList}>
                    {alerts.map((a, i) => (
                      <li key={i} className={s.alertRow}>
                        <TriangleAlert size={14} strokeWidth={2.1} aria-hidden="true" />
                        <span>
                          <span className={s.chipType}>{a.type}</span> at{' '}
                          {a.junction} · {a.severity}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </Popover.Popup>
            </Popover.Positioner>
          </Popover.Portal>
        </Popover.Root>
      </div>
    </header>
  );
}

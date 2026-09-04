import { NavLink } from 'react-router-dom';
import { Tooltip } from '@base-ui/react/tooltip';
import {
  LayoutGrid, Waypoints, SlidersHorizontal, ScrollText,
  Sun, Moon, Settings,
} from 'lucide-react';
import s from './Rail.module.css';
import { useTheme } from '../useTheme';

const NAV = [
  { to: '/', label: 'Overview', Icon: LayoutGrid, end: true },
  { to: '/junctions', label: 'Junctions', Icon: Waypoints, end: false },
  { to: '/manual', label: 'Manual control', Icon: SlidersHorizontal, end: false },
  { to: '/logs', label: 'Logs', Icon: ScrollText, end: false },
];

function RailTip({ label, children }: { label: string; children: React.ReactElement }) {
  return (
    <Tooltip.Root>
      <Tooltip.Trigger render={children} />
      <Tooltip.Portal>
        <Tooltip.Positioner side="right" sideOffset={10}>
          <Tooltip.Popup className={s.tip}>{label}</Tooltip.Popup>
        </Tooltip.Positioner>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

export function Rail() {
  const [theme, toggleTheme] = useTheme();
  const NextIcon = theme === 'light' ? Moon : Sun;

  return (
    /* delay 0 after the first hover — moving along the rail shouldn't wait
       again for each tile (§8). */
    <Tooltip.Provider delay={350} closeDelay={80}>
      <nav className={s.rail} aria-label="Console sections">
        <div className={s.brand} aria-label="PsychoFlow">
          <svg viewBox="0 0 28 28" width="28" height="28" aria-hidden="true">
            <rect width="28" height="28" rx="9" fill="var(--primary)" />
            <path
              d="M8 19V9.6h4.7a3.1 3.1 0 0 1 0 6.2H8"
              fill="none" stroke="var(--primary-ink)"
              strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round"
            />
            <circle cx="19.4" cy="18.7" r="1.5" fill="var(--primary-ink)" />
          </svg>
        </div>

        <ul className={s.group}>
          {NAV.map(({ to, label, Icon, end }) => (
            <li key={to}>
              <RailTip label={label}>
                <NavLink
                  to={to}
                  end={end}
                  className={s.item}
                  aria-label={label}
                >
                  <Icon size={20} strokeWidth={1.9} aria-hidden="true" />
                </NavLink>
              </RailTip>
            </li>
          ))}
        </ul>

        <div className={s.foot}>
          <RailTip label={theme === 'light' ? 'Dark theme' : 'Light theme'}>
            <button type="button" className={s.item} onClick={toggleTheme}>
              <NextIcon size={20} strokeWidth={1.9} aria-hidden="true" />
              <span className="visually-hidden">
                Switch to {theme === 'light' ? 'dark' : 'light'} theme
              </span>
            </button>
          </RailTip>
          <RailTip label="Settings">
            <button type="button" className={s.item} aria-label="Settings">
              <Settings size={20} strokeWidth={1.9} aria-hidden="true" />
            </button>
          </RailTip>
          <RailTip label="Officer on duty · Booth 2">
            <button type="button" className={s.avatar} aria-label="Officer on duty">
              <span aria-hidden="true">RK</span>
            </button>
          </RailTip>
        </div>
      </nav>
    </Tooltip.Provider>
  );
}

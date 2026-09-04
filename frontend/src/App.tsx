import { useEffect } from 'react';
import { Route, Routes, useLocation } from 'react-router-dom';
import { Rail } from './components/Rail';
import { TopBar } from './components/TopBar';
import { Overview } from './screens/Overview';
import { Manual } from './screens/Manual';
import { Junctions } from './screens/Junctions';
import { Logs } from './screens/Logs';
import { createSource } from './data/source';
import { useStore } from './store/store';
import s from './App.module.css';

const TITLES: Record<string, { title: string; context: string }> = {
  '/':          { title: 'Overview',       context: 'corridor J1 → J3' },
  '/junctions': { title: 'Junctions',      context: 'per-junction detail' },
  '/manual':    { title: 'Manual control', context: 'cycle plans and overrides' },
  '/logs':      { title: 'Logs',           context: 'every decision this session' },
};

export function App() {
  const { pathname } = useLocation();
  const meta = TITLES[pathname] ?? TITLES['/'];
  useEffect(() => {
    // StrictMode mounts, unmounts and remounts in dev. Creating the source
    // inside the effect and stopping it in the cleanup is what makes that
    // survivable — a ref guard would let the first cleanup kill the only
    // source and the stream would never start.
    const src = createSource();
    const { pushFrame, setStatus, setSourceLabel } = useStore.getState();
    setSourceLabel(src.label);
    src.start(pushFrame, setStatus);
    return () => src.stop();
  }, []);

  return (
    <div className={s.shell}>
      <Rail />
      <div className={s.main}>
        <div className={s.inner}>
          <TopBar title={meta.title} context={meta.context} />
          {/* keyed on pathname so each route replays its enter (§8) */}
          <main className={s.content} key={pathname}>
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/junctions" element={<Junctions />} />
              <Route path="/manual" element={<Manual />} />
              <Route path="/logs" element={<Logs />} />
            </Routes>
          </main>
        </div>
      </div>
    </div>
  );
}

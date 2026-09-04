import { useMemo } from 'react';
import { create } from 'zustand';
import type {
  Frame, IncidentAlert, JunctionId, ResponderMessage, SpilloverPrediction,
} from '../data/types';
import type { SourceStatus } from '../data/source';

/** Enough history for the 90 s timeline and the 30-point sparklines, and no
 *  more — this array is re-read on every frame. */
const WINDOW = 120;

export type Mode = 'auto' | 'manual';

/** §11: control_api has no "set 60-second cycle plan" function yet. The editor
 *  emits this and shows the optimistic result; only the send is stubbed. */
export interface CyclePlanIntent {
  kind: 'cycle_plan';
  junction: JunctionId;
  ewGreen: number;
  nsGreen: number;
  amber: number;
}
export interface ControlIntent {
  kind: 'force_phase' | 'clear_override' | 'set_lane_bias' | 'set_mode' | 'cycle_plan';
  junction?: JunctionId;
  summary: string;
  detail?: Record<string, unknown>;
}

export interface ActionRecord {
  id: string;
  at: number;
  intent: ControlIntent;
  undone: boolean;
}

export interface ChatTurn {
  id: string;
  role: 'officer' | 'console';
  text: string;
  /** Present when the turn produced a control action. */
  action?: ActionRecord;
}

interface State {
  frame: Frame | null;
  history: Frame[];
  status: SourceStatus;
  statusDetail?: string;
  sourceLabel: string;
  mode: Mode;
  /** Junction → pinned phase index, held until released (§7.3 quick actions). */
  holds: Partial<Record<JunctionId, number>>;
  /** Junction → the officer's edited cycle, optimistic until the backend lands. */
  cycles: Partial<Record<JunctionId, { ewGreen: number; nsGreen: number; amber: number }>>;
  bias: Partial<Record<JunctionId, { laneId: string; weight: number; durationS: number }>>;
  actions: ActionRecord[];
  chat: ChatTurn[];

  pushFrame: (f: Frame) => void;
  setStatus: (s: SourceStatus, detail?: string) => void;
  setSourceLabel: (l: string) => void;
  setMode: (m: Mode) => void;
  setHold: (j: JunctionId, phase: number | null) => void;
  setCycle: (j: JunctionId, c: { ewGreen: number; nsGreen: number; amber: number }) => void;
  setBias: (j: JunctionId, b: { laneId: string; weight: number; durationS: number } | null) => void;
  record: (intent: ControlIntent) => ActionRecord;
  undo: (id: string) => void;
  say: (role: ChatTurn['role'], text: string, action?: ActionRecord) => void;
}

let seq = 0;
const nextId = () => `a${++seq}`;

export const useStore = create<State>((set, get) => ({
  frame: null,
  history: [],
  status: 'connecting',
  sourceLabel: '',
  mode: 'auto',
  holds: {},
  cycles: {},
  bias: {},
  actions: [],
  chat: [],

  pushFrame: (f) =>
    set((s) => {
      const history = s.history.length >= WINDOW
        ? [...s.history.slice(s.history.length - WINDOW + 1), f]
        : [...s.history, f];
      return { frame: f, history };
    }),

  setStatus: (status, statusDetail) => set({ status, statusDetail }),
  setSourceLabel: (sourceLabel) => set({ sourceLabel }),
  setMode: (mode) => set({ mode }),

  setHold: (j, phase) =>
    set((s) => {
      const holds = { ...s.holds };
      if (phase === null) delete holds[j];
      else holds[j] = phase;
      return { holds };
    }),

  setCycle: (j, c) => set((s) => ({ cycles: { ...s.cycles, [j]: c } })),

  setBias: (j, b) =>
    set((s) => {
      const bias = { ...s.bias };
      if (b === null) delete bias[j];
      else bias[j] = b;
      return { bias };
    }),

  record: (intent) => {
    const rec: ActionRecord = {
      id: nextId(),
      at: get().frame?.sim_time ?? 0,
      intent,
      undone: false,
    };
    set((s) => ({ actions: [rec, ...s.actions].slice(0, 40) }));
    return rec;
  },

  undo: (id) =>
    set((s) => ({
      actions: s.actions.map((a) => (a.id === id ? { ...a, undone: true } : a)),
      chat: s.chat.map((t) =>
        t.action?.id === id ? { ...t, action: { ...t.action, undone: true } } : t,
      ),
    })),

  say: (role, text, action) =>
    set((s) => ({ chat: [...s.chat, { id: nextId(), role, text, action }].slice(-30) })),
}));

/* ---- selectors ----
   These derive arrays, so they memoise on `history` identity. A selector that
   allocated a fresh array per call would re-render on every store read. */

export const useFrame = () => useStore((s) => s.frame);
export const useHistory = () => useStore((s) => s.history);

export function useLatestAlerts(): IncidentAlert[] {
  const history = useHistory();
  return useMemo(() => lastPresent(history, (f) => f.incident_alerts), [history]);
}

export function useLatestPredictions(): SpilloverPrediction[] {
  const history = useHistory();
  return useMemo(
    () => lastPresent(history, (f) => f.predictions?.spillover),
    [history],
  );
}

/** Every responder row seen this session, newest first, de-duplicated by
 *  (junction, lane, sim_time) — the same row repeats across many frames. */
export function useResponderMessages(): ResponderMessage[] {
  const history = useHistory();
  return useMemo(() => {
    const seen = new Set<string>();
    const out: ResponderMessage[] = [];
    for (let i = history.length - 1; i >= 0; i--) {
      for (const m of history[i].responder_messages ?? []) {
        const k = `${m.junction_id}|${m.lane_id}|${m.sim_time}`;
        if (!seen.has(k)) { seen.add(k); out.push(m); }
      }
    }
    return out;
  }, [history]);
}

/** The additive keys appear on only some frames; the last non-empty value is
 *  what the officer should still be looking at. Returns a shared empty array
 *  when a key has never arrived, so "absent" reads as a quiet state and not
 *  as an error. */
function lastPresent<T>(history: Frame[], pick: (f: Frame) => T[] | undefined): T[] {
  for (let i = history.length - 1; i >= 0; i--) {
    const v = pick(history[i]);
    if (v && v.length) return v;
  }
  return EMPTY as T[];
}

const EMPTY: never[] = [];

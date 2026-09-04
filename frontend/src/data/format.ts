import type { Approach, DecisionReason, Frame, Lane, ResponderMessage } from './types';

/** The recorded narration carries UTF-8 mojibake — the separator arrives as
 *  U+FFFD (and, depending on the encoder that mangled it, as the raw cp1252
 *  read of the UTF-8 middot). Restore it to a real interpunct. §6. */
const MOJIBAKE = /\uFFFD|Â·|â€¢|â€“/g;
export function sanitize(text: string | undefined | null): string {
  if (!text) return '';
  return text.replace(MOJIBAKE, '·').replace(/\s+/g, ' ').trim();
}

/** Sim seconds → HH:MM:SS. Sim time is not wall time; always label it as sim. */
export function simClock(t: number): string {
  const s = Math.max(0, Math.floor(t));
  const h = String(Math.floor(s / 3600)).padStart(2, '0');
  const m = String(Math.floor((s % 3600) / 60)).padStart(2, '0');
  const sec = String(s % 60).padStart(2, '0');
  return `${h}:${m}:${sec}`;
}

export function secs(v: number, digits = 1): string {
  return `${v.toFixed(digits)}s`;
}

export const REASON_LABEL: Record<string, string> = {
  raw_count: 'Highest demand',
  emergency_override: 'Emergency override',
  starvation_ceiling: 'Starvation ceiling',
  starvation_bonus: 'Fairness bonus',
  rl_policy: 'Policy decision',
  voice_command: 'Operator command',
};

/** Four visual classes, per §7.1's dot marker. */
export type ReasonClass = 'normal' | 'starvation' | 'emergency' | 'manual';

export function reasonClass(reason: string): ReasonClass {
  if (reason === 'emergency_override') return 'emergency';
  if (reason.startsWith('starvation')) return 'starvation';
  if (reason === 'voice_command') return 'manual';
  return 'normal';
}

export function reasonLabel(reason: DecisionReason | string): string {
  return REASON_LABEL[reason] ?? reason.replace(/_/g, ' ');
}

export const APPROACH_SHORT: Record<Approach | string, string> = {
  north: 'N', south: 'S', east: 'E', west: 'W',
};

export function laneNumber(laneId: string): number {
  const m = /_(\d+)$/.exec(laneId);
  return m ? Number(m[1]) : 0;
}

/** Signal state is never colour-only (§10) — every use pairs with this glyph. */
export const SIGNAL_GLYPH = { go: '▲', wait: '■', stop: '●' } as const;

export function lanesOf(frame: Frame, junction: 'J1' | 'J2' | 'J3'): Lane[] {
  const j = frame.digital_twin.junctions[junction];
  if (!j) return [];
  const raw = j.lanes as unknown;
  return Array.isArray(raw) ? (raw as Lane[]) : Object.values(j.lanes);
}

export function allLanes(frame: Frame): Lane[] {
  return (['J1', 'J2', 'J3'] as const).flatMap((j) => lanesOf(frame, j));
}

/** §6 trap: operator-triggered rows report a 0.0 s clearance and a 100 %
 *  improvement because the lane happened to already be green. Those are not a
 *  claim about the system. Only rows that actually fired an override and were
 *  not served on arrival carry a defensible clearance figure. */
export function isClaimable(m: ResponderMessage): boolean {
  return m.override_fired && !m.served_on_arrival && m.clearance_time_s > 0;
}

export function clearanceText(m: ResponderMessage): string {
  if (!isClaimable(m)) return 'lane already clear — no delay measured';
  return `≈ ${Math.round(m.clearance_time_s)} s to green`;
}

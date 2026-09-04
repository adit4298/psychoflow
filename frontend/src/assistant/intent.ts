import type { JunctionId } from '../data/types';

/** A rule-based parser over the verbs the console actually exposes. In the
 *  live build this is replaced by local Gemma via Ollama — no Claude API and
 *  no paid inference, here or there.
 *
 *  It fails closed: anything it cannot resolve to a known function AND a
 *  complete set of arguments returns null. It never guesses a junction, a
 *  phase, or a duration. */

export type Intent =
  | { fn: 'force_phase'; junction: JunctionId; axis: 'ew' | 'ns'; durationS?: number; oneShot?: boolean }
  | { fn: 'clear_override'; junction?: JunctionId }
  | { fn: 'set_mode'; mode: 'auto' | 'manual' }
  | { fn: 'set_lane_bias'; junction: JunctionId; laneSlot: number; weight: number; durationS: number }
  | { fn: 'get_stats' };

const JUNCTION_RE = /\bj\s?([123])\b|\bjunction\s+([123])\b/i;

function junctionIn(text: string): JunctionId | null {
  const m = JUNCTION_RE.exec(text);
  if (!m) return null;
  return `J${m[1] ?? m[2]}` as JunctionId;
}

function axisIn(text: string): 'ew' | 'ns' | null {
  if (/\b(n\s?[-\u2013\/]?\s?s|north\s*[-\u2013\/ ]?\s*south)\b/i.test(text)) return 'ns';
  if (/\b(e\s?[-\u2013\/]?\s?w|east\s*[-\u2013\/ ]?\s*west)\b/i.test(text)) return 'ew';
  return null;
}

function secondsIn(text: string): number | undefined {
  // Word boundaries at both ends. Without the leading one the digit run
  // backtracks, so "9000 seconds" matched "000" and read as zero. Callers
  // clamp the result to control_api's own ranges.
  const m = /\b(\d{1,5})\s*(?:s\b|secs?\b|seconds?\b)/i.exec(text);
  return m ? Number(m[1]) : undefined;
}

export function parseIntent(raw: string): Intent | null {
  const text = raw.trim().toLowerCase();
  if (!text) return null;

  if (/\b(status|stats|summary|report|how are we)\b/.test(text)) return { fn: 'get_stats' };

  if (/\bauto(matic)?\b/.test(text) && /\b(switch|go|back|return|hand|resume)\b/.test(text)) {
    return { fn: 'set_mode', mode: 'auto' };
  }
  if (/\bmanual\b/.test(text) && /\b(switch|take|go|give)\b/.test(text)) {
    return { fn: 'set_mode', mode: 'manual' };
  }

  if (/\b(release|clear|cancel|drop|lift)\b/.test(text) && /\b(hold|override|pin|lock)\b/.test(text)) {
    const junction = junctionIn(text);
    return junction ? { fn: 'clear_override', junction } : { fn: 'clear_override' };
  }

  if (/\b(hold|keep|pin|stay|extend)\b/.test(text)) {
    const junction = junctionIn(text);
    const axis = axisIn(text);
    // Both are required. A hold on an unnamed junction is exactly the guess
    // this parser must not make.
    if (!junction || !axis) return null;
    return { fn: 'force_phase', junction, axis, durationS: secondsIn(text) };
  }

  if (/\b(skip|switch|change|advance)\b/.test(text)) {
    const junction = junctionIn(text);
    const axis = axisIn(text);
    if (!junction || !axis) return null;
    return { fn: 'force_phase', junction, axis, oneShot: true };
  }

  if (/\b(priorit|bias|favour|favor|weight)/.test(text)) {
    const junction = junctionIn(text);
    const laneM = /\blane\s+(\d)\b/.exec(text);
    if (!junction || !laneM) return null;
    const weightM = /\bweight\s*([\d.]+)\b/.exec(text);
    const weight = Math.min(10, Math.max(0.1, weightM ? Number(weightM[1]) : 2));
    const durationS = Math.min(900, Math.max(10, secondsIn(text) ?? 120));
    // Lane numbering is 0-based, matching SUMO and the narrator. Spoken
    // "lane 3" means slot 3 — stated here, never silently assumed to match.
    return { fn: 'set_lane_bias', junction, laneSlot: Number(laneM[1]), weight, durationS };
  }

  return null;
}

export const AXIS_LABEL = { ew: 'E\u2013W', ns: 'N\u2013S' } as const;
/** Phase 0 serves east-west on this corridor; the competing movement is 2. */
export const AXIS_PHASE = { ew: 0, ns: 2 } as const;

export function describe(intent: Intent): string {
  switch (intent.fn) {
    case 'force_phase':
      return intent.oneShot
        ? `Skip to ${AXIS_LABEL[intent.axis]} \u00b7 ${intent.junction}`
        : `Hold ${AXIS_LABEL[intent.axis]} green \u00b7 ${intent.junction}`
          + (intent.durationS ? ` \u00b7 ${intent.durationS} s` : '');
    case 'clear_override':
      return `Release hold${intent.junction ? ` \u00b7 ${intent.junction}` : ' \u00b7 corridor'}`;
    case 'set_mode':
      return intent.mode === 'manual' ? 'Take manual control' : 'Return to Auto';
    case 'set_lane_bias':
      return `Priority lane ${intent.laneSlot} \u00b7 ${intent.junction}`
        + ` \u00b7 \u00d7${intent.weight} for ${intent.durationS} s`;
    case 'get_stats':
      return 'Corridor status';
  }
}

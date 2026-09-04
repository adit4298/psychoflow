import { useCallback, useRef, useState } from 'react';
import { useStore, type ActionRecord, type ControlIntent } from '../store/store';

/** The live assistant: text and voice both converge on ONE backend round trip
 *  — `raw text → local Gemma intent parse → control_api.dispatch()` (§7.5).
 *
 *  Nothing here parses, decides, or simulates. The panel sends words and
 *  renders what came back. Every value below — the message, the resolved lane,
 *  whether it applied, the inverse call for Undo — is computed by the backend,
 *  which is the only place that can see the corridor's real lane set.
 *
 *  WITHOUT A BACKEND it falls back to `intent.ts`'s rule parser, which is what
 *  DESIGN.md §7.5 designates for the fixture build. The fallback is honest
 *  rather than silent: `live` is false and the panel says so, because a demo
 *  that looks identical whether or not a real model ran is precisely the thing
 *  §7.5 is trying not to build. */

export interface AssistantReply {
  transcript: string;
  language: string | null;
  stt_provider: string;
  understood: boolean;
  read_only: boolean;
  applied: boolean;
  message: string;
  function: string | null;
  args: Record<string, unknown>;
  assumptions: string[];
  reason: string | null;
  model: string;
  latency_ms: number;
  stt_ms: number;
  parse_ms: number;
  undo: { function: string; args: Record<string, unknown> } | null;
}

/** The backend's HTTP origin, derived from the SAME source that selects the
 *  §13.2 stream — so a dashboard pointed at a live corridor talks to that
 *  corridor's assistant, and one running off the recorded fixture has no
 *  backend and says so. */
function resolveApiBase(): string | null {
  const explicit = import.meta.env.VITE_API_URL as string | undefined;
  if (explicit) return explicit.replace(/\/$/, '');
  const fromQuery = new URLSearchParams(window.location.search).get('ws');
  const ws = fromQuery ?? (import.meta.env.VITE_WS_URL as string | undefined);
  if (!ws) return null;
  try {
    const u = new URL(ws);
    u.protocol = u.protocol === 'wss:' ? 'https:' : 'http:';
    u.pathname = '';
    return u.toString().replace(/\/$/, '');
  } catch {
    return null;
  }
}

/** Resolved ONCE at module load, exactly as `createSource()` reads `?ws=` once.
 *  Re-reading `window.location` per render looked equivalent and was not: the
 *  router's client-side navigation drops the query string, so the moment the
 *  officer opened /manual the assistant silently downgraded to the offline
 *  rule parser while the corridor above it was still live. Caught by driving
 *  the real page, not by reading this back. */
const API_BASE = resolveApiBase();

export function apiBase(): string | null {
  return API_BASE;
}

/** A command times out rather than leaving the panel spinning. Generous
 *  against a measured ~1.7 s warm parse and an 18 s cold start — the backend
 *  pre-warms at boot, but a laptop that just woke up has not. */
const TIMEOUT_MS = 30_000;

/** Longest clip the mic sends. A spoken traffic command is a few seconds, and
 *  the backend caps the upload anyway; this stops a stuck recorder streaming. */
const MAX_CLIP_MS = 10_000;

/** The action card's SHORT label — `Pin J2 · phase 2`, not the whole sentence.
 *  The console's reply is already the bubble above it; repeating it inside the
 *  card just prints the same paragraph twice. §7.5 asks for a compact card
 *  ("Hold N–S green · J2 · 20 s") next to the Undo button.
 *
 *  Phases are shown 1-based to match what the officer said, with the raw index
 *  left to the backend's fuller reply — the two numbers differ by one and only
 *  one of them was spoken. */
function label(reply: AssistantReply): string {
  const a = reply.args ?? {};
  const j = (a.junction_id as string) ?? '';
  switch (reply.function) {
    case 'force_phase':   return `Pin ${j} · phase ${Number(a.phase) + 1}`;
    case 'clear_override': return `Release ${j || 'every junction'}`;
    case 'set_mode':      return `Mode → ${a.mode}`;
    case 'set_baseline_mode': return `Controller → ${a.baseline}`;
    case 'set_lane_bias': return `Bias ${a.lane_id} ×${a.weight} · ${a.duration_s}s`;
    case 'trigger_emergency': return `Emergency · ${a.lane_id}`;
    case 'inject_incident': return `${a.incident_type} · ${j}`;
    case 'set_topology':  return `Topology ${a.topology_id}`;
    default:              return reply.message;
  }
}

function summarise(reply: AssistantReply): ControlIntent {
  const kind = (reply.function ?? 'force_phase') as ControlIntent['kind'];
  const junction = (reply.args?.junction_id as ControlIntent['junction']) ?? undefined;
  return { kind, junction, summary: label(reply), detail: { ...reply.args } };
}

async function post(base: string, path: string, body: BodyInit,
                    headers?: HeadersInit): Promise<AssistantReply> {
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${base}${path}`, {
      method: 'POST', body, headers, signal: ctrl.signal,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return (await res.json()) as AssistantReply;
  } finally {
    window.clearTimeout(timer);
  }
}

export function useAssistant() {
  const base = apiBase();
  const [thinking, setThinking] = useState(false);
  const [lastMs, setLastMs] = useState<number | null>(null);
  const [recording, setRecording] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const st = useStore;

  const render = useCallback((reply: AssistantReply) => {
    const { say, record } = st.getState();
    setLastMs(reply.latency_ms);

    // A read-only answer ("36 lanes reporting…", "J2 switched because…") is a
    // reply, not an action: no card, and nothing to undo.
    if (!reply.understood || reply.read_only || !reply.applied) {
      say('console', reply.message);
      return;
    }
    const action: ActionRecord = record(summarise(reply));
    // The inverse call the backend computed rides on the record, so Undo sends
    // a real control call rather than only rolling back local UI state.
    (action.intent.detail as Record<string, unknown>).undo = reply.undo;
    say('console', reply.message, action);
  }, [st]);

  const fail = useCallback((detail: string) => {
    st.getState().say('console',
      `The assistant is not reachable (${detail}). The corridor is still `
      + 'running — use the Manual controls.');
  }, [st]);

  /** Free text from the field, a quick-action pill, or a browser transcript.
   *  Returns false ONLY when there is no backend, which is the caller's signal
   *  to run the local fixture parser instead. */
  const submitText = useCallback(async (text: string): Promise<boolean> => {
    const trimmed = text.trim();
    if (!trimmed || !base) return false;
    st.getState().say('officer', trimmed);
    setThinking(true);
    try {
      render(await post(base, '/voice/text', JSON.stringify({ text: trimmed }),
                        { 'Content-Type': 'application/json' }));
    } catch (e) {
      fail(e instanceof Error ? e.message : 'unknown error');
    } finally {
      setThinking(false);
    }
    return true;    // handled either way — the caller must not double-submit
  }, [base, fail, render, st]);

  /** Record a short clip and POST it for backend transcription. Used when the
   *  STT provider is whisper or sarvam; the webspeech provider never reaches
   *  here, because the browser has already produced text. */
  const recordAndSubmit = useCallback(async (): Promise<void> => {
    if (!base) return;
    if (recorder.current) { recorder.current.stop(); return; }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      // A denied microphone is not an error to escalate — the text field is
      // right there and does exactly the same thing.
      st.getState().say('console',
        'No microphone access — type the command instead.');
      return;
    }
    const chunks: Blob[] = [];
    const rec = new MediaRecorder(stream);
    recorder.current = rec;
    setRecording(true);
    rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      recorder.current = null;
      setRecording(false);
      if (!chunks.length) return;
      setThinking(true);
      try {
        const form = new FormData();
        form.append('file', new Blob(chunks, { type: rec.mimeType }), 'command.webm');
        const reply = await post(base, '/voice/audio', form);
        // The transcript becomes the officer's own turn only once the backend
        // has produced it — there is nothing honest to show before that.
        if (reply.transcript) st.getState().say('officer', reply.transcript);
        render(reply);
      } catch (e) {
        fail(e instanceof Error ? e.message : 'unknown error');
      } finally {
        setThinking(false);
      }
    };
    rec.start();
    window.setTimeout(() => { if (recorder.current === rec) rec.stop(); },
                      MAX_CLIP_MS);
  }, [base, fail, render, st]);

  /** Undo sends the backend's own inverse call back through `dispatch()`. It
   *  is a new control call, not a rewind: the corridor moved on in the seconds
   *  since, and §10 validates this one exactly like the first. */
  const undoRemote = useCallback(async (action: ActionRecord): Promise<boolean> => {
    const undo = (action.intent.detail as Record<string, unknown> | undefined)
      ?.undo as { function: string; args: Record<string, unknown> } | null | undefined;
    if (!base || !undo) return false;
    try {
      await post(base, '/voice/undo', JSON.stringify(undo),
                 { 'Content-Type': 'application/json' });
      return true;
    } catch {
      return false;   // the caller still rolls the local view back
    }
  }, [base]);

  return {
    live: base !== null,
    thinking, lastMs, recording,
    submitText, recordAndSubmit, undoRemote,
  };
}

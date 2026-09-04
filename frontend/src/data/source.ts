import type { Frame } from './types';

/** The UI never knows which implementation is behind this. Both push frames at
 *  their own pace; both report a connection status the shell can render. */
export type SourceStatus = 'connecting' | 'live' | 'ended' | 'error';

export interface FrameSource {
  /** Human label for the top bar, e.g. "recorded session" / "live corridor". */
  readonly label: string;
  start(onFrame: (f: Frame) => void, onStatus: (s: SourceStatus, detail?: string) => void): void;
  stop(): void;
  /** Fixture replay only — no-ops on the live socket. */
  setPaused?(paused: boolean): void;
}

const FIXTURE_URL = '/recorded_session.json';
const FIXTURE_FPS = 2;

/** Replays the recorded 200-frame session at ~2 fps, looping. */
export class FixtureSource implements FrameSource {
  readonly label = 'recorded session';
  private timer: number | null = null;
  private frames: Frame[] = [];
  private i = 0;
  private paused = false;
  private stopped = false;

  constructor(private url: string = FIXTURE_URL, private fps: number = FIXTURE_FPS) {}

  start(onFrame: (f: Frame) => void, onStatus: (s: SourceStatus, d?: string) => void) {
    onStatus('connecting');
    fetch(this.url)
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json();
      })
      .then((data: Frame[]) => {
        if (this.stopped) return;
        if (!Array.isArray(data) || data.length === 0) throw new Error('fixture is empty');
        this.frames = data;
        onStatus('live');
        onFrame(this.frames[0]);
        this.i = 1;
        this.timer = window.setInterval(() => {
          if (this.paused) return;
          onFrame(this.frames[this.i % this.frames.length]);
          this.i += 1;
        }, 1000 / this.fps);
      })
      .catch((e: unknown) => {
        if (!this.stopped) onStatus('error', e instanceof Error ? e.message : String(e));
      });
  }

  stop() {
    this.stopped = true;
    if (this.timer !== null) window.clearInterval(this.timer);
    this.timer = null;
  }

  setPaused(paused: boolean) {
    this.paused = paused;
  }
}

/** The §13.2 stream. Used whenever a URL is supplied (?ws=… or VITE_WS_URL).
 *  Reconnects with a bounded backoff so a backend restart doesn't need a
 *  page reload mid-shift. */
export class WebSocketSource implements FrameSource {
  readonly label = 'live corridor';
  private ws: WebSocket | null = null;
  private retry = 0;
  private retryTimer: number | null = null;
  private stopped = false;

  constructor(private url: string) {}

  start(onFrame: (f: Frame) => void, onStatus: (s: SourceStatus, d?: string) => void) {
    const open = () => {
      if (this.stopped) return;
      onStatus('connecting');
      const ws = new WebSocket(this.url);
      this.ws = ws;

      ws.onopen = () => {
        this.retry = 0;
        onStatus('live');
      };
      ws.onmessage = (ev) => {
        try {
          onFrame(JSON.parse(ev.data as string) as Frame);
        } catch {
          /* A malformed frame is dropped, not surfaced — the next one is 500ms
             away and an error toast per bad frame would bury the console. */
        }
      };
      ws.onerror = () => onStatus('error', 'stream error');
      ws.onclose = () => {
        if (this.stopped) return;
        onStatus('connecting', 'reconnecting');
        const delay = Math.min(500 * 2 ** this.retry, 8000);
        this.retry += 1;
        this.retryTimer = window.setTimeout(open, delay);
      };
    };
    open();
  }

  stop() {
    this.stopped = true;
    if (this.retryTimer !== null) window.clearTimeout(this.retryTimer);
    this.ws?.close();
    this.ws = null;
  }
}

/** A URL from `?ws=` or `VITE_WS_URL` selects the live stream; otherwise the
 *  recorded session, which needs no backend at all. */
export function createSource(): FrameSource {
  const fromQuery = new URLSearchParams(window.location.search).get('ws');
  const url = fromQuery ?? (import.meta.env.VITE_WS_URL as string | undefined);
  return url ? new WebSocketSource(url) : new FixtureSource();
}

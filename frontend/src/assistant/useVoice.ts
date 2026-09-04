import { useCallback, useEffect, useRef, useState } from 'react';

/** Browser SpeechRecognition, dictation only — it produces a transcript and
 *  nothing else; the intent parser is local (§7.5). Note this is NOT on-device:
 *  in Chrome it streams audio to Google's speech service. */
type SR = {
  continuous: boolean; interimResults: boolean; lang: string;
  start(): void; stop(): void;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
};

function ctor(): (new () => SR) | undefined {
  const w = window as unknown as Record<string, new () => SR>;
  return w.SpeechRecognition ?? w.webkitSpeechRecognition;
}

export function useVoice(onTranscript: (text: string) => void) {
  const [listening, setListening] = useState(false);
  const supported = typeof window !== 'undefined' && !!ctor();
  const ref = useRef<SR | null>(null);
  const cb = useRef(onTranscript);
  cb.current = onTranscript;

  useEffect(() => () => ref.current?.stop(), []);

  const toggle = useCallback(() => {
    const Ctor = ctor();
    if (!Ctor) return;
    if (listening) { ref.current?.stop(); setListening(false); return; }

    const sr = new Ctor();
    sr.continuous = false;
    sr.interimResults = false;
    sr.lang = 'en-IN';
    sr.onresult = (e) => {
      const text = e.results[0]?.[0]?.transcript ?? '';
      if (text) cb.current(text);
    };
    // A denied mic or a dropped connection just stops listening — the officer
    // still has the text field, so there is nothing to escalate.
    sr.onerror = () => setListening(false);
    sr.onend = () => setListening(false);
    ref.current = sr;
    sr.start();
    setListening(true);
  }, [listening]);

  return { supported, listening, toggle };
}

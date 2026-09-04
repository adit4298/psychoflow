import { useCallback, useEffect, useRef, useState } from 'react';
import { CornerDownLeft, Mic, RotateCcw, Sparkle } from 'lucide-react';
import { useCommands } from '../assistant/useCommands';
import { useVoice } from '../assistant/useVoice';
import { apiBase, useAssistant } from '../assistant/useAssistant';
import { parseIntent } from '../assistant/intent';
import { useStore, type ActionRecord } from '../store/store';
import s from './Assistant.module.css';

const PILLS = [
  { label: 'Hold a phase', text: 'hold N-S green at J2 for 20 seconds' },
  { label: 'Set a cycle timer', text: 'priority lane 1 at J2 for 120 seconds' },
  { label: 'Corridor status', text: 'corridor status' },
  { label: 'Switch to Manual', text: 'switch to manual' },
];

/** Which engine turns speech into text. Read once from the backend, because
 *  only it knows what `--stt` was passed. `webspeech` means the BROWSER
 *  transcribes and we POST the words; anything else means we record a clip and
 *  the backend transcribes it. Unknown until that fetch lands, and the mic
 *  behaves as `webspeech` until then — the browser path needs no upload, so it
 *  is the safe assumption if the backend never answers. */
type Provider = 'webspeech' | 'whisper' | 'sarvam';

function ActionCard({ action, onUndo }: { action: ActionRecord; onUndo: () => void }) {
  return (
    <div className={s.action} data-undone={action.undone || undefined}>
      <span className={s.actionText}>{action.intent.summary}</span>
      {action.undone ? (
        <span className={s.undone}>undone</span>
      ) : (
        <button type="button" className={s.undo} onClick={onUndo}>
          <RotateCcw size={13} strokeWidth={2.1} aria-hidden="true" /> Undo
        </button>
      )}
    </div>
  );
}

export function Assistant() {
  const [text, setText] = useState('');
  const [provider, setProvider] = useState<Provider>('webspeech');
  const [model, setModel] = useState<string | null>(null);
  const chat = useStore((st) => st.chat);
  const commands = useCommands();
  const assistant = useAssistant();
  const logRef = useRef<HTMLDivElement>(null);

  /** Text and voice converge here, exactly as §7.5 requires: one path,
   *  `raw text → local intent parse → dispatch`. The mic only produces text.
   *  With no backend this falls through to the local rule parser, which is
   *  what DESIGN.md designates for the fixture build. */
  const submit = useCallback(async (raw: string) => {
    if (!(await assistant.submitText(raw))) commands.submit(raw);
  }, [assistant, commands]);

  // The browser's own recogniser, used only under the `webspeech` provider.
  const { supported, listening, toggle } = useVoice((t) => { void submit(t); });

  useEffect(() => {
    const base = apiBase();
    if (!base) return;
    let cancelled = false;
    fetch(`${base}/voice/status`)
      .then((r) => (r.ok ? r.json() : null))
      .then((st) => {
        if (cancelled || !st) return;
        setProvider(st.stt_provider as Provider);
        setModel(st.model as string);
      })
      // A failed status fetch changes nothing the officer can see: the text
      // field is the primary input and does not depend on it.
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' });
  }, [chat.length, assistant.thinking]);

  const send = () => { const t = text; setText(''); void submit(t); };

  const undo = useCallback((action: ActionRecord) => {
    // Send the backend's inverse call first, then roll the local view back.
    // The card reads "undone" either way: the officer's intent is recorded
    // whether or not the corridor was reachable at that instant.
    void assistant.undoRemote(action);
    commands.undo(action);
  }, [assistant, commands]);

  const usesBrowserMic = provider === 'webspeech';
  const micOn = usesBrowserMic ? listening : assistant.recording;
  const micEnabled = usesBrowserMic ? supported : assistant.live;
  const onMic = () => (usesBrowserMic ? toggle() : void assistant.recordAndSubmit());

  // §7.5: the 0.5-3 s a real local parse takes is CORRECT AND VISIBLE. An
  // instant reply would mean a lookup table, which is the thing being
  // replaced — so the wait is shown, and so is what it actually cost.
  const willParse = text.trim().length > 0
    && (assistant.live || parseIntent(text) !== null);

  return (
    <section className={s.card} aria-label="Assistant">
      <header className={s.head}>
        <span className={s.orb} aria-hidden="true"><Sparkle size={13} strokeWidth={2.2} /></span>
        <h2 className={s.title}>How can I help, officer?</h2>
        <span
          className={s.badge}
          title={assistant.live
            ? `Intent parsing runs on ${model ?? 'a local model'} via Ollama. `
              + `Speech-to-text: ${provider}.`
            : 'No backend connected — commands are parsed by the built-in rule '
              + 'parser and applied to this view only.'}
        >
          {assistant.live ? (model ?? 'local model') : 'offline parser'}
        </span>
      </header>

      <div className={s.log} ref={logRef} aria-live="polite">
        {chat.length === 0 ? (
          <p className={s.hint}>
            Type or speak a decision and it runs exactly as if you had clicked it.
          </p>
        ) : (
          chat.map((turn) => (
            <div key={turn.id} className={s.turn} data-role={turn.role}>
              <p className={s.bubble}>{turn.text}</p>
              {turn.action && (
                <ActionCard action={turn.action} onUndo={() => undo(turn.action!)} />
              )}
            </div>
          ))
        )}
        {assistant.thinking && (
          <div className={s.turn} data-role="console">
            <p className={s.thinking}>
              <span className={s.dots} aria-hidden="true"><i /><i /><i /></span>
              Working it out&hellip;
            </p>
          </div>
        )}
      </div>

      {assistant.lastMs !== null && !assistant.thinking && (
        <p className={s.meta}>
          Parsed locally in {(assistant.lastMs / 1000).toFixed(1)}&thinsp;s
        </p>
      )}

      <ul className={s.pills}>
        {PILLS.map((p) => (
          <li key={p.label}>
            <button type="button" className={s.pill} onClick={() => void submit(p.text)}>
              {p.label}
            </button>
          </li>
        ))}
      </ul>

      <form
        className={s.inputRow}
        onSubmit={(e) => { e.preventDefault(); send(); }}
      >
        <input
          className={s.input}
          name="command"
          autoComplete="off"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={micOn ? 'Listening…' : 'Hold N–S green at J2 for 20 seconds'}
          aria-label="Command"
        />
        <button
          type="button"
          className={s.mic}
          data-listening={micOn || undefined}
          onClick={onMic}
          disabled={!micEnabled || assistant.thinking}
          aria-pressed={micOn}
          title={micEnabled
            ? (usesBrowserMic
              ? 'Dictate a command (the browser transcribes — not on-device)'
              : `Dictate a command (transcribed by ${provider})`)
            : 'Speech recognition unavailable'}
        >
          <Mic size={16} strokeWidth={2} aria-hidden="true" />
          <span className="visually-hidden">Dictate a command</span>
        </button>
        <button
          type="submit"
          className={s.send}
          disabled={!text.trim() || assistant.thinking}
          data-ready={willParse || undefined}
        >
          <CornerDownLeft size={16} strokeWidth={2} aria-hidden="true" />
          <span className="visually-hidden">Send</span>
        </button>
      </form>
    </section>
  );
}

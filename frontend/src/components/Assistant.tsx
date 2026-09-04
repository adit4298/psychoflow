import { useEffect, useRef, useState } from 'react';
import { CornerDownLeft, Mic, RotateCcw, Sparkle } from 'lucide-react';
import { useCommands } from '../assistant/useCommands';
import { useVoice } from '../assistant/useVoice';
import { parseIntent } from '../assistant/intent';
import { useStore, type ActionRecord } from '../store/store';
import s from './Assistant.module.css';

const PILLS = [
  { label: 'Hold a phase', text: 'hold N-S green at J2 for 20 seconds' },
  { label: 'Set a cycle timer', text: 'priority lane 1 at J2 for 120 seconds' },
  { label: 'Corridor status', text: 'corridor status' },
  { label: 'Switch to Manual', text: 'switch to manual' },
];

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
  const chat = useStore((st) => st.chat);
  const { submit, undo } = useCommands();
  const { supported, listening, toggle } = useVoice((t) => submit(t));
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' });
  }, [chat.length]);

  const send = () => { submit(text); setText(''); };
  const willParse = text.trim().length > 0 && parseIntent(text) !== null;

  return (
    <section className={s.card} aria-label="Assistant">
      <header className={s.head}>
        <span className={s.orb} aria-hidden="true"><Sparkle size={13} strokeWidth={2.2} /></span>
        <h2 className={s.title}>How can I help, officer?</h2>
      </header>

      <div className={s.log} ref={logRef}>
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
      </div>

      <ul className={s.pills}>
        {PILLS.map((p) => (
          <li key={p.label}>
            <button type="button" className={s.pill} onClick={() => submit(p.text)}>
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
          placeholder={listening ? 'Listening\u2026' : 'Hold N\u2013S green at J2 for 20 seconds'}
          aria-label="Command"
        />
        <button
          type="button"
          className={s.mic}
          data-listening={listening || undefined}
          onClick={toggle}
          disabled={!supported}
          aria-pressed={listening}
          title={supported ? 'Dictate a command' : 'Speech recognition unavailable in this browser'}
        >
          <Mic size={16} strokeWidth={2} aria-hidden="true" />
          <span className="visually-hidden">Dictate a command</span>
        </button>
        <button type="submit" className={s.send} disabled={!text.trim()} data-ready={willParse || undefined}>
          <CornerDownLeft size={16} strokeWidth={2} aria-hidden="true" />
          <span className="visually-hidden">Send</span>
        </button>
      </form>
    </section>
  );
}

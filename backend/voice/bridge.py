"""§14 voice bridge — one utterance in, one operator-facing outcome out.

    audio bytes ──► stt.get_stt(provider)  ─┐
                                            ├─► transcript (English)
    browser text ───────────────────────────┘
                     │
                     ├─ READ-ONLY question?  ──► answered from the published
                     │                            snapshot / the last §13.2
                     │                            frame. NOTHING is dispatched.
                     │
                     └─ otherwise ──► VoiceIntentAgent.parse()   [local Gemma]
                                        │
                                        └──► control_api.dispatch()
                                              [allowlist, then bounds, then §10]

WHY THIS MODULE EXISTS SEPARATELY FROM `intent_agent.py`
--------------------------------------------------------
`VoiceIntentAgent.handle()` parses AND dispatches in one call. That is right
for a command and wrong for a question: "what's the wait time" and "why did J2
just switch" are answerable from state the backend already publishes, and
routing them through a control call would put a mutation on the queue in order
to answer a question. So `intent_agent.parse()` is the pure half, and this
module decides — before any dispatch — whether the utterance is a command.

`get_stats` is on `CONTROL_FUNCTIONS` and `dispatch()` handles it harmlessly,
but it is still answered here from `state.snapshot_stats()` directly. Same
data, one less moving part, and it makes "read-only means read-only" a property
of this code rather than of one function's implementation elsewhere.

THE SAFETY ARGUMENT IS UNCHANGED AND LIVES DOWNSTREAM
----------------------------------------------------
This module adds no authority. Everything it dispatches goes through
`control_api.dispatch()`, which refuses any name outside `CONTROL_FUNCTIONS`
*before* binding an argument, range-checks every operator-supplied number, and
queues the result for the sim thread, where §10's validator still gates it. A
mis-parse's worst case remains a valid call to one of nine bounded functions an
operator could have clicked.

FAIL CLOSED. An unparsed intent, a function off the allowlist, or a
transcription that produced nothing: the operator sees `NOT_UNDERSTOOD`, ONE
line is logged, and NO action is taken. Never a guessed function, never a
guessed argument.
"""

from __future__ import annotations

import json
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from backend.control_api import (
    CONTROL_FUNCTIONS,
    LANE_BIAS_DURATION_RANGE_S,
    ControlState,
    dispatch,
)
from backend.voice import stt
from backend.voice.intent_agent import VoiceIntentAgent
from backend.voice.intents import RANGE_ERROR_MARKER

#: Allowlisted functions that only READ. Answered from the snapshot, never
#: dispatched. Intersected with the allowlist so that a rename in
#: `control_api` shows up here as an empty set rather than as silent
#: dispatching of something meant to be read-only.
READ_ONLY_FUNCTIONS = frozenset({"get_stats"}) & frozenset(CONTROL_FUNCTIONS)
assert READ_ONLY_FUNCTIONS, "get_stats left the control allowlist"

#: DESIGN.md §7.5's wording, which is what the panel renders. `intent_agent`
#: keeps §14's own verbatim string for its harness; this is the operator copy,
#: and it names a command that works — "not understood" with no example leaves
#: the officer guessing at the vocabulary in front of an audience.
NOT_UNDERSTOOD = (
    "Didn't catch a command — try “hold N–S green at J2 for 20 "
    "seconds”."
)

#: A why-question is intercepted BEFORE the model runs: it is not a control
#: call, so handing it to a function-calling prompt can only produce a wrong
#: one. Needs an explanation word AND a decision word, so "why is lane 3 so
#: slow" still goes to the model rather than being answered with a phase change.
_WHY_RE = re.compile(
    r"\b(why|how come|explain|what happened|reason)\b.{0,60}?"
    r"\b(switch|switched|change|changed|decision|decide|decided|green|phase|"
    r"pick|picked|choose|chose)\b",
    re.IGNORECASE | re.DOTALL,
)
_JUNCTION_RE = re.compile(r"\b(?:j\s*|junction\s+)([123])\b", re.IGNORECASE)

#: Ring size for the in-memory command log the panel and rehearsal review read.
_LOG_MAXLEN = 200


@dataclass
class CommandLog:
    """Every utterance, understood or not (§14: log the miss for review).

    Deliberately NOT the `DecisionLog`: that one is per-episode and owned by
    the sim thread, which replaces it at every reset (CLAUDE.md §8). The voice
    layer must not hold one, so it keeps its own ring and hands the sim thread
    a `record_voice` payload instead.
    """

    path: Path | None = None
    entries: deque = field(default_factory=lambda: deque(maxlen=_LOG_MAXLEN))

    def record(self, row: dict) -> dict:
        entry = {"at": round(time.time(), 3), **row}
        self.entries.append(entry)
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, default=str) + "\n")
            except OSError:
                pass    # a log that cannot write must never break the demo
        return entry


class VoiceBridge:
    """Transcript or audio -> dispatch / read-only answer / fail-closed no-op.

    `agent` is injectable so the whole bridge runs against a pinned parse with
    no Ollama at all — that is what makes the read-only routing and the
    fail-closed paths deterministic in a test, rather than dependent on what a
    4B model happened to say on that run.
    """

    def __init__(
        self,
        state: ControlState,
        *,
        agent: VoiceIntentAgent | None = None,
        stt_provider: str = stt.DEFAULT_STT_PROVIDER,
        stt_client=None,
        tts=None,
        log_path: str | Path | None = None,
        **agent_kwargs,
    ) -> None:
        self.state = state
        self.agent = agent if agent is not None else VoiceIntentAgent(
            state, **agent_kwargs)
        self.stt_provider = stt_provider
        self._stt = stt_client    # built lazily: a whisper client loads no
        self.tts = tts            # model until something is actually spoken
        self.log = CommandLog(Path(log_path) if log_path else None)
        self._frame: dict | None = None

    # -- wiring -----------------------------------------------------------
    def stt_client(self):
        """The configured provider, built on first use."""
        if self._stt is None:
            self._stt = stt.get_stt(self.stt_provider)
        return self._stt

    def observe_frame(self, frame) -> None:
        """Keep the latest §13.2 frame so "why did it switch" has an answer.

        Called from `backend/main.py`'s frame sink, not from inside the sim
        loop. It holds ONE reference and copies nothing, so it can neither slow
        the loop nor retain an episode's history.
        """
        if isinstance(frame, dict):
            self._frame = frame

    def warmup(self) -> dict:
        """Pay the model's ~18s cold start at server start, not mid-demo."""
        return self.agent.warmup()

    def status(self) -> dict:
        client = self._stt
        return {
            "stt_provider": self.stt_provider,
            "stt": client.status() if client is not None else None,
            "tts": self.tts.status() if self.tts is not None else None,
            "model": self.agent.model,
            "commands_logged": len(self.log.entries),
        }

    # -- read-only answers (nothing below dispatches) ----------------------
    def _answer_stats(self) -> str:
        stats = self.state.snapshot_stats()
        if not stats:
            return "The corridor has not published a snapshot yet."
        metrics = stats.get("metrics_snapshot") or {}
        lanes = stats.get("lanes") or {}
        parts = [f"{len(lanes)} lanes reporting"]
        wait = metrics.get("mean_wait_max", stats.get("mean_wait_max"))
        if wait is not None:
            try:
                parts.append(f"mean max wait {float(wait):.1f}s")
            except (TypeError, ValueError):
                pass
        for key, label in (("starvation_events_total", "starvation events"),
                           ("throughput_total", "vehicles cleared")):
            value = metrics.get(key, stats.get(key))
            if value is not None:
                parts.append(f"{value} {label}")
        return "; ".join(parts) + "."

    def _answer_why(self, transcript: str) -> str:
        """Explain the last decision from the §13.2 frame's own narration.

        §12.2's narration is already the answer to this question, computed by
        `explainability.narrator` on the frame. Re-deriving one here would be a
        second explanation of the same event that could disagree with the
        decision log the judge is looking at.
        """
        frame = self._frame
        if not isinstance(frame, dict):
            return ("No decision has come through yet — the corridor has "
                    "not reported a frame.")
        decision = frame.get("decision") or {}
        narration = frame.get("narration")
        jid = decision.get("junction_id")
        asked = _JUNCTION_RE.search(transcript or "")
        wanted = f"J{asked.group(1)}" if asked else None

        bits: list[str] = []
        if wanted and jid and wanted != jid:
            # Say so rather than answering about a different junction. The
            # frame carries ONE decision per step (§13.2's `_emit_junction`
            # selector), so the one asked about may simply not be on it.
            bits.append(f"The latest decision on the wire is {jid}, "
                        f"not {wanted}:")
        if narration:
            bits.append(str(narration))
        reason = decision.get("reason")
        if reason and not narration:
            bits.append(f"{jid or 'the corridor'} — reason: {reason}")
        sim_time = frame.get("sim_time")
        if sim_time is not None:
            try:
                bits.append(f"(t={float(sim_time):.0f}s)")
            except (TypeError, ValueError):
                pass
        return " ".join(bits) if bits else "No narration on the last frame."

    # -- undo --------------------------------------------------------------
    @staticmethod
    def _undo_for(function: str, args: dict, previous_mode: str) -> dict | None:
        """The inverse control call, for the panel's Undo button — or None.

        Returned rather than executed: Undo is the operator's decision, made
        after they have seen what happened. Only genuinely reversible calls get
        one. `trigger_emergency`, `inject_incident` and `set_topology` return
        None on purpose — an emergency corridor already granted, an incident
        already reported to the twin and a rebuilt network are not undone by
        calling something else, and offering a button that pretends otherwise
        would be worse than offering none.
        """
        if function == "set_mode":
            return {"function": "set_mode", "args": {"mode": previous_mode}}
        if function == "force_phase":
            return {"function": "clear_override",
                    "args": {"junction_id": args.get("junction_id")}}
        if function == "set_lane_bias":
            # Weight 1.0 is the neutral multiplier (`agents/rule_based.py`
            # multiplies the lane's whole §9.1 score by it), and the shortest
            # legal duration retires it promptly. There is no "unset" call.
            return {"function": "set_lane_bias",
                    "args": {"lane_id": args.get("lane_id"), "weight": 1.0,
                             "duration_s": LANE_BIAS_DURATION_RANGE_S[0]}}
        if function == "set_baseline_mode":
            other = ("greedy" if args.get("baseline") == "psychoflow"
                     else "psychoflow")
            return {"function": "set_baseline_mode", "args": {"baseline": other}}
        return None

    # -- the pipeline ------------------------------------------------------
    def _blank(self, **over) -> dict:
        base = {
            "transcript": "", "language": None, "stt_provider": "text",
            "understood": False, "read_only": False, "applied": False,
            "message": NOT_UNDERSTOOD, "function": None, "args": {},
            "result": None, "assumptions": [], "reason": None,
            "model": self.agent.model, "latency_ms": 0, "stt_ms": 0,
            "parse_ms": 0, "undo": None, "speech": None,
        }
        base.update(over)
        return base

    def _finish(self, out: dict, started: float) -> dict:
        out["latency_ms"] = int((time.perf_counter() - started) * 1000)
        if self.tts is not None and out.get("message"):
            out["speech"] = self.tts.speak(out["message"])
        self.log.record({
            "transcript": out["transcript"],
            "language": out["language"],
            "stt_provider": out["stt_provider"],
            "function": out["function"],
            "args": out["args"],
            "understood": out["understood"],
            "read_only": out["read_only"],
            "applied": out["applied"],
            "reason": out["reason"],
            "latency_ms": out["latency_ms"],
        })
        return out

    def handle_text(self, text, *, language: str | None = None,
                    stt_provider: str = "text", stt_ms: int = 0) -> dict:
        """The single path every utterance converges on. Never raises."""
        started = time.perf_counter() - (stt_ms / 1000.0)
        transcript = stt.normalise_transcript(text)
        out = self._blank(transcript=transcript, language=language,
                          stt_provider=stt_provider, stt_ms=stt_ms)
        if not transcript:
            out["reason"] = "empty or unusable transcript"
            return self._finish(out, started)

        # READ-ONLY, INTERCEPTED BEFORE THE MODEL. A why-question has no
        # control call to make, so running the function-calling prompt on it
        # can only produce a wrong one.
        if _WHY_RE.search(transcript):
            out.update(understood=True, read_only=True,
                       message=self._answer_why(transcript), function="why")
            return self._finish(out, started)

        parsed = self.agent.parse(transcript)
        out["parse_ms"] = parsed.get("latency_ms", 0)
        out["assumptions"] = list(parsed.get("assumptions") or [])
        if parsed.get("unparsed", True):
            # FAIL CLOSED — no dispatch, one log line, the operator is told.
            out["reason"] = parsed.get("reason") or "could not parse a command"
            if RANGE_ERROR_MARKER in out["reason"]:
                # Heard perfectly, asked for something out of bounds. Show the
                # bound; "didn't catch a command" would be a lie and would send
                # the officer back to re-speaking a fine sentence.
                out["message"] = out["reason"][0].upper() + out["reason"][1:] + "."
            self.agent.misses.record(transcript, out["reason"],
                                     parsed.get("raw", ""))
            return self._finish(out, started)

        function, args = parsed["function"], parsed["args"]
        out.update(understood=True, function=function, args=args)

        if function in READ_ONLY_FUNCTIONS:
            # Answered from the published snapshot. NOT dispatched.
            out.update(read_only=True, message=self._answer_stats())
            return self._finish(out, started)

        previous_mode = self.state.mode        # read BEFORE the mutation, so
        outcome = dispatch(self.state, function, args)   # Undo can restore it
        out["result"] = outcome
        out["applied"] = outcome.get("applied") is True
        if out["applied"]:
            out["message"] = _echo(function, args, outcome)
            out["undo"] = self._undo_for(function, args, previous_mode)
        else:
            # Understood, but the control API declined it (out of range, no
            # checkpoint, Greedy not built yet). Surface ITS reason — "not
            # understood" would be a lie and would send the operator into
            # re-speaking a command that parsed perfectly.
            out["message"] = str(outcome.get("reason")
                                 or "the dashboard declined that command")
            out["reason"] = out["message"]
        return self._finish(out, started)

    def handle_audio(self, audio, *, language: str | None = None) -> dict:
        """Audio bytes -> STT -> `handle_text`. Fails closed on a bad clip."""
        started = time.perf_counter()
        try:
            heard = self.stt_client().transcribe(audio, language=language)
        except Exception:
            heard = None    # a provider must never take the panel down
        if not heard:
            out = self._blank(stt_provider=self.stt_provider,
                              reason="transcription produced nothing usable")
            return self._finish(out, started)
        return self.handle_text(heard["text"], language=heard["language"],
                                stt_provider=heard["provider"],
                                stt_ms=heard["latency_ms"])

    def handle_payload(self, payload) -> dict:
        """The browser's Web Speech POST body (see `stt.py`'s contract)."""
        event = stt.accept_web_speech_result(payload)
        if event is None:
            out = self._blank(stt_provider=stt.PROVIDER_WEBSPEECH,
                              reason="no usable final transcript in the payload")
            return self._finish(out, time.perf_counter())
        return self.handle_text(event.transcript,
                                language=(payload or {}).get("language"),
                                stt_provider=stt.PROVIDER_WEBSPEECH)


def _echo(function: str, args: dict, outcome: dict) -> str:
    """Short operator-facing confirmation. Names the RESOLVED lane, not the
    spoken number — the officer said "lane 3" and the corridor acted on
    `N1_J2_2`, and only one of those is checkable against the decision log."""
    try:
        if function == "set_mode":
            return f"Mode set to {args['mode']}."
        if function == "set_baseline_mode":
            return f"Controller set to {args['baseline']}."
        if function == "set_lane_bias":
            return (f"Lane {args['lane_id']} weighted ×{args['weight']:g} "
                    f"for {args['duration_s']:g}s.")
        if function == "trigger_emergency":
            return f"Emergency corridor requested for {args['lane_id']}."
        if function == "force_phase":
            return (f"{args['junction_id']} pinned to phase {args['phase']}; "
                    f"it applies at the next decision step and §10 still "
                    f"validates it.")
        if function == "clear_override":
            return f"Override cleared on {args.get('junction_id') or 'every junction'}."
        if function == "set_topology":
            return f"Corridor rebuilding as {outcome.get('topology_id')}."
        if function == "inject_incident":
            return (f"{args['incident_type']} reported at {args['junction_id']} "
                    f"on {', '.join(args['affected_lanes'])}.")
    except Exception:
        pass    # a formatting slip must not drop an action already applied
    return f"{function} applied."

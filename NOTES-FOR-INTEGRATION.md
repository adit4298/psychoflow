# NOTES FOR INTEGRATION

Changes that belong to files **outside** the writing branch's ownership. Nothing
here has been applied — each item names the file, why the voice branch could not
make the change itself, and exactly what to do.

---

## From `hackathon/voice` (Phase 11, §14) — 2026-09-03

The voice branch owns `backend/voice/` only. It **imports** from
`backend/control_api.py` and changes nothing there. Five items.

### 1. `backend/main.py` — mount the voice endpoint (REQUIRED)

There is no HTTP route into the voice layer yet. `VoiceIntentAgent` is a plain
object; the frontend panel needs something to POST to. Add:

```python
from backend.voice.intent_agent import VoiceIntentAgent

# one agent for the app's lifetime — it holds a lazily-created Ollama client
voice_agent = VoiceIntentAgent(state)

@app.post("/voice/utterance")
def voice_utterance(payload: dict):
    return voice_agent.handle_payload(payload).to_dict()
```

`handle_payload` takes the browser's POST body directly (shape and a reference
`SpeechRecognition` snippet are in `backend/voice/stt.py`'s docstring). It never
raises — every failure path returns `understood: false` with §14's
`"Command not understood, please try again"` and dispatches nothing.

**No new auth surface.** The endpoint reaches exactly
`control_api.CONTROL_FUNCTIONS` through `dispatch()`, which is the same
allowlist the existing REST routes go through, and it is subject to the same
loopback-by-default host guard.

**One thing this endpoint has that the button routes do not: real cost per
request.** Each utterance is ~1.5-2.6s of local model inference. The other
control routes are effectively free (a `queue.put`). If you add any rate
limiting to the §13 API, this is the route that most wants it — an unauthenticated
loopback endpoint that costs 2s of CPU per call is a different shape of exposure
from one that costs nothing. Not a blocker for a local demo; flagged because it
is the one place where §17's "local demo surface" reasoning is doing more work
than elsewhere.

### 2. `backend/main.py` — pre-warm the model at startup (REQUIRED for §14's done-bar)

Measured on this machine: **~18s cold start** for the first `gemma3:4b` request
(BUILD_LOG 2026-09-03 §6), against §14's "dashboard visibly reacts within ~2
seconds" bar. The **first spoken command of the demo will miss that bar badly**
unless the model is already resident.

```python
@app.on_event("startup")
def _warm_voice():
    threading.Thread(target=voice_agent.warmup, daemon=True).start()
```

Backgrounded on purpose — `warmup()` blocks for the cold start and must not
delay the server accepting connections. Add "voice model pre-warmed" to §20's
pre-event checklist.

### 3. `backend/sim_runner.py` — post voice actions to the §12.1 decision log

§12.2 says voice-triggered actions post to the same decision log, and
`DecisionLog.record_voice(sim_time, junction_id, transcript, action_taken)`
already exists for it. The voice layer deliberately does **not** call it: a
`DecisionLog` covers exactly ONE episode, `record_voice` raises on a `sim_time`
earlier than the highest already recorded, and `_reset_counters` REPLACES the
log at every episode boundary — so a voice thread holding a reference would
raise across the first reset (CLAUDE.md §8's monotonicity rule).

Instead, `VoiceResult.decision_log_payload()` returns the `transcript` /
`action_taken` kwargs, or `None` for a miss (no action taken = nothing to log).
The clean wiring is a small queue the sim thread drains alongside
`ControlState.pending`, stamping `sim_time` itself. Not built here because it
touches `sim_runner.py`.

### 4. Lane numbering — the voice/narrator off-by-one is REAL and DELIBERATE

**Voice "lane N" is 1-BASED** (`intents.VOICE_LANE_BASE = 1`); spoken "lane 3"
resolves to SUMO slot 2. **`explainability/narrator.py` renders 0-BASED slots**
(`entry.lane_slot`, no `+1`), so it prints "Lane 2" for the same lane. Full
reasoning is in `backend/voice/intents.py`'s module docstring; the short version
is that 0-based would make §14's own required demo command ("give lane 3 more
priority") **fail** on the demo corridor, since J2 has only 3 lanes.

**No change to `explainability/` is proposed** — that would move numbers already
recorded in Phase 8's verified figures.

**Recommendation for Phase 10 (frontend):** render the resolved **`lane_id`**,
not a number. Every `VoiceResult` carries it, plus an `assumptions` entry
spelling out the conversion (`"spoken lane 3 (1-based) -> SUMO slot 2 ->
N2_J2_2"`). If a number must be shown in the voice panel, show the spoken one
and the lane id together. Never show a bare number next to a narration line —
that is the only place the two conventions are visible side by side.

**Same rule for phases**: spoken "phase 2" is `force_phase(phase=1)`.

### 5. Two defaults the frontend should surface (judgement calls, not bugs)

* **Unqualified lanes resolve against `J2` / `north`.** "Give lane 3 more
  priority" names no junction and no approach, but `set_lane_bias` needs a
  concrete lane id. The resolver applies `DEFAULT_JUNCTION="J2"` /
  `DEFAULT_APPROACH="north"` and **discloses every fallback it used** in
  `result["assumptions"]`. Display those. `VoiceIntentAgent(..., strict_lanes=True)`
  turns the fallback off entirely, making an under-specified lane a fail-closed
  no-op that asks the operator to name a junction and an approach — safer, and
  it breaks §14's required demo command, which is why it is not the default.
* **`get_stats` returns the full lane dict** in `result`. `message` carries a
  one-line spoken-style summary; prefer that for the panel and keep the dict for
  a details view, or the WebSocket frame grows by ~36 lane records per query.

### 6. Optional, not required: the truly-local STT fallback

`faster-whisper` is **not installed** and §14 says to fall back to local Whisper
only if Web Speech proves unreliable in rehearsal with real background noise.
`stt.LocalWhisperSTT` is written and inert; if rehearsal needs it,
`pip install faster-whisper` and construct with `enabled=True`. **Say
"browser speech-to-text", not "local"** — Web Speech streams audio to Google
(CLAUDE.md §2).

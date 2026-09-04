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
# NOTES FOR INTEGRATION — `hackathon/integration`

**This file is the MERGE of two documents that were written independently on
two branches and conflicted (add/add) when those branches came together.** Both
are kept in full, in the order they were written. Neither was rewritten to
agree with the other, because the disagreement between them is itself the
record of how the two tracks converged.

| Part | Written by | Owns |
|---|---|---|
| **Part I** | `hackathon/agents-backend` | `agents/incident_priority.py`, `agents/incident_types.py`, `orchestrator/`, `backend/`, `frontend/`, their tests |
| **Part II** | `hackathon/vision-iot` | `iot/`, `perception/vision_detector.py`, `perception/vision_source.py`, `perception/incident_detector.py`, `tests/` |

## Reading order, and which side wins

Part I §A1/§A2/§A3 are **assumptions** agents-backend recorded about Track A
while Track A did not yet exist in its tree. Part II §9.1/§9.2/§9.3 are Track
A's **authoritative answers** to exactly those three.

> **Where Part I §A and Part II §9 disagree, §9 wins.** §A is retained verbatim
> as the historical record of what was assumed — not as current guidance.

The three resolutions, applied in Prompt 5 (see `docs/BUILD_LOG.md`):

| | assumed (§A) | **resolved (§9)** |
|---|---|---|
| **§A2 / §9.1** — vision factory | `make_vision_source(kind, *, seed)` | **`get_vision_source(mode="mock", **kwargs)`**; `detector` requires `source=<clip>` and raises without it |
| **§A1 / §9.2** — detector emergency | detector sets `emergency` | **advisory-only.** `emergency_vehicle_flag` never maps to `emergency`, never reaches `safety.validator`'s `forced_emergency_lanes`; the fail-closed `type_composition["ambulance"] > 0` path is kept |
| **§A3 / §9.3** — MQTT topics | four topic strings | **match character-for-character.** `fresh_s` is NOT produced by `iot/`; it is derived by the consumer as `now_sim_time - payload.sim_time` |

Everything else in both parts stands as written.

---
---

# PART I — from `hackathon/agents-backend`


Changes that belong to files **outside** the writing branch's ownership. Nothing
here has been applied — each item names the file, why the voice branch could not
make the change itself, and exactly what to do.

---

## ⚠️ ASSUMED, NOT DELIVERED — Track A (`hackathon/vision-iot`)

At the time Part 4a was built, **nothing from Track A exists in this repo.**
Verified by grep: no `perception/incident_detector.py`, no
`perception/vision_source.py`, no `iot` module content, no vision emergency
flag anywhere. The shapes below are therefore **this track's assumptions**,
recorded so Track A can either match them or tell us to change.

**ANSWERED — read Part II §9 before acting on any §A below.** Track A has
landed and §9.1/§9.2/§9.3 are its authoritative replies. Where the two
disagree, **§9 wins**: §A2's `make_vision_source` is really
`get_vision_source(mode, **kwargs)` (§9.1), and §A1's `emergency` key is
deliberately NOT fed by the detector's `emergency_vehicle_flag` (§9.2,
advisory-only). §A3's topics match exactly (§9.3). The §A text is kept
verbatim as the record of what was assumed, not as current guidance.

### A1. `perception.incident_detector` — assumed producer shape

The incident-priority agent **does not import Track A at all.** It takes a
plain `Sequence[Mapping]` as its `vision_events` argument, so there is no
import to stub and nothing to fail on. Track A adds a *source*, never a
*dependency*.

Assumed element shape = §7.2's vision shape plus exactly ONE added key:

```json
{
  "lane_id": "N1_J1_0",
  "vehicle_count": 7,
  "type_composition": {"bike":0,"auto":0,"car":6,"truck":0,"ambulance":1},
  "confidence": 0.91,
  "source": "incident_detector",
  "emergency": true
}
```

Keys actually read, and what happens when each is absent:

| key | required | absent / invalid behaviour |
|---|---|---|
| `lane_id` | **yes** | record dropped, reason in `TickResult.dropped_inputs` |
| `confidence` | **yes, for an emergency claim** | dropped — §7.2 already mandates this field, so requiring it adds no new assumption |
| `emergency` | no | falsey ⇒ not an emergency; falls back to `type_composition["ambulance"] > 0` |
| `source` | no | recorded as provenance only; **gates nothing** |
| `type_composition` | no | only the `emergency` fallback reads it |
| `vehicle_count` | no | **never read** — stated so Track A knows it is free to change |

Gate: `confidence >= 0.85` (`VISION_MIN_CONFIDENCE`), anchored to
`perception.vision_mock.CONFIDENCE_RANGE[0]` — every output the existing
mock produces clears it, and a real detector must clear the same bar the
mock already sets.

**Graceful degradation is total.** `vision_events=None` ⇒ the agent behaves
exactly as if Track A does not exist, and the emergency class stays fully
functional from §7.1's `type_composition["ambulance"]` (the same channel
`safety/validator.py` uses) plus `forced_emergency_lanes`.

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

`TickResult` fields: `sim_time`, `events`, `directives`, `preempted`,
`suppressed`, `dropped_inputs` — all tuples, all with `.to_dict()`.

### Caller contracts — three of these bite if ignored

1. **ONE agent per EPISODE — replace the instance, do not carry it across a
   reset.** `tick()` **RAISES `IncidentPriorityError`** on a backwards
   `sim_time`, mirroring `DecisionLog`. This is load-bearing, not defensive
   noise: `env.reset()` sends sim_time back to ~0, so a carried-over
   registry holds expiry deadlines that are all in the future, every
   response reads as permanently active, and the agent emits **nothing for
   an entire episode** — silently, while passing every smoke test.
   `backend/sim_runner.py` replaces it wherever `_reset_counters()` replaces
   `self._log`.
2. **Call `confirm()` after `apply()`.** It promotes `pending -> active`
   only where `result["applied"] is True`. Without it a rejected bias would
   record as active and never be retried. It **raises** on a
   directives/results length mismatch rather than `zip`-truncating and
   leaving the tail stuck pending.
3. **Pass the SAME `forced_emergency_lanes` frozenset** handed to
   `safety.validator.validate()` and `EmergencyClearanceCoordinator.observe()`
   — `sim_runner` already computes `forced = frozenset(self._forced)` once
   per step. Do not maintain a second copy.

### Guarantees worth relying on

* **Every directive names a function on `control_api.CONTROL_FUNCTIONS`**,
  and every `set_lane_bias` weight/duration is inside
  `LANE_BIAS_WEIGHT_RANGE` / `LANE_BIAS_DURATION_RANGE_S` **by
  construction** — the ranges are imported, and `boost_weight`/
  `suppress_weight` are closed-form in them. `control_api` can never reject
  one of ours on range. Asserted over every directive the done-bar produces.
* **No new actuation path.** The agent returns *descriptions*; only `apply()`
  touches a `ControlState`, and it contains zero policy. §10's validator
  remains the sole gate.
* **No SUMO / torch / numpy import** — asserted by a unit test. Importable in
  a voice-only or offline context, like `backend/control_api.py`.
* `dropped_inputs` is capped at `MAX_DROPPED_REPORTED` (32) plus an overflow
  count, because from Part 4c it rides the §13.2 wire and an oversized
  upstream feed must not be able to inflate every frame.

### Honest boundaries to carry wherever this is shown

* **`set_lane_bias` is INERT under `mode="auto"`** — the RL policy has no
  per-lane score, so accident / congestion / fairness are recorded and
  echoed with no effect while the trained policy drives.
  **`trigger_emergency` works in BOTH modes**, so the emergency class is
  fully functional either way. The incident-priority beats are demoed in
  **manual / Tier 0 mode**. There is deliberately no `force_phase` auto-mode
  fallback.
* An accident **SUPPRESSES** the blocked lane (floors at weight `0.1`, never
  zero — the lane keeps minimum service and §10 still protects it). Boosting
  an *alternate* lane to route around a blockage is a separate policy call
  and is deliberately not built.
* Preemption is a **neutralising re-issue** at weight `1.0` plus a registry
  mark — there is no `clear_lane_bias`. It works only because
  `sim_runner._apply_command` does `self._bias[lane_id] = (weight, expiry)`,
  **last write wins**. If `_bias` ever becomes additive, preemption silently
  stops working.
* The priority order is a **fixed ordinal policy** — explicit, total,
  deterministic and testable; not learned, not claimed optimal.

### STATED COUPLING — re-check if `prediction/spillover.py` changes

`CONGESTION_MIN_CONFIDENCE = 0.5` with a **strict `>`** is chosen because it
equals `spillover.CONFIDENCE_COLD_START` and, with today's constants
(`0.85 − 0.20 = 0.65 > 0.5`), uniquely means "no previous snapshot to
compute a rate from". So the rule reads *"ignore a cold-start forecast"*
while still admitting the incident-penalised `0.65` case — which is exactly
when you most want to act. **This equality breaks if spillover's incident
confidence penalty ever exceeds 0.35.**

### Duplicated literals (the no-SUMO tax)

`FAIRNESS_WAIT_S=90.0`, `CEILING_WAIT_S=120.0`, `MIN_GREEN_S=10.0`,
`CORRIDOR_ORDER=("J1","J2","J3")` are duplicated as literals because their
home modules pull in sumolib/traci. `_check_constant_drift()` in the
scenarios module compares each against its home module **when SUMO is
importable** and skips silently when it is not.

---

## 2. Orchestrator blackboard — `agent_activity` entry shape (Part 4b, DELIVERED)

Commands:

```
python -m orchestrator.selftest                      # W1-W9, NO SUMO  -> 34/34
venv/Scripts/python.exe sim/run_orchestrator_check.py   # O1 offline + O2 paired (SUMO)
```

Six named agents, each a THIN wrapper over a module that already ran:

| agent | wraps | kind it emits |
|---|---|---|
| `Detection` | `perception/lane_sensor.py` (§7.1) | `observation` |
| `Vision` | `perception/vision_mock.py` (§7.2) | `observation` |
| `Prediction` | `prediction/spillover.py` + `incident_impact.py` (§8.1/§8.2) | `forecast` |
| `IncidentPriority` | `agents/incident_priority.py` | `arbitration` |
| `Control` | `agents/rule_based.py` / the deployed PPO policy | `action` |
| `Supervisor` | `safety/validator.py` (§10) | `veto` |

### The `agent_activity` entry shape

`agent_activity` is a **list of these, for THIS ROUND ONLY** (6-9 entries) —
the frontend accumulates; the blackboard keeps the full episode. **ADDITIVE
and omitted entirely** when the orchestrator is off or has latched off, so no
consumer handles an empty list. Part 3's documented minimum
`{agent, said, at}` is a **strict subset**, so a naive consumer works
unchanged.

```json
{
  "agent":  "Supervisor",
  "role":   "reports the §10 overrides that ALREADY fired inside env.step() — a RECORD of a veto, not a veto power",
  "wraps":  "safety/validator.py",
  "kind":   "veto",
  "said":   "§10 VETO at J2: emergency_override — E1_J2_0 waited 4.0s; phase 0 -> 1 (applied).",
  "at":     1840.0,
  "step":   368,
  "detail": {"junction_id": "J2", "rule": "emergency_override", "lane_id": "E1_J2_0",
             "wait_s": 4.0, "from_slot": 0, "to_slot": 1, "outcome": "applied"}
}
```

* `ENTRY_KEYS` is pinned as a frozenset in `orchestrator/types.py` (the trick
  `run_shadow_advisor_check.py` uses for `SHADOW_KEYS`), so a silent field
  rename fails a check rather than a frontend.
* `kind` ∈ `observation | forecast | arbitration | action | veto | idle`.
  It is **load-bearing, not decoration** — it is what makes the veto
  assertion machine-checkable. `idle` exists so **every agent emits at least
  one line every round**, making "six live rows" a per-round invariant.
* `said` is a **human-readable string on purpose.** The panel's job is "who
  said what" for a human, and the renderer is where the honesty lives — a
  `Supervisor` line reading "I vetoed" instead of "§10 vetoed" would be a lie
  the frontend was writing. Same reasoning that puts §12.2 narration in
  `explainability/narrator.py` rather than the dashboard.
* **Bounded by construction**, because one producer consumes a
  caller-supplied feed: `said` ≤ 240 chars, `detail` ≤ 8 keys of FLAT JSON
  scalars only (no nested containers — that is what makes the size bound
  provable), string values ≤ 120 chars, ≤ 4 entries per agent per round.
  Whole round asserted under a 16 KB budget (W3); measured ~9 KB worst case
  against a `digital_twin` field already in the tens of KB.
* `at` is `info["sim_time"]` — the SAME stamp `DecisionLog.record_step` gets,
  so a join between the blackboard and the §12.1 decision log is exact.

### Guarantees

* **The orchestrator cannot change the action, and that is a property of
  STATEMENT ORDER.** Its single call site in `_run_iteration` sits after
  `_pick_action()`, after `env.step()` (inside which §10 ran), after
  `record_step()`, `_coord.observe()`, `_update_metrics()` and
  `_assemble_frame()`. Measured: `sim/run_orchestrator_check.py --o2` runs
  two SimRunners sequentially at the same seed, one off one on, and the
  decision / executed-phase / throughput series are **identical on all 40
  frames**, with `agent_activity` absent from 40/40 off-frames and present in
  40/40 on-frames (the anti-vacuity half).
* **The Supervisor's veto is a RECORD, not authority.** §10 runs inside
  `env.step()`; the Supervisor reports `info["safety_overrides"]` verbatim.
  Pinned by W4c, which feeds it an override naming a lane that exists nowhere
  in the snapshot and asserts the lane id is still reported — something only
  a reporter can do.
* **Wrappers compute nothing.** The reviewable rule: a wrapper may filter,
  count, max, sort and format over its context; it may not introduce a
  threshold, weight, score or comparison against a constant. W7 enforces it
  by AST-scanning `wrappers.py` for numeric literals outside `{0,1}` and for
  imports of `apply`/`dispatch`/`validate`/`forecast`/`ControlState`.
* **Failure isolation** (`_shadow_advice`'s precedent): one broken wrapper is
  caught, logged once and LATCHED off — the other five keep reporting. An
  exception escaping `observe()` disables the whole orchestrator and the key
  stops being emitted. Nothing can take down the sim thread.
* **ONE orchestrator per EPISODE**, replaced in `_reset_counters()` beside
  `self._log`. The disabled set is carried FORWARD (a structurally broken
  wrapper is still broken next episode).
* **No SUMO / torch / numpy / random import** in the package (W7), which is
  what lets the whole blackboard run against a synthetic §7.6 snapshot.

### Honest boundaries

* **`IncidentPriority` is ADVISORY here.** The wrapper calls `tick()` only —
  never `apply()`, never `confirm()`. Dispatching would mutate
  `sim_runner._forced` and change what §10 does, breaking the additive
  guarantee. Consequence to accept: with nothing promoted to `STATUS_ACTIVE`,
  the same proposal is **re-reported each step while the state holds**. That
  repetition is truthful ("the arbitration still ranks this first");
  suppressing it would be new logic in a wrapper. Every such entry carries
  `detail.dispatched == False` and the word `ADVISORY` in `said`.
* **`Vision` is a MOCK and its `role` says so on every entry** — §7.2 runs no
  detection model. The panel must not launder that.
* **`Prediction` reports the POST-step forecast** the backend already
  computed — `_spillover_view.forecast()` is stateful and must be called
  exactly once per step, so a wrapper recomputing it would corrupt the next
  frame.
* Flags: `--no-orchestrator` (`backend/main.py`), `enable_orchestrator=`
  (`create_app` / `SimRunner`). Default ON. The OFF switch exists so O2 can
  run both arms; without it that check is impossible.

## 3. §13.2 additive frame keys (Part 4c, DELIVERED)

Three keys beyond the frozen five-key core, each **emitted ONLY when
non-empty** so no consumer ever handles an empty container, and each strictly
additive — the core five and `digital_twin` are untouched.

Measured, not asserted: 200 frames captured at seed 7 on (4,3,2) against a
`git worktree` at the pre-4c commit (`de3ed41`) — **the five-key core and the
ENTIRE `digital_twin`, including the §7.2 `vision` block, are identical on all
200 frames.** Keys added: `incident_alerts` only. Keys removed: none.

### `incident_alerts` — list, Track A's detector shape

```json
{"type": "emergency_vehicle", "junction": "J2", "approach": "east",
 "lane_index": 1, "distance_m": null, "distance_confidence": null,
 "severity": "high", "detected_at": 1840.0, "source": "lane_sensor"}
```

Pinned as `backend.frame_sources.ALERT_KEYS`. `type` is `emergency_vehicle` or
a §7.3 `INCIDENT_TYPES` value. `source` ∈ `incident_intake` (§7.3 reported,
incl. every operator `inject_incident`) | `lane_sensor` (§7.1 ambulance count
— the same channel §10 reads) | `operator` (§13.1 `trigger_emergency`) |
whatever Track A tags its own. **Detection wins over an operator force on
overlap**, the provenance rule §11.2's `trigger_source` already follows.
`lane_index` is **0-BASED**, matching `explainability/narrator.py`'s existing
`_lane_index` so it agrees with the narration on the same frame — the
0-vs-1-based reconciliation CLAUDE.md flags for Phase 11 is a *presentation*
choice the frontend owns. Bounded at `MAX_ALERTS` (32).

**⚠️ `distance_m` and `distance_confidence` are ALWAYS `null` from this
producer, and that is not a placeholder to fill in with a guess.** Distance
needs a fixed camera and a homography (`sim/media/README.md`); the twin has
neither, and the lane occupancy it does have is TraCI ground truth, not a
ranged detection. **Render "distance unknown", never 0.** Track A's real
detector is the only thing that can populate these.

`backend/frame_sources.py` is an ADAPTER — it detects nothing. Every alert is
a reshape of a fact another module already established, which is what lets the
frontend build against one stable shape before Track A lands and keeps working
unchanged after.

### `iot_sensors` — object, per lane

```json
{"N1_J1_0": {"source": "mqtt", "fresh_s": 1.4}}
```

Pinned as `backend.frame_sources.IOT_KEYS`. Fed from
`SimRunner.iot_telemetry`, which Track A sets to
`{lane_id: {"source": str, "last_seen_s": float}}`; `fresh_s` is derived as
`sim_time - last_seen_s`, floored at 0, so a producer only reports *when* it
last heard from a lane. A lane not in the live network is dropped.

**With no IoT source attached this is `{}` and the key is never emitted — and
it is absent from the recorded fixture for that reason.** Reporting the twin's
TraCI ground truth as `{"source": "mqtt", "fresh_s": 0.0}` would fabricate a
sensor network that does not exist. The shape is unit-asserted by
`sim/run_backend_smoke.py` check 8c against injected telemetry.

### `agent_activity`

See §2.

### `--vision-source {mock,detector}`

`backend/main.py --vision-source` -> `create_app(vision_source=)` ->
`SimRunner(vision_source=)`. Default `mock`.

**`mock` performs NO SWAP AT ALL** — `DigitalTwin.__init__` builds its own
`VisionMock(seed=seed)` and `SimRunner._apply_vision_source()` returns
immediately. The byte-identical guarantee is "no statement runs", not "an
equivalent statement runs": re-assigning even an identical `VisionMock` would
reseed it and perturb every recorded number. Asserted by smoke check 9 (the
twin keeps the very same object) and by the 200-frame worktree diff above.

`detector` swaps `env.twin.vision` for
`perception.vision_source.make_vision_source(...)` (§A2). This does **not**
modify `twin/digital_twin.py`, which this track does not own — the seam is an
attribute assignment onto the already-constructed twin, and any object with
`observe_all(readings)` works. **With Track A absent the import fails and the
runner falls back to the mock LOUDLY** rather than taking the demo down.

### `frontend/fixtures/recorded_session.json`

200 consecutive frames, seed 7, topology 432, ~5.7 MB, recorded by
`venv/Scripts/python.exe sim/record_fixture.py`. Regenerate with that command;
it drives `inject_incident` at frame 40 and `trigger_emergency` at frame 90
through the real §13.1 control path so the fixture exercises what a frontend
has to render:

| | |
|---|---|
| frames | 200, sim_time 15.0 -> 1010.0, monotonic (no episode boundary) |
| `agent_activity` | 200 frames, 1200 entries, all six agents, 4 Supervisor vetoes |
| `incident_alerts` | 121 frames, 125 alerts, both types, all three sources |
| `predictions` | 180 frames |
| `responder_messages` | 2 (one `operator`, one `detected`) |
| `iot_sensors` | **absent** — no IoT source exists yet (see above) |

⚠️ One fixture row shows the KNOWN OPEN `served_on_arrival` issue CLAUDE.md
records: the operator-triggered clearance reports `clearance_time_s = 0.0` /
`improvement_pct = 100.0` for a lane §10 had to clear inside the same decision
step. That is pre-existing and unrelated to Part 4c, but it is now visible in
a committed artifact, so **do not build a frontend claim on a 100% improvement
figure.** The `detected` row (3.0s / 89.3%) is the sound one.



---
---

# PART II — from `hackathon/vision-iot`

> Part II §9 is the authoritative resolution of Part I §A1/§A2/§A3. Where the
> two disagree, this part wins.

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

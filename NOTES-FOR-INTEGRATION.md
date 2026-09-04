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


Cross-track contracts for the parallel hackathon build. Anything recorded
here is either **owned by this track and safe to depend on**, or an
**assumption this track made about another track that has not landed yet**.

Owned by this track: `agents/incident_priority.py`, `agents/incident_types.py`,
`agents/incident_priority_scenarios.py`, `orchestrator/`,
`backend/sim_runner.py`, `backend/main.py`, `backend/control_api.py`, their
tests, and `frontend/fixtures/recorded_session.json` (write-only).

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

**The adapter is the CALLER's job** (`backend/sim_runner.py`), not the
agent's: whatever Track A exposes — `detect(frame) -> list[dict]` or
otherwise — `sim_runner` converts it to the list above.

### A2. `perception.vision_source` — assumed factory (Part 4c)

Part 4c plumbs a `--vision-source {mock,detector}` flag. The seam chosen
deliberately **does not modify `twin/digital_twin.py`** (not owned by this
track): `DigitalTwin` builds its own `VisionMock` and calls
`self.vision.observe_all(readings)`, so the swap is an attribute assignment
from `backend/sim_runner.py` onto the already-constructed twin.

Required duck-typed interface — anything with this one method works:

```python
class VisionSource:
    def observe_all(self, readings: dict[str, LaneReading]) -> dict[str, dict]:
        """lane_id -> §7.2 shape (lane_id/vehicle_count/type_composition/
        confidence/source). May carry the extra `emergency` key of A1."""
```

Assumed factory, if Track A provides one:

```python
perception.vision_source.make_vision_source(kind: str, *, seed: int | None) -> VisionSource
```

**`mock` must stay byte-identical to today**, so on `--vision-source mock`
this track performs **no swap at all** — the twin keeps the `VisionMock` it
built itself, with the seed it already had. Re-assigning even an identical
`VisionMock` would reseed it and perturb recorded numbers. Only `detector`
swaps.

### A3. IoT / MQTT telemetry — assumed per-lane freshness shape (Part 4c)

The §13.2 frame gains an `iot_sensors` key, per lane:

```json
{ "N1_J1_0": {"source": "mqtt", "fresh_s": 1.4} }
```

`source` is a free-form producer tag; `fresh_s` is seconds since that lane's
last telemetry. Emitted **only when non-empty**, so with Track A absent the
key never appears and no consumer has to handle an empty object.

---

## 1. `agents.incident_priority` — public interface (Part 4a, DELIVERED)

Commands:

```
python -m agents.incident_priority          # done-bar: 3 hand-scored scenarios
python -m tests.test_incident_priority      # 26 unit checks + the done-bar
```

Neither starts SUMO, loads a checkpoint, or constructs a `ControlState`, so
both are safe to run at any time — **no `sim.sumo_activity` beacon check is
needed or wanted**, same category as `training/scripts/stage4_contamination.py`.

### The priority policy

```python
EVENT_CLASSES = ("emergency", "accident", "major_congestion", "fairness")
CLASS_RANK    = {"emergency": 0, "accident": 1, "major_congestion": 2, "fairness": 3}
```

`arbitrate(events) -> tuple[Event, ...]` sorts by
`(CLASS_RANK, -severity_value, -urgency, corridor_index, lane_id)`.
The trailing `lane_id` makes the order **total**, so the classifier's
iteration order cannot leak into the result. `corridor_index` (J1<J2<J3) is
the same tiebreak `sim_runner._emit_junction()` already uses for §10
overrides.

### Public surface

```python
from agents.incident_priority import IncidentPriorityAgent, apply

agent = IncidentPriorityAgent(config=DEFAULT_CONFIG)

result = agent.tick(
    snapshot,                               # §7.6 twin snapshot
    sim_time=None,                          # defaults to snapshot["sim_time"]
    spillover=None,                         # §8.1 forecast() list
    incident_impacts=None,                  # §8.2 predict_incident_impact() list
    vision_events=None,                     # Track A, see A1 — optional
    forced_emergency_lanes=frozenset(),     # the SAME set handed to the validator
) -> TickResult

results = apply(state, result.directives)   # dispatches via control_api.dispatch
agent.confirm(result.directives, results, result.sim_time)

agent.reset()
agent.active_responses  # -> tuple[ActiveResponse, ...]
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

Written by the branch that owns `iot/`, `perception/vision_detector.py`,
`perception/vision_source.py`, `perception/incident_detector.py` and `tests/`.

Everything in this file is a change **outside those paths**. None of it has been
made. Each item says what the change is, why the owning branch could not make it,
and what breaks if it is skipped.

---

## 0. What this branch added, in one paragraph

Three things, all additive, none of them on any existing measured path:

| what | where | done-bar |
|---|---|---|
| Local MQTT ingestion (amqtt broker + paho clients) | `iot/` | `python -m tests.test_iot` -> 32/32 |
| Real YOLOv8n detector + `mock`/`detector` factory | `perception/vision_detector.py`, `perception/vision_source.py` | `python -m tests.test_vision_detector` -> 28/28 |
| Incident classification from tracks + flow state | `perception/incident_detector.py` | `python -m perception.incident_detector` -> 34/34 |

**No file under `env/`, `agents/`, `safety/`, `prediction/`, `coordinator/`,
`explainability/`, `twin/` or `backend/` was touched.** `perception/vision_mock.py`
is byte-for-byte unchanged and remains the default vision source.

---

## 1. `twin/digital_twin.py` — the vision source is hardwired to the mock

**This is the largest integration item and the one with a real design decision in
it. Read all of it before wiring anything.**

`DigitalTwin.__init__` currently does `self.vision = VisionMock(seed=seed)`
(`twin/digital_twin.py:73`) and `update()` calls
`self.vision.observe_all(readings)` (`twin/digital_twin.py:154`), where `readings`
is keyed by real SUMO `lane_id`.

Swapping in the factory is one line:

```python
from perception.vision_source import get_vision_source
self.vision = get_vision_source(vision_mode, seed=seed)          # mode="mock" default
```

**But do not do only that.** The two sources are not interchangeable in the way
the shared method signature suggests:

### 1a. A camera does not know a SUMO `lane_id`

`VisionMock.observe(reading)` re-emits the exact `lane_id` it was handed, because
it is reading the same TraCI ground truth. `VisionDetector` sees image-space ROI
polygons, one per **approach**. Its native output (`FrameObservation`) is keyed by
approach, and `observe(reading)` hands that approach's aggregate back under the
caller's `lane_id` with **`lane_fanout: True`** set on the observation.

That flag is the whole point: the per-lane split is **declared, not observed**.
Four lanes on the north approach will each report the approach's full count.

Three options, in order of honesty:

1. **Keep the detector as a parallel feed** (recommended). Leave `§7.1` /
   `lane_sensor` driving the twin, and surface the camera feed as its own panel.
   This is what BUILD_LOG's 2026-09-03 §1 scope boundary already committed to:
   *"a second, parallel perception source for the jury beat, not a replacement for
   §7.1."* Nothing below is needed.
2. **Fan out with a declared split ratio.** Add an explicit per-approach
   `lane_split` to the config and divide the aggregate by it. Still not a
   measurement, but at least the ratio is a stated assumption rather than an
   implied uniform one.
3. **Divide evenly and say nothing.** Do not. It puts an invented per-lane number
   on the dashboard with nothing marking it as invented.

### 1b. Three fields the detector cannot measure, and says so

The detector emits every `LaneReading` key (that is asserted by
`tests.test_vision_detector` D1), but three of them are structural zeros:

| field | detector value | why |
|---|---|---|
| `wait_time_current` | `0.0` | a camera cannot see accumulated waiting time |
| `wait_time_max_single_vehicle` | `0.0` | ditto |
| `starvation_flag` | `False` | derived from the above, so also not measured |

The observation carries **`wait_times_measured: False`** beside them. **Any
consumer that reads those three without checking that flag will read "not
observable from here" as "observed zero"** — and §9.1's starvation bonus and
§9.4's starvation penalty both key off exactly these. That is why the detector
must not drive `PsychoFlowEnv`: it would silently zero the fairness signal.

A fourth field is conditionally unmeasured. `halted_count` comes from the queue
estimate, which needs per-track speeds, which need a previous frame to difference
against. On the **first frame of a clip** — and any frame where the tracker
produced no ids — it falls back to the raw count, so `halted_count ==
vehicle_count`: every vehicle reported as queued. That is the right fallback (a
`0` would claim "no queue", a stronger claim than the truth), but it reads
identically to a genuinely stopped lane, so **`queue_measured: False`** rides
beside it. Check `queue_measured` before trusting `halted_count`, the same way
you check `wait_times_measured`.

### 1c. `type_composition` will always report `auto: 0, ambulance: 0`

Measured, not assumed: COCO's 80 classes give `person / bicycle / car /
motorcycle / bus / truck` and nothing else. There is no auto-rickshaw class and no
ambulance class. `bike <- bicycle+motorcycle`, `car <- car`, `truck <- truck+bus`;
`auto` and `ambulance` are present-and-zero by construction and listed in the
observation's `undetectable_types`.

This is the same class of silent breakage as the `base_vtype` bug: §9.2's
observation features `LF_TYPE_START+0..4` would take two permanently-zero channels.
The detector must not feed those features.

---

## 2. `perception/incident_intake.py` — no home for three detector fields

`incident_detector.detect_incident()` returns
`{type, junction, approach, lane_index, distance_m, distance_confidence, severity,
detected_at, source}`. `Incident` (§7.3) has none of `distance_m`,
`distance_confidence` or `lane_index`.

`incident_detector.to_intake_kwargs(out, lane_id=..., estimated_duration_s=...)`
already bridges this and is asserted end-to-end against the real
`IncidentIntake.report()` (`tests.test_incident_detector` F8). It makes two
mappings explicit that do **not** line up on their own:

- **`type`.** §7.3's enum is `lane_blocked / accident / roadworks`. The detector's
  kinds are `breakdown / accident / major_congestion`. `INTAKE_TYPE_FOR_KIND` folds
  `breakdown` and `major_congestion` onto `lane_blocked`.
- **`lane_id`.** The detector knows an approach and a lane index, never a SUMO lane
  id, so **the caller must supply it**. There is no derivation that would be
  correct.

`distance_m` / `distance_confidence` / `lane_index` are **dropped** by that mapping.
If they should survive into the twin — and for §11.2's responder messaging they
probably should, since "breakdown, north approach, 40m short of the J2 stop line"
is a materially better dispatch than "lane_blocked at J2" — then `Incident` needs
three new optional fields. That is an edit to `perception/incident_intake.py`,
which this branch does not own.

**If you add them, keep `distance_confidence` beside `distance_m`.** It is not
decoration: the detector reports `distance_m = None, distance_confidence = 0.0`
when no geometry was supplied, and a consumer that drops the confidence will
render an approximation from a junction-centre fallback identically to an exact
stop-line measurement.

---

## 3. `backend/sim_runner.py` — where the MQTT feed would attach

Not required for the demo; recorded so the shape is not re-derived.

`iot/subscriber.py` gives `latest_counts()` (keyed by `lane_id`) and
`latest_camera()` (keyed by `junction_id`), both newest-wins over a bounded buffer.
`LaneCountsPayload.to_lane_reading_dict()` emits exactly `LaneReading.to_dict()`'s
key set, so it drops into anything that consumes a §7.1 reading.

**The standing TraCI-single-thread rule still applies.** `iot/subscriber.py` runs
paho's own network thread; it must publish into a lock-protected cache that the
sim thread drains between decision steps, exactly as `ControlState.pending`
already does. **Never call `env.step()` / `reset()` or anything TraCI-touching
from an MQTT callback.**

---

## 4. Security posture of `iot/` — matched to `backend/`'s, and it has the same limit

The broker mirrors `backend/main.py`'s rules deliberately:

- **Loopback by default.** `IoTBroker` refuses a non-loopback bind unless
  `allow_lan=True`; `python -m iot.broker` refuses `--host` without `--allow-lan`
  and prints a warning banner when given both.
- **Everything inbound is validated.** `iot/schema.py`'s `decode()` enforces a
  64KiB cap before parsing, rejects non-UTF-8, non-JSON and non-object bodies,
  **rejects unknown fields rather than ignoring them**, validates every field
  against §7.1/§7.3/§7.4's enums, and refuses a message whose body names a
  different junction or lane than its own topic.
- **Topic levels are allowlisted** (`iot/topics.py::validate_segment`) before any
  id is interpolated, so a `junction_id` of `#` or `J1/+` cannot widen a
  subscription or forge a level.

**The limit is the same as §13's and must be said out loud the same way: the
broker runs anonymous auth with no TLS and is a LOCAL DEMO SURFACE.** Any process
on the machine can publish to any `psychoflow/` topic. The decoder is what makes
that survivable; it is not authentication and does not pretend to be. If this ever
needs to leave the machine, that is a real auth design, not an `--allow-lan` flag.

---

## 4b. `.gitignore` needs three more entries — a stray commit already tripped this

A commit on this branch, `8926dcc` ("iot"), swept **39 unrelated tooling files**
into version control alongside this branch's work: `.agents/skills/**`,
`.claude/skills/**` and `skills-lock.json`, ~13,000 lines of agent-skill markdown
that has nothing to do with PsychoFlow. This branch's own commit untracks them
(`git rm --cached`, files left on disk), but **nothing stops the next `git add -A`
from doing it again**, and `8926dcc` is still in the history that reaches
`hackathon/integration`.

`.gitignore` is outside this branch's ownership, so the lines were not added.
They should be, next to the existing `ECC/` entry, which exists for exactly this
reason (a third-party clone that a `git add -A` would have swallowed):

```gitignore
# Agent-skill tooling — not part of PsychoFlow, same reason as ECC/ above.
.agents/
.claude/skills/
skills-lock.json
```

Note `.claude/settings.local.json` is already ignored; `.claude/skills/` is not,
so ignoring the whole of `.claude/` would be a wider change than needed.

---

## 5. Dependency pins

`ultralytics 8.4.138`, `opencv-python 5.0.0`, `paho-mqtt 2.1.0`, `amqtt 0.12.0`
are installed in the venv and pinned **nowhere**. There is no `requirements.txt`
or `pyproject.toml` at the repo root. A fresh clone cannot reproduce this branch.
Whoever owns dependency manifests should add these four plus the existing stack.

Two amqtt/paho facts worth carrying, both learned by measurement, both documented
in `iot/broker.py`'s docstring:

- `amqtt.broker.Broker` **must be constructed inside a running event loop** — its
  `__init__` calls `asyncio.get_running_loop()`.
- **`plugins={}` does not mean "defaults"** — it removes `AnonymousAuthPlugin` and
  the broker then refuses every client with `Not authorized`. The failure is a
  clean CONNACK rejection with no broker-side error, so it reads as a client bug.

---

## 6. `perception/vision_detector.py` is 968 lines — over the 800-line ceiling

Stated as a deliberate, temporary exception rather than left for a reviewer to
find. The house rule (`coding-style.md`, `code-review.md`) puts a soft
maintainability ceiling at 800 lines and rates an *unexplained* overrun MEDIUM.

The measured split is **997 total = ~590 executable + ~210 docstring + ~65 comment
+ ~132 blank**, so most of the overrun is the "why" documentation this repo runs
on. That does not make it fine — ~590 executable lines in one module is still
large.

**The obvious fix was not taken, on purpose.** The file has three clean seams and
the config layer (`VisionConfigError`, `ApproachROI`, `VisionConfig`, ~200 lines)
would lift straight out into `perception/vision_config.py`. That file is not on
this branch's stated ownership list, so it was not created — the boundary is worth
more during a parallel build than the refactor is, and the split is mechanical and
behaviour-preserving whenever someone with the wider remit wants it.

Suggested split, if you take it:

| new file | contents |
|---|---|
| `perception/vision_config.py` | `VisionConfigError`, `ApproachROI`, `VisionConfig` |
| `perception/vision_geometry.py` (optional) | `foot_point`, `point_in_polygon`, `assign_to_approaches`, `density_for`, `queue_estimate` |
| `perception/vision_detector.py` | label mapping, `ApproachAggregate`, `FrameObservation`, `VisionDetector`, CLI |

`perception/incident_detector.py` at 599 lines (279 executable) is inside the
ceiling and needs nothing.

---

## 7. Docs that should learn these modules exist

Neither `CLAUDE.md` nor `docs/PsychoFlow_Master_Plan.md` mentions `iot/`, MQTT,
`vision_detector` or `incident_detector` — grepped, zero matches. Given how much
this project leans on `CLAUDE.md` as the module-boundary source of truth, someone
with edit rights should fold in a §7.2b (real detector, as an addition to §7.2's
mock) and a §7.7 (MQTT transport) once the hackathon branches reconcile.

The commands worth adding to `CLAUDE.md` §8:

```
python -m tests.test_iot                      # 32 assertions, no SUMO, no camera
python -m tests.test_vision_detector          # 28 assertions, generates its own clip
python -m perception.incident_detector        # 34 assertions, no SUMO
python -m iot.broker                          # local broker, loopback:1883
python -m iot.publisher --with-broker --steps 5
python -m iot.subscriber --seconds 10
python -m perception.vision_detector --make-sample --frames 100   # self-contained
```

---

## 8. Known gaps this branch is NOT claiming to have closed

- **No real camera footage exists.** `sim/media/` is empty but for its README; the
  ROI polygons shipped in `VisionConfig.default()` are **placeholders**, marked
  `is_placeholder=True` in the data itself and warned about on the CLI. They were
  never measured against a real camera. Nobody should read a per-approach number
  off placeholder geometry as real.
- **The done-bar clip is synthetic.** `make_sample_video()` composites a real
  photographed bus (shipped inside the `ultralytics` wheel, no download) so the
  pipeline is exercised with genuine non-zero detections — an earlier version drew
  rectangles, ran 100 clean frames and detected **zero vehicles in every one**,
  which is this repo's named "passes while proving nothing" failure mode. It is
  still one photo on a flat background, not a footage substitute.
- **`emergency_vehicle_flag` is a behavioural heuristic, not a classification.**
  COCO has no ambulance class. It flags a vehicle sustaining several times its
  neighbours' median speed in congested traffic. It is wrong on a motorcycle
  filtering hard and says nothing in free-flowing traffic. It rides with
  `emergency_flag_is_experimental: True` and is forbidden from incrementing
  `type_composition["ambulance"]`. **It must never reach §10's emergency override.**
- **The incident thresholds are reasoned defaults, not measured.**
  `STATIONARY_MIN_S=15`, `ACCIDENT_CLUSTER_RADIUS_PX=90`,
  `SPEED_COLLAPSE_RATIO=0.25`, `CONGESTION_QUEUE_MIN_M=60` are engineering starting
  points chosen to satisfy the hand-scored scenarios, in the sense
  `docs/MIXED_TRAFFIC_RESEARCH.md` §2 uses. `ACCIDENT_CLUSTER_RADIUS_PX` in
  particular is in **pixels** and so is frame-scale dependent — it will need
  retuning against whatever real footage arrives.

---

## 9. Reconciliation against `hackathon/agents-backend`'s §A1/§A2/§A3

Added 2026-09-04. That branch recorded three **assumed** Track A shapes while
Track A did not yet exist in its tree, and asked to "either match them or tell
us to change." This section is that answer. Nothing below changes any code on
this branch — all three are the consuming side's to apply.

**Both branches carry a file named `NOTES-FOR-INTEGRATION.md` and they are
different documents.** They will conflict on merge; the resolution is to keep
both, not to pick one.

### 9.1 §A2 — the factory name does not match. This one breaks at import.

| | assumed by agents-backend | **shipped here** |
|---|---|---|
| name | `make_vision_source` | **`get_vision_source`** |
| signature | `(kind: str, *, seed: int \| None)` | **`(mode: str = "mock", **kwargs)`** |

`get_vision_source` is the name Track A was specified to build, so the code is
right and §A2's assumption is what needs correcting. `detector` additionally
**requires** `source=<video path or camera index>` and raises `ValueError`
without it — there is no default camera.

Compatible as-is: §A2's duck-type is `observe_all(readings) -> dict[str, dict]`,
and both `VisionMock` and `VisionDetector` expose `observe()` **and**
`observe_all()` with that shape.

§A2's "**on `mock` perform no swap at all**" rule still holds and should be kept
— `get_vision_source("mock")` constructs a *fresh* `VisionMock`, so assigning it
onto the twin would reseed it and perturb recorded numbers, which is precisely
what that rule exists to prevent. Call the factory only for `detector`.

### 9.2 §A1 — the detector's emergency key is named differently, and routing it is a decision, not a rename.

The detector emits **`emergency_vehicle_flag`**, never `emergency`. Passed
through unmapped, §A1 falls back to `type_composition["ambulance"] > 0`, and the
detector can never set that (COCO has no ambulance class, see §8) — so a
detector-sourced emergency silently reads as **always false**. That fails
closed, which is the safe direction, but it is silent.

**Do not fix it with a rename.** The flag is a behavioural heuristic that rides
with `emergency_flag_is_experimental: True`, and §8 forbids it reaching §10's
emergency override. Mapping it onto §A1's `emergency` routes an experimental
signal into the priority agent's emergency class.

**DECISION (2026-09-04, user's call): advisory-only.** The adapter in
`backend/sim_runner.py` does NOT map `emergency_vehicle_flag` onto §A1's
`emergency`. A detector-sourced reading keeps the fail-closed
`type_composition["ambulance"] > 0` path (always false for `detector`, which is
correct). `emergency_vehicle_flag` is instead forwarded as a low-confidence
advisory field to the IncidentPriority agent and surfaced on the frame for the
operator — it never enters `safety.validator`'s `forced_emergency_lanes`.
Rationale: no COCO ambulance class (heuristic only), §10's override is stateless
and recomputes every step (a flickering false positive thrashes a junction),
and the project floor is structural not statistical. Actuation stays with a real
detected-ambulance signal (V2X / incident intake) or the operator's
`trigger_emergency`.

Otherwise §A1 matches: `lane_id`, `vehicle_count`, `type_composition`,
`confidence` and `source` are all emitted in the §7.2 envelope.
`perception/incident_detector.py` returns a **different** shape (§A1's own table
of `{type, junction, approach, lane_index, distance_m, distance_confidence,
severity, detected_at, source}`) — §A1 already states the adapter is
`backend/sim_runner.py`'s job, which is the correct split.

### 9.3 §A3 — topics match exactly; `fresh_s` is the consumer's to compute.

All four topic strings match `iot/topics.py` character for character.

`fresh_s` is **not a field this branch produces** and does not appear anywhere
in `iot/`. It is derivable: `SensorCountsPayload` carries **`sim_time`**, so the
frame's `{"source": ..., "fresh_s": ...}` is `now_sim_time - payload.sim_time`,
computed at the point the frame is built. `source` is on the payload already
(defaults to `"iot_sensor"`).

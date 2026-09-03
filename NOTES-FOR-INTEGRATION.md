# NOTES FOR INTEGRATION — `hackathon/agents-backend`

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



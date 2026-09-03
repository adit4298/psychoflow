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

## 2. Orchestrator blackboard — `agent_activity` entry shape (Part 4b)

*(appended when Part 4b lands)*

## 3. §13.2 additive frame keys (Part 4c)

*(appended when Part 4c lands)*

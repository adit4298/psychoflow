"""Unit tests for `agents/incident_priority.py` (Part 4a).

Standalone and runnable — this repo has no pytest in its venv, and every
existing module test is a plain `python -m <module>` self-test with asserts
(`safety.validator`, `prediction.spillover`, `prediction.incident_impact`).
This file follows that convention:

    venv/Scripts/python.exe -m tests.test_incident_priority

DIVISION OF LABOUR with the module's own self-test:
  * `agents.incident_priority.test_incident_priority_scenarios()` owns the
    three DONE-BAR scenarios (§4a) and is the command the done-bar names.
    This file CALLS it rather than restating it — one implementation of
    those assertions, not two that can drift.
  * This file owns the UNIT-level tests underneath them: the arbitration
    key in isolation, the weight/duration bound invariants, boundary
    validation of malformed inputs, and the no-SUMO import guarantee.

Nothing here starts SUMO, loads a checkpoint, or constructs a ControlState
(`apply()` is exercised with an injected fake dispatch), so it is safe to
run at any time — no `sim.sumo_activity` beacon check is needed or wanted.
"""

from __future__ import annotations

import sys

from agents.incident_priority import (
    BIAS_MAX_WEIGHT,
    BIAS_MIN_WEIGHT,
    BIAS_NEUTRAL_WEIGHT,
    CEILING_WAIT_S,
    CLASS_RANK,
    CORRIDOR_ORDER,
    DURATION_MAX_S,
    DURATION_MIN_S,
    EVENT_ACCIDENT,
    EVENT_CLASSES,
    EVENT_EMERGENCY,
    EVENT_FAIRNESS,
    EVENT_MAJOR_CONGESTION,
    SOURCE_SENSOR,
    Directive,
    Event,
    IncidentPriorityAgent,
    IncidentPriorityError,
    MAX_DROPPED_REPORTED,
    apply,
    arbitrate,
    boost_weight,
    suppress_weight,
)
from agents.incident_priority_scenarios import (
    test_incident_priority_scenarios,
    _lane,
    _synthetic_snapshot,
)
from perception.incident_intake import SEVERITY_VALUE

_passed = 0
_failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f"  — {detail}" if detail else ""))
    if ok:
        _passed += 1
    else:
        _failed += 1


def _event(event_class, junction_id, lane_id, severity, urgency) -> Event:
    return Event(
        event_id=f"{event_class}:{lane_id}",
        event_class=event_class,
        junction_id=junction_id,
        lane_id=lane_id,
        severity=severity,
        severity_value=SEVERITY_VALUE[severity],
        urgency=urgency,
        source=SOURCE_SENSOR,
        detected_at_sim_time=0.0,
        evidence={},
    )


# ---------------------------------------------------------------------------
# 1. The arbitration key, in isolation from any snapshot
# ---------------------------------------------------------------------------
def test_arbitration_key() -> None:
    print("\n1  arbitration key (`_priority_key` via `arbitrate`)")

    # Level 1 — the priority policy itself, constraint 2. Built in REVERSE
    # order and with urgency deliberately ANTI-correlated (fairness most
    # urgent, emergency least), so an implementation that sorted on urgency
    # before class would produce the exact opposite and cannot pass.
    mixed = [
        _event(EVENT_FAIRNESS, "J1", "a", "low", 0.99),
        _event(EVENT_MAJOR_CONGESTION, "J1", "b", "high", 0.80),
        _event(EVENT_ACCIDENT, "J1", "c", "high", 0.50),
        _event(EVENT_EMERGENCY, "J1", "d", "high", 0.01),
    ]
    got = [e.event_class for e in arbitrate(mixed)]
    check("1a class rank dominates every other term",
          got == list(EVENT_CLASSES), f"{got}")

    # Level 2 — severity, within one class. Junction order is set to fight
    # it (the LOW-severity event sits at J1, which wins the junction
    # tiebreak), so passing requires severity to be consulted first.
    sev = [
        _event(EVENT_ACCIDENT, "J1", "a", "low", 0.5),
        _event(EVENT_ACCIDENT, "J3", "b", "high", 0.5),
    ]
    check("1b severity desc outranks the junction tiebreak",
          [e.lane_id for e in arbitrate(sev)] == ["b", "a"])

    # Level 3 — urgency, with class and severity held equal.
    urg = [
        _event(EVENT_FAIRNESS, "J1", "a", "low", 0.10),
        _event(EVENT_FAIRNESS, "J1", "b", "low", 0.90),
    ]
    check("1c urgency desc breaks a severity tie",
          [e.lane_id for e in arbitrate(urg)] == ["b", "a"])

    # Level 4 — corridor index, upstream first. This is the tiebreak
    # `sim_runner._emit_junction()` already uses for §10 overrides.
    jn = [_event(EVENT_FAIRNESS, j, f"l{j}", "low", 0.5)
          for j in reversed(CORRIDOR_ORDER)]
    check("1d corridor index asc (J1 first) breaks an urgency tie",
          [e.junction_id for e in arbitrate(jn)] == list(CORRIDOR_ORDER))

    # Level 5 — lane_id makes the order TOTAL. Without it, sort stability
    # would leak the classifier's iteration order into the result and the
    # done-bar orderings would pass by accident. Feeding the same set in
    # two different input orders must give one identical output.
    same = [_event(EVENT_FAIRNESS, "J2", n, "low", 0.5) for n in "cab"]
    check("1e ordering is TOTAL — input order cannot leak through",
          [e.lane_id for e in arbitrate(same)]
          == [e.lane_id for e in arbitrate(list(reversed(same)))]
          == ["a", "b", "c"])

    check("1f arbitrate returns a new tuple, does not mutate its input",
          isinstance(arbitrate(mixed), tuple)
          and [e.event_class for e in mixed][0] == EVENT_FAIRNESS)


# ---------------------------------------------------------------------------
# 2. Weight / duration bounds — constraint 6, made structural
# ---------------------------------------------------------------------------
def test_bounds() -> None:
    print("\n2  directive bounds (control_api can never reject one on range)")

    check("2a CLASS_RANK covers exactly EVENT_CLASSES",
          set(CLASS_RANK) == set(EVENT_CLASSES)
          and sorted(CLASS_RANK.values()) == list(range(len(EVENT_CLASSES))))

    ok = True
    detail = []
    for severity, value in SEVERITY_VALUE.items():
        b, s = boost_weight(value), suppress_weight(value)
        detail.append(f"{severity}: boost={b} suppress={s}")
        ok &= BIAS_MIN_WEIGHT <= b <= BIAS_MAX_WEIGHT
        ok &= BIAS_MIN_WEIGHT <= s <= BIAS_MAX_WEIGHT
    check("2b boost/suppress in range for every §7.3 severity", ok,
          "; ".join(detail))

    # The endpoints, which is where a naive formula drifts outside by a float
    # ulp: severity_value 1.0 must land exactly ON the range endpoints.
    check("2c severity 1.0 maps to the exact range endpoints",
          boost_weight(1.0) == BIAS_MAX_WEIGHT
          and suppress_weight(1.0) == BIAS_MIN_WEIGHT,
          f"{boost_weight(1.0)} / {suppress_weight(1.0)}")
    check("2d severity 0.0 maps to neutral on both",
          boost_weight(0.0) == suppress_weight(0.0) == BIAS_NEUTRAL_WEIGHT)

    # Monotonic in the right direction — a boost must RISE with severity and
    # a suppression must FALL. Getting the sign backwards would still be in
    # range and would still pass 2b.
    vals = sorted(SEVERITY_VALUE.values())
    check("2e boost rises and suppress falls with severity",
          [boost_weight(v) for v in vals] == sorted(boost_weight(v) for v in vals)
          and [suppress_weight(v) for v in vals]
              == sorted((suppress_weight(v) for v in vals), reverse=True))

    check("2f suppression floors at BIAS_MIN_WEIGHT, never 0 — the lane "
          "keeps minimum service and §10 still protects it",
          suppress_weight(1.0) == BIAS_MIN_WEIGHT > 0.0)


# ---------------------------------------------------------------------------
# 3. Boundary validation — malformed input degrades, never raises
# ---------------------------------------------------------------------------
def test_boundary_validation() -> None:
    print("\n3  boundary validation (bad input is dropped, not raised on)")

    agent = IncidentPriorityAgent()
    snap = _synthetic_snapshot(0.0, {
        "J1": [_lane("N1_J1_0", "north")],
        "J2": [_lane("W1_J2_0", "west")],
        "J3": [_lane("W1_J3_0", "west")],
    })

    bad_vision = [
        {"confidence": 0.99, "emergency": True},              # no lane_id
        {"lane_id": "NOPE_0", "confidence": 0.99, "emergency": True},  # unknown
        {"lane_id": "N1_J1_0", "emergency": True},            # no confidence
        {"lane_id": "N1_J1_0", "confidence": 0.10, "emergency": True},  # low
        "not-a-mapping",
    ]
    res = agent.tick(snap, vision_events=bad_vision)
    check("3a every malformed vision record is dropped with a reason",
          len(res.dropped_inputs) == len(bad_vision) and res.events == (),
          f"{len(res.dropped_inputs)} dropped")

    bad_forecast = [
        {"to_junction": "J2", "predicted_queue_delta": float("nan"),
         "confidence": 0.85},
        {"to_junction": "J9", "predicted_queue_delta": 5.0, "confidence": 0.85},
        {"predicted_queue_delta": 5.0, "confidence": 0.85},
    ]
    res = agent.tick(_synthetic_snapshot(5.0, {
        "J1": [_lane("N1_J1_0", "north")],
        "J2": [_lane("W1_J2_0", "west")],
        "J3": [_lane("W1_J3_0", "west")],
    }), spillover=bad_forecast)
    check("3b malformed forecasts are dropped, no congestion event",
          len(res.dropped_inputs) == len(bad_forecast)
          and not any(e.event_class == EVENT_MAJOR_CONGESTION
                      for e in res.events))

    # A cold-start forecast (confidence == 0.5) is exactly the case
    # CONGESTION_MIN_CONFIDENCE's strict `>` exists to reject.
    res = agent.tick(_synthetic_snapshot(10.0, {
        "J1": [_lane("N1_J1_0", "north")],
        "J2": [_lane("W1_J2_0", "west")],
        "J3": [_lane("W1_J3_0", "west")],
    }), spillover=[{"from_junction": "J1", "to_junction": "J2",
                    "horizon_s": 60.0, "predicted_queue_delta": 9.0,
                    "confidence": 0.5}])
    check("3c a COLD-START forecast (confidence == 0.5) is ignored",
          not any(e.event_class == EVENT_MAJOR_CONGESTION for e in res.events))

    # ... but the incident-penalised 0.65 case IS acted on, which is the
    # whole reason the bar is 0.5 and not 0.85.
    res = agent.tick(_synthetic_snapshot(15.0, {
        "J1": [_lane("N1_J1_0", "north")],
        "J2": [_lane("W1_J2_0", "west")],
        "J3": [_lane("W1_J3_0", "west")],
    }), spillover=[{"from_junction": "J1", "to_junction": "J2",
                    "horizon_s": 60.0, "predicted_queue_delta": 9.0,
                    "confidence": 0.65}])
    check("3d an incident-penalised forecast (0.65) DOES fire congestion",
          any(e.event_class == EVENT_MAJOR_CONGESTION for e in res.events))

    check("3e vision_events=None behaves exactly as if Track A is absent",
          agent.tick(snap, sim_time=20.0).dropped_inputs == ())


# ---------------------------------------------------------------------------
# 4. Episode-boundary guard
# ---------------------------------------------------------------------------
def test_episode_boundary() -> None:
    print("\n4  episode boundary (backwards sim_time RAISES, per DecisionLog)")

    agent = IncidentPriorityAgent()
    lanes = {"J1": [_lane("N1_J1_0", "north")],
             "J2": [_lane("W1_J2_0", "west")],
             "J3": [_lane("W1_J3_0", "west")]}
    agent.tick(_synthetic_snapshot(100.0, lanes))

    raised = False
    try:
        agent.tick(_synthetic_snapshot(1.0, lanes))
    except IncidentPriorityError:
        raised = True
    check("4a a backwards sim_time raises rather than silently mis-expiring "
          "every response for a whole episode", raised)

    check("4b equal sim_time is legal (one tick, several junctions)",
          agent.tick(_synthetic_snapshot(100.0, lanes)) is not None)

    agent.reset()
    check("4c reset() rebinds the registry and clears the clock",
          agent.active_responses == ()
          and agent.tick(_synthetic_snapshot(1.0, lanes)) is not None)


# ---------------------------------------------------------------------------
# 5. `apply()` carries no policy and never needs a real ControlState
# ---------------------------------------------------------------------------
def test_apply_is_policy_free() -> None:
    print("\n5  apply() — dispatch order, injectable, zero policy")

    seen = []

    def fake_dispatch(state, function, args):
        seen.append((function, dict(args)))
        return {"applied": True, "echo": function}

    directives = (
        Directive("trigger_emergency", {"lane_id": "L0"}, "e:1",
                  EVENT_EMERGENCY, CLASS_RANK[EVENT_EMERGENCY], "r"),
        Directive("set_lane_bias", {"lane_id": "L1", "weight": 3.97,
                                    "duration_s": 30.0}, "f:1",
                  EVENT_FAIRNESS, CLASS_RANK[EVENT_FAIRNESS], "r"),
    )
    results = apply(None, directives, dispatch=fake_dispatch)
    check("5a dispatches IN ORDER, one result per directive",
          [f for f, _ in seen] == ["trigger_emergency", "set_lane_bias"]
          and len(results) == 2 and all(r["applied"] for r in results))

    check("5b every emitted function is on the control_api allowlist",
          all(d.function in __import__(
              "backend.control_api", fromlist=["CONTROL_FUNCTIONS"]
          ).CONTROL_FUNCTIONS for d in directives))


# ---------------------------------------------------------------------------
# 6. Constraint 3 — importable with no SUMO / torch / numpy
# ---------------------------------------------------------------------------
def test_no_heavy_imports() -> None:
    print("\n6  constraint 3 — no SUMO / torch / numpy pulled in")

    import agents.incident_priority as mod  # noqa: F401

    heavy = {"traci", "sumolib", "torch", "numpy", "stable_baselines3",
             "gymnasium"}
    # Anything already imported by THIS test process does not prove the
    # module pulled it in, so re-check against a module-local view: what
    # matters is that incident_priority's own import graph is clean.
    pulled = heavy & set(sys.modules)
    check("6a no heavy module is in sys.modules after importing it",
          not pulled, f"pulled={sorted(pulled) or 'none'}")


# ---------------------------------------------------------------------------
# 7. Hardening (security pass, 4a)
# ---------------------------------------------------------------------------
def test_hardening() -> None:
    print("\n7  hardening — bounded diagnostics, no silent truncation")

    agent = IncidentPriorityAgent()
    lanes = {"J1": [_lane("N1_J1_0", "north")],
             "J2": [_lane("W1_J2_0", "west")],
             "J3": [_lane("W1_J3_0", "west")]}

    # dropped_inputs rides the §13.2 frame from Part 4c, so an upstream
    # producer must not be able to inflate a frame without bound.
    flood = [{"lane_id": "NOPE", "confidence": 0.99, "emergency": True}] * 500
    res = agent.tick(_synthetic_snapshot(0.0, lanes), vision_events=flood)
    check("7a a 500-record malformed feed is capped, with the overflow "
          "reported as a count",
          len(res.dropped_inputs) == MAX_DROPPED_REPORTED + 1
          and "further dropped input" in res.dropped_inputs[-1],
          f"{len(res.dropped_inputs)} entries; last={res.dropped_inputs[-1]!r}")

    # confirm() must not silently truncate — the tail would sit PENDING
    # forever: never active, never retried.
    raised = False
    try:
        agent.confirm(
            (Directive("set_lane_bias", {"lane_id": "W1_J2_0", "weight": 2.0,
                                         "duration_s": 30.0}, "x:1",
                       EVENT_FAIRNESS, CLASS_RANK[EVENT_FAIRNESS], "r"),),
            (),   # caller lost the result
            0.0,
        )
    except IncidentPriorityError:
        raised = True
    check("7b confirm() refuses a directives/results length mismatch", raised)

    check("7c a non-numeric wait never escapes as an exception",
          agent.tick(_synthetic_snapshot(1.0, {
              "J1": [{**_lane("N1_J1_0", "north"),
                      "wait_time_max_single_vehicle": "not-a-number"}],
          })) is not None)


def main() -> int:
    print("=" * 72)
    print("  UNIT TESTS — agents/incident_priority.py")
    print("=" * 72)
    test_arbitration_key()
    test_bounds()
    test_boundary_validation()
    test_episode_boundary()
    test_apply_is_policy_free()
    test_no_heavy_imports()
    test_hardening()

    print("\n" + "=" * 72)
    print("  DONE-BAR SCENARIOS (delegated to the module's own self-test)")
    print("=" * 72)
    test_incident_priority_scenarios()

    print("\n" + "=" * 72)
    print(f"  unit tests: {_passed} passed, {_failed} failed")
    print("=" * 72)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())

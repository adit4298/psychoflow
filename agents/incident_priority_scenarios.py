"""Hand-scored done-bar scenarios for the Incident & Priority agent (Part 4a).

Split out of `agents/incident_priority.py` so the agent module stays inside
the 800-line ceiling. This carries the §7.6-shaped synthetic fixtures (no
SUMO, no net file, no TraCI — modelled on `prediction/spillover.py`'s
`_synthetic_snapshot`) and the three scenarios the Part 4a done-bar names:

  1. an emergency PREEMPTS an active congestion response
  2. two simultaneous incidents order correctly
  3. a fairness directive yields to all three

Run either of these — they are the same assertions:

    python -m agents.incident_priority              (the done-bar command)
    python -m agents.incident_priority_scenarios

Nothing here starts SUMO, so no `sim.sumo_activity` beacon check is needed.
The one exception is `_check_constant_drift`, which imports the SUMO-backed
home modules of the four duplicated literals WHEN they are importable and
skips silently when they are not.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from agents.incident_priority import (
    BIAS_MIN_WEIGHT, BIAS_MAX_WEIGHT, BIAS_NEUTRAL_WEIGHT,
    CEILING_WAIT_S, CONTROL_FUNCTIONS, DURATION_MAX_S, DURATION_MIN_S,
    EVENT_ACCIDENT, EVENT_CLASSES, EVENT_EMERGENCY, EVENT_FAIRNESS,
    EVENT_MAJOR_CONGESTION,
    FAIRNESS_WAIT_S, MIN_GREEN_S, NEUTRALISE_DURATION_S, SEVERITY_VALUE,
    STATUS_ACTIVE, STATUS_PREEMPTED, IncidentPriorityAgent, boost_weight,
)


# ---------------------------------------------------------------------------
def _lane(lane_id: str, approach: str, *, vehicle_count: int = 0,
          halted_count: int = 0, wait_current: float = 0.0,
          wait_max: float = 0.0, starved: bool = False,
          ambulance: int = 0) -> dict:
    """One §7.1 lane reading."""
    return {
        "lane_id": lane_id, "approach": approach,
        "vehicle_count": vehicle_count, "halted_count": halted_count,
        "type_composition": {"bike": 0, "auto": 0, "car": vehicle_count,
                             "truck": 0, "ambulance": ambulance},
        "wait_time_current": wait_current,
        "wait_time_max_single_vehicle": wait_max,
        "starvation_flag": starved,
    }


def _synthetic_snapshot(sim_time: float, junction_lanes: Mapping[str, Sequence[dict]],
                        incidents: Sequence[dict] = ()) -> dict:
    """A §7.6 snapshot with no TraCI, no sumolib and no net file."""
    return {
        "sim_time": sim_time,
        "corridor_adjacency": [["J1", "J2"], ["J2", "J3"]],
        "junctions": {
            jid: {"lanes": {l["lane_id"]: l for l in lanes},
                  "current_phase": 0, "lane_count": len(lanes)}
            for jid, lanes in junction_lanes.items()
        },
        "active_incidents": [dict(i) for i in incidents],
        "weather": {"state": "clear", "changed_at_sim_time": 0.0},
        "v2x_messages_recent": [],
    }


def _incident(incident_id: str, junction_id: str, lane_id: str, severity: str,
              *, affected: Sequence[str] = (), duration_s: float = 600.0,
              incident_type: str = "accident") -> dict:
    return {"incident_id": incident_id, "type": incident_type,
            "location": {"junction_id": junction_id, "lane_id": lane_id},
            "severity": severity,
            "affected_lanes": list(affected or [lane_id]),
            "reported_at_sim_time": 0.0, "estimated_duration_s": duration_s}


# ---------------------------------------------------------------------------
# DONE-BAR self-test (§4a) — python -m agents.incident_priority
# ---------------------------------------------------------------------------
def _clean(jid: str, n: int = 2) -> list[dict]:
    """A junction with nothing wrong with it."""
    return [_lane(f"W{i}_{jid}_0", "west", vehicle_count=2, halted_count=1,
                  wait_max=10.0) for i in range(n)]


def _scenario_1_emergency_preempts_congestion() -> None:
    print("\n--- 1. emergency PREEMPTS an active congestion response --------")
    agent = IncidentPriorityAgent()

    def snap(t: float, ambulance: int = 0) -> dict:
        return _synthetic_snapshot(t, {
            "J1": _clean("J1"),
            "J2": [
                _lane("W1_J2_0", "west", vehicle_count=12, halted_count=8,
                      wait_current=180.0, wait_max=95.0, starved=True),
                _lane("N1_J2_0", "north", vehicle_count=7, halted_count=5,
                      wait_current=140.0, wait_max=100.0, starved=True),
                _lane("E1_J2_0", "east", vehicle_count=1, halted_count=0,
                      wait_max=4.0, ambulance=ambulance),
            ],
            "J3": _clean("J3"),
        })

    # t=0 — two starved lanes at J2 => major_congestion, severity "medium"
    # (>=2 starved, but no lane has crossed the 120s ceiling).
    r0 = agent.tick(snap(0.0))
    assert len(r0.events) == 1, r0.events
    assert r0.events[0].event_class == EVENT_MAJOR_CONGESTION
    assert r0.events[0].severity == "medium", r0.events[0].severity
    assert len(r0.directives) == 1, r0.directives
    d0 = r0.directives[0]
    assert d0.function == "set_lane_bias", d0.function
    # Targets the junction's BUSIEST lane (halted 8 > 5), not merely the
    # longest-waiting one.
    assert d0.args["lane_id"] == "W1_J2_0", d0.args
    assert d0.args["weight"] == boost_weight(SEVERITY_VALUE["medium"]), d0.args
    print(f"  t=0.0   congestion -> {d0.function}"
          f"(lane={d0.args['lane_id']}, weight={d0.args['weight']}, "
          f"duration_s={d0.args['duration_s']})")
    agent.confirm(r0.directives, [{"applied": True}], 0.0)
    assert agent.active_responses[0].status == STATUS_ACTIVE

    # t=5 — IDENTICAL snapshot. A live response is not re-issued every step.
    r1 = agent.tick(snap(5.0))
    assert r1.directives == (), r1.directives
    assert r0.events[0].event_id in r1.suppressed, r1.suppressed
    print(f"  t=5.0   identical state -> 0 directives, "
          f"suppressed={list(r1.suppressed)}")

    # t=10 — an ambulance appears at J2.
    r2 = agent.tick(snap(10.0, ambulance=1))
    assert r2.events[0].event_class == EVENT_EMERGENCY, r2.events
    assert r2.directives[0].function == "trigger_emergency", r2.directives
    assert r2.directives[0].args["lane_id"] == "E1_J2_0"
    congestion_id = r0.events[0].event_id
    assert congestion_id in r2.preempted, r2.preempted
    neutralisers = [d for d in r2.directives
                    if d.function == "set_lane_bias"
                    and d.args["weight"] == BIAS_NEUTRAL_WEIGHT]
    assert len(neutralisers) == 1, r2.directives
    assert neutralisers[0].args["lane_id"] == "W1_J2_0"
    assert neutralisers[0].args["duration_s"] == NEUTRALISE_DURATION_S
    entry = {r.event_id: r for r in agent.active_responses}[congestion_id]
    assert entry.status == STATUS_PREEMPTED, entry
    assert entry.preempted_by == r2.events[0].event_id, entry
    print(f"  t=10.0  ambulance -> directives[0]={r2.directives[0].function}"
          f"({r2.directives[0].args['lane_id']}), "
          f"neutralised {neutralisers[0].args['lane_id']} @ "
          f"weight={neutralisers[0].args['weight']}")
    print(f"          registry: {congestion_id} status={entry.status} "
          f"preempted_by={entry.preempted_by}")
    print("  OK — preemption asserted at the MECHANISM level (emission order,"
          "\n       neutralising re-issue, and the registry mark).")
    return [r0, r1, r2]


def _scenario_2_two_incidents_order() -> None:
    print("\n--- 2. two simultaneous incidents order correctly --------------")

    # (a) severity separates them, and the CORRIDOR INDEX is set to fight it:
    # the lower-severity incident sits at J1 (which wins the junction
    # tiebreak) and on the longer-waiting lanes (which wins the urgency
    # tiebreak). Only consulting severity FIRST can produce the right answer.
    lanes = {
        "J1": [_lane("N1_J1_0", "north", vehicle_count=6, halted_count=4,
                     wait_max=80.0)],
        "J2": _clean("J2"),
        "J3": [_lane("W1_J3_0", "west", vehicle_count=3, halted_count=2,
                     wait_max=20.0)],
    }
    snapshot = _synthetic_snapshot(0.0, lanes, incidents=[
        _incident("inc_0001", "J1", "N1_J1_0", "medium"),
        _incident("inc_0002", "J3", "W1_J3_0", "high"),
    ])
    result = IncidentPriorityAgent().tick(snapshot)
    classes = [e.event_class for e in result.events]
    assert classes == [EVENT_ACCIDENT, EVENT_ACCIDENT], classes
    assert result.events[0].event_id == "accident:inc_0002", result.events
    assert result.events[1].event_id == "accident:inc_0001", result.events
    assert result.directives[0].event_id == result.events[0].event_id
    print(f"  (a) J1/medium (wait 80s) vs J3/high (wait 20s) -> "
          f"{[e.event_id for e in result.events]}")
    print(f"      severity wins over BOTH the urgency and junction tiebreaks")

    # (b) severity AND urgency identical -> the corridor index decides,
    # upstream first. This pins the level (a) structurally cannot reach.
    lanes_b = {
        "J1": [_lane("N1_J1_0", "north", halted_count=2, wait_max=50.0)],
        "J2": _clean("J2"),
        "J3": [_lane("W1_J3_0", "west", halted_count=2, wait_max=50.0)],
    }
    snapshot_b = _synthetic_snapshot(0.0, lanes_b, incidents=[
        _incident("inc_0003", "J3", "W1_J3_0", "high"),
        _incident("inc_0004", "J1", "N1_J1_0", "high"),
    ])
    result_b = IncidentPriorityAgent().tick(snapshot_b)
    assert result_b.events[0].junction_id == "J1", result_b.events
    assert result_b.events[0].urgency == result_b.events[1].urgency
    print(f"  (b) both high, identical urgency -> "
          f"{[e.junction_id for e in result_b.events]} (upstream first)")
    print("  OK")
    return [result, result_b]


def _scenario_3_fairness_yields() -> None:
    print("\n--- 3. a fairness directive yields to all three ----------------")

    # All four classes at once. J1 carries the accident AND the fairness
    # case on DIFFERENT lanes; J2 the congestion; J3 the emergency.
    def lanes(fairness_at: str) -> dict:
        j1 = [_lane("N1_J1_0", "north", vehicle_count=9, halted_count=6,
                    wait_max=30.0)]
        j3 = [_lane("W1_J3_0", "west", vehicle_count=2, halted_count=1,
                    wait_max=8.0, ambulance=1)]
        starved = _lane(f"S1_{fairness_at}_0", "south", vehicle_count=4,
                        halted_count=3, wait_current=95.0, wait_max=95.0,
                        starved=True)
        (j1 if fairness_at == "J1" else j3).append(starved)
        return {
            "J1": j1,
            "J2": [
                _lane("W1_J2_0", "west", vehicle_count=11, halted_count=7,
                      wait_max=95.0, starved=True),
                _lane("N1_J2_0", "north", vehicle_count=8, halted_count=5,
                      wait_max=100.0, starved=True),
            ],
            "J3": j3,
        }

    incidents = [_incident("inc_0007", "J1", "N1_J1_0", "high",
                           affected=["N1_J1_0"])]
    result = IncidentPriorityAgent().tick(
        _synthetic_snapshot(0.0, lanes("J1"), incidents=incidents))
    classes = [e.event_class for e in result.events]
    assert classes == list(EVENT_CLASSES), classes
    emitted = [d.event_class for d in result.directives]
    assert emitted == list(EVENT_CLASSES), emitted
    print(f"  events    : {classes}")
    print(f"  directives: {emitted}")
    print(f"              (fairness emitted LAST, behind all three)")

    # Now put the fairness lane at the EMERGENCY's junction. An emergency
    # claims its whole junction (§10 greens that lane and reds every
    # conflicting movement), so the fairness directive is dropped ENTIRELY
    # rather than merely ordered last. That is what "yields" means
    # operationally.
    shadowed = IncidentPriorityAgent().tick(
        _synthetic_snapshot(0.0, lanes("J3"), incidents=incidents))
    fairness = [e for e in shadowed.events if e.event_class == EVENT_FAIRNESS]
    assert len(fairness) == 1, shadowed.events
    assert fairness[0].event_id in shadowed.suppressed, shadowed.suppressed
    assert not any(d.event_class == EVENT_FAIRNESS for d in shadowed.directives)
    print(f"  fairness moved onto the emergency's junction -> DROPPED "
          f"({fairness[0].event_id} in suppressed)")
    print("  OK")
    return [result, shadowed]


def _check_structural_invariants(results) -> None:
    print("\n--- structural invariants over every directive above -----------")
    directives = [d for r in results for d in r.directives]
    assert directives, "no directives were produced — invariants are vacuous"
    assert all(d.function in CONTROL_FUNCTIONS for d in directives)
    biases = [d for d in directives if d.function == "set_lane_bias"]
    assert all(BIAS_MIN_WEIGHT <= d.args["weight"] <= BIAS_MAX_WEIGHT
               for d in biases)
    assert all(DURATION_MIN_S <= d.args["duration_s"] <= DURATION_MAX_S
               for d in biases)
    print(f"  {len(directives)} directives, {len(biases)} of them set_lane_bias")
    print(f"  every function on control_api.CONTROL_FUNCTIONS            OK")
    print(f"  every weight in [{BIAS_MIN_WEIGHT}, {BIAS_MAX_WEIGHT}]"
          f"                              OK")
    print(f"  every duration_s in [{DURATION_MIN_S}, {DURATION_MAX_S}]"
          f"                    OK")
    print("  => control_api can never reject one of ours on range.")


def _check_constant_drift() -> None:
    """Compare the duplicated literals against their home modules — but only
    when SUMO is importable, so an offline run is never blocked."""
    print("\n--- duplicated-constant drift guard ----------------------------")
    try:
        from env.psychoflow_env import MIN_GREEN_S as ENV_MIN_GREEN
        from perception.lane_sensor import DEFAULT_STARVATION_THRESHOLD_S
        from safety.validator import STARVATION_CEILING_S
    except Exception as exc:                      # pragma: no cover
        print(f"  SKIPPED — home modules need SUMO ({type(exc).__name__})")
        return
    assert FAIRNESS_WAIT_S == DEFAULT_STARVATION_THRESHOLD_S
    assert CEILING_WAIT_S == STARVATION_CEILING_S
    assert MIN_GREEN_S == ENV_MIN_GREEN
    assert CEILING_WAIT_S > FAIRNESS_WAIT_S, "the soft band must not close"
    print(f"  FAIRNESS_WAIT_S={FAIRNESS_WAIT_S} CEILING_WAIT_S={CEILING_WAIT_S} "
          f"MIN_GREEN_S={MIN_GREEN_S}  — all match their home modules")


def test_incident_priority_scenarios() -> None:
    """Hand-scored §4a done-bar scenarios."""
    print("=" * 72)
    print("  INCIDENT & PRIORITY AGENT — hand-scored scenarios")
    print(f"  policy: {' > '.join(EVENT_CLASSES)}")
    print("=" * 72)
    results = []
    results += _scenario_1_emergency_preempts_congestion()
    results += _scenario_2_two_incidents_order()
    results += _scenario_3_fairness_yields()
    _check_structural_invariants(results)
    _check_constant_drift()
    print("\n" + "=" * 72)
    print("  ALL SCENARIOS PASS")
    print("=" * 72)

if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    test_incident_priority_scenarios()

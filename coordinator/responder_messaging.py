"""Responder coordination messaging (§11.2).

Turns a closed `EmergencyClearanceEvent` (§11.1) into the decision-support
message a dispatch coordinator would want — §11.2's JSON shape. Not wired
into any real emergency-services system (§17); it is an output object.

§11.2's literal schema:

    { "event", "lane_id", "sim_time", "clearance_time_s",
      "baseline_clearance_time_s", "improvement_pct", "summary" }

Two additions in this build (design plan, Phase 8), both for honesty:

  * "junction_id"        — §11.2 predates §0.1's 3-junction corridor; the
                           per-junction attribution (CLAUDE.md §8 PHASE 8
                           WARNING) needs it stated.
  * "baseline_is_estimate": true — see below.

`clearance_time_s` is REAL: detection -> green onset at the junction the
override fired at, measured by §11.1 (`sim/run_tier0_episode.py` B2's
method, generalised per junction). The known-broken Stage 4 sweep latency
is NOT inherited.

`baseline_clearance_time_s` is a STATED MODEL ESTIMATE, not a measured
counterfactual — a true per-event A/B would need to re-run the episode
with the override disabled, which is impossible live and is what
`run_tier0_episode.py --b3` does offline instead. The estimate is the
time until the ambulance's phase would next come up under normal signal
rotation: the min-green still owed on the current phase, plus every other
green phase held for its min-green with a yellow between. It is labelled
as an estimate in the payload and in the summary text.
"""

from __future__ import annotations

from coordinator.emergency_clearance import EmergencyClearanceEvent

# Rotation-model constants. MIN_GREEN_S / DECISION_INTERVAL_S mirror
# env.psychoflow_env; a fixed yellow estimate is used because the exact
# per-phase yellow is a TLS-program detail this decision-support estimate
# does not need to be precise about.
_MIN_GREEN_S = 10.0
_YELLOW_S_EST = 4.0


def estimate_baseline_clearance_s(
    time_since_switch_s: float,
    n_green_phases: int,
    *,
    min_green_s: float = _MIN_GREEN_S,
    yellow_s_est: float = _YELLOW_S_EST,
) -> float:
    """Model estimate: time to next natural service of the target phase.

    Worst case within the model — the target phase is last in rotation:

        remaining min-green on the current phase
      + (n_green_phases - 1) other phases, each (min_green + one yellow)
      + one yellow to leave the current phase
    """
    remaining_current = max(0.0, min_green_s - float(time_since_switch_s))
    other_phases = max(0, int(n_green_phases) - 1)
    return (
        remaining_current
        + other_phases * (min_green_s + yellow_s_est)
        + yellow_s_est
    )


def build_responder_message(
    event: EmergencyClearanceEvent, baseline_clearance_time_s: float
) -> dict:
    """§11.2 message for one closed clearance event."""
    if event.clearance_time_s is None:
        raise ValueError(
            f"event for {event.junction_id} has no green onset — it never "
            f"resolved, so there is no clearance time to report"
        )

    clearance = event.clearance_time_s
    baseline = float(baseline_clearance_time_s)
    improvement = 100.0 * (1.0 - clearance / baseline) if baseline > 0 else 0.0

    if event.served_on_arrival:
        summary = (
            f"{event.junction_id}: lane {event.lane_id} ({event.direction}) was "
            f"already clear for the emergency vehicle on arrival — no signal "
            f"delay (vs. ~{baseline:.1f}s estimated under normal rotation)."
        )
    else:
        summary = (
            f"{event.junction_id}: lane {event.lane_id} ({event.direction}) "
            f"cleared for emergency vehicle in {clearance:.1f}s "
            f"(vs. ~{baseline:.1f}s estimated without override) — normal "
            f"operation resumed immediately after."
        )

    return {
        "event": "emergency_clearance",
        "junction_id": event.junction_id,
        "lane_id": event.lane_id,
        "sim_time": round(event.first_detection_sim_time, 1),
        "clearance_time_s": round(clearance, 1),
        "baseline_clearance_time_s": round(baseline, 1),
        "baseline_is_estimate": True,
        "improvement_pct": round(improvement, 1),
        "override_fired": event.override_fired,
        "summary": summary,
    }


# --------------------------------------------------------------------------
# Self-test — no SUMO process
# --------------------------------------------------------------------------
def _selftest() -> None:
    print("§11.2 responder_messaging self-test\n")

    # -- baseline estimate arithmetic --------------------------------
    # 2-green junction, current phase just switched (age 0):
    #   remaining_current = 10
    #   other_phases (1) * (10 + 4) = 14
    #   + one yellow = 4
    #   = 28.0
    b = estimate_baseline_clearance_s(0.0, 2)
    assert b == 28.0, b
    # age 6s into min-green -> remaining_current = 4 -> 4 + 14 + 4 = 22.0
    assert estimate_baseline_clearance_s(6.0, 2) == 22.0
    # 3-green junction, age 0 -> 10 + 2*14 + 4 = 42.0
    assert estimate_baseline_clearance_s(0.0, 3) == 42.0
    print(f"  [OK] baseline estimate: 2-phase/age0 = {b:.1f}s, "
          f"3-phase/age0 = {estimate_baseline_clearance_s(0.0, 3):.1f}s")

    # -- message from a real override event -------------------------
    ev = EmergencyClearanceEvent(
        junction_id="J2", lane_id="N2_J2_0", direction="north",
        first_detection_sim_time=100.0, override_sim_time=105.0,
        green_onset_sim_time=108.0, closed_sim_time=140.0,
    )
    assert ev.clearance_time_s == 8.0
    msg = build_responder_message(ev, estimate_baseline_clearance_s(0.0, 2))
    assert msg["event"] == "emergency_clearance"
    assert msg["junction_id"] == "J2" and msg["lane_id"] == "N2_J2_0"
    assert msg["clearance_time_s"] == 8.0
    assert msg["baseline_clearance_time_s"] == 28.0
    assert msg["baseline_is_estimate"] is True
    # 100 * (1 - 8/28) = 71.4285... -> 71.4
    assert msg["improvement_pct"] == 71.4, msg["improvement_pct"]
    assert msg["override_fired"] is True
    assert "cleared for emergency vehicle in 8.0s" in msg["summary"]
    print(f"  [OK] override message: clearance 8.0s vs 28.0s est -> "
          f"{msg['improvement_pct']}% improvement")
    print(f"       summary: {msg['summary']}")

    # -- served-on-arrival event ----------------------------------
    ev2 = EmergencyClearanceEvent(
        junction_id="J1", lane_id="W1_J1_0", direction="west",
        first_detection_sim_time=50.0, green_onset_sim_time=48.0,
        closed_sim_time=70.0,
    )
    assert ev2.served_on_arrival and ev2.clearance_time_s == 0.0
    msg2 = build_responder_message(ev2, estimate_baseline_clearance_s(20.0, 2))
    assert msg2["clearance_time_s"] == 0.0
    assert msg2["improvement_pct"] == 100.0
    assert msg2["override_fired"] is False
    assert "already clear" in msg2["summary"]
    print(f"  [OK] served-on-arrival message: clearance 0.0s, "
          f"summary: {msg2['summary']}")

    # -- unresolved event must raise ----------------------------
    ev3 = EmergencyClearanceEvent(
        junction_id="J3", lane_id="E3_J3_0", direction="east",
        first_detection_sim_time=200.0, closed_sim_time=260.0,
    )
    try:
        build_responder_message(ev3, 30.0)
    except ValueError as exc:
        print(f"  [OK] unresolved event raises: {exc}")
    else:
        raise AssertionError("event with no green onset must raise")

    print(f"\nAll responder_messaging self-tests passed.")


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _selftest()

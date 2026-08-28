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

`baseline_clearance_time_s` is a STATED WORST-CASE MODEL ESTIMATE, not a
measured counterfactual — a true per-event A/B would need to re-run the
episode with the override disabled, which is impossible live and is what
`run_tier0_episode.py --b3` does offline instead. The estimate assumes the
target phase is LAST in rotation and sums: the min-green still owed on the
current phase, one yellow per phase-change on the way to the target
(n_green - 1 of them), and the full min-green of each phase STRICTLY
between here and the target (n_green - 2 of them). The target's own
min-green is not waited — service begins the instant it goes green. It is
labelled `baseline_is_estimate` AND `baseline_is_worst_case` in the
payload, and "worst-case" in the summary text.
"""

from __future__ import annotations

from coordinator.emergency_clearance import EmergencyClearanceEvent
from env.psychoflow_env import MIN_GREEN_S as _MIN_GREEN_S

# Imported, not re-typed, so it cannot drift from the env's anti-flicker
# constant (CLAUDE.md §8 discipline). Yellow stays a fixed nominal — the
# exact per-phase yellow is a TLS-program detail this decision-support
# estimate does not need to be precise about.
_YELLOW_S_EST = 4.0


def estimate_baseline_clearance_s(
    time_since_switch_s: float,
    n_green_phases: int,
    *,
    min_green_s: float = _MIN_GREEN_S,
    yellow_s_est: float = _YELLOW_S_EST,
) -> float:
    """WORST-CASE estimate: time until the target phase would next be served
    under normal rotation, assuming it is LAST in rotation.

    From the current phase to the target green you traverse:
      - the remainder of the current phase's min-green      (remaining_current)
      - one yellow off each of the (n_green - 1) phase-changes on the way
      - the full min-green of each of the (n_green - 2) phases STRICTLY between

    The target phase's own min-green is NOT included — you are served the
    instant it goes green. (An earlier revision double-counted: it charged
    (min_green + yellow) for every phase including the target and added a
    spurious trailing yellow, roughly doubling the 2-phase estimate.)
    """
    remaining_current = max(0.0, float(min_green_s) - float(time_since_switch_s))
    n = max(1, int(n_green_phases))
    yellows = (n - 1) * yellow_s_est
    intervening_min_greens = max(0, n - 2) * min_green_s
    return remaining_current + yellows + intervening_min_greens


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
            f"delay (vs. ~{baseline:.1f}s worst-case under normal rotation)."
        )
    else:
        summary = (
            f"{event.junction_id}: lane {event.lane_id} ({event.direction}) "
            f"cleared for emergency vehicle in {clearance:.1f}s "
            f"(vs. ~{baseline:.1f}s worst-case without override) — normal "
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
        "baseline_is_worst_case": True,
        "improvement_pct": round(improvement, 1),
        "override_fired": event.override_fired,
        "summary": summary,
    }


# --------------------------------------------------------------------------
# Self-test — no SUMO process
# --------------------------------------------------------------------------
def _selftest() -> None:
    print("§11.2 responder_messaging self-test\n")

    # -- baseline estimate: expectations DERIVED FROM FIRST PRINCIPLES here,
    #    not read back from the function under test (closes the "a wrong model
    #    passes its own test" gap). Worst case = target phase last in rotation:
    #    wait the current phase's remaining min-green, one yellow per
    #    phase-change on the way (n-1), and the full min-green of each phase
    #    STRICTLY between here and the target (n-2). The target's own min-green
    #    is NOT waited.
    MIN_G, YEL = _MIN_GREEN_S, _YELLOW_S_EST

    def independently_expected(age: float, n: int) -> float:
        remaining = max(0.0, MIN_G - age)
        return remaining + (n - 1) * YEL + max(0, n - 2) * MIN_G

    for age, n in [(0.0, 2), (6.0, 2), (0.0, 3), (0.0, 4), (15.0, 3), (0.0, 1)]:
        got = estimate_baseline_clearance_s(age, n)
        want = independently_expected(age, n)
        assert got == want, f"n={n} age={age}: {got} != {want}"

    # Hand-computed anchors (MIN_GREEN=10, YELLOW=4) — if MIN_GREEN_S changes
    # in the env these fail deliberately so the numbers get re-derived, not
    # silently drift.
    assert estimate_baseline_clearance_s(0.0, 2) == 14.0   # 10 + 1*4 + 0
    assert estimate_baseline_clearance_s(6.0, 2) == 8.0    #  4 + 1*4 + 0
    assert estimate_baseline_clearance_s(0.0, 3) == 28.0   # 10 + 2*4 + 1*10
    assert estimate_baseline_clearance_s(0.0, 4) == 42.0   # 10 + 3*4 + 2*10
    assert estimate_baseline_clearance_s(15.0, 3) == 18.0  #  0 + 2*4 + 1*10
    print(f"  [OK] worst-case baseline: 2-phase/age0 = "
          f"{estimate_baseline_clearance_s(0.0, 2):.1f}s, 3-phase/age0 = "
          f"{estimate_baseline_clearance_s(0.0, 3):.1f}s, 4-phase/age0 = "
          f"{estimate_baseline_clearance_s(0.0, 4):.1f}s "
          f"(prior double-counted revision gave 28/42/56)")

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
    assert msg["baseline_clearance_time_s"] == 14.0
    assert msg["baseline_is_estimate"] is True
    assert msg["baseline_is_worst_case"] is True
    # 100 * (1 - 8/14) = 42.857... -> 42.9   (was 71.4 against the inflated 28s)
    assert msg["improvement_pct"] == 42.9, msg["improvement_pct"]
    assert msg["override_fired"] is True
    assert "cleared for emergency vehicle in 8.0s" in msg["summary"]
    assert "worst-case without override" in msg["summary"]
    print(f"  [OK] override message: clearance 8.0s vs 14.0s worst-case -> "
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
    assert msg2["baseline_is_worst_case"] is True
    assert "already clear" in msg2["summary"]
    assert "worst-case under normal rotation" in msg2["summary"]
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

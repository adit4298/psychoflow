"""Spillover forecasting (§8.1).

Input: the digital twin snapshot only — PsychoFlowEnv._spillover() calls
`predictor.forecast(self._snapshot)` with no other argument, so this module
never sees runtime/green-phase state, only per-lane counts/waits/types and
`corridor_adjacency`.

Heuristic (not a learned model, per §8's own instruction to start simple):
net queue growth on the actual connecting lane group, extrapolated forward.

For each (upstream, downstream) pair in CORRIDOR_ADJACENCY, the lanes that
matter are downstream's lanes tagged approach == "west" — on this locked
linear W->E corridor (§0.1), that is always exactly the lane group fed by
the upstream neighbor (verified geometrically in Phase 2: J1->J2 and J2->J3
both arrive from the west at their downstream junction). This is a hardcoded
fact of the locked corridor topology, same category as CORRIDOR_ADJACENCY
itself, not a general-purpose direction inference.

"Outflow rate from J1" is proxied as the NET growth rate of that queue
(halted_count) between consecutive forecast() calls, rather than an
unmeasurable per-vehicle turn-routed outflow — no route/destination data
exists in the twin snapshot to isolate "vehicles at J1 headed for J2"
specifically. Net growth already nets J1's discharge into the link against
J2's own service of it, which is the more honest quantity for spillover risk
anyway: it is a real, observed trend, not a guessed turn fraction.

    rate = (queue_now - queue_prev) / dt
    predicted_queue_delta = rate * horizon_s

Confidence is not a learned uncertainty estimate — it is a fixed baseline,
penalized when the assumption a linear trend will hold is compromised:
    - cold start (no previous snapshot yet): 0.5, delta forced to 0.0
    - otherwise: 0.85, minus 0.2 (floor 0.5) if an incident is active at
      the downstream junction, since an incident breaks the "trend
      continues" assumption a linear extrapolation relies on.

Output has two shapes:
  - forecast() returns §8.1's exact JSON shape, one dict per adjacency pair.
    This is what §12 narration, §13.2's API and testing consume.
  - as_junction_dict() adapts that into {junction_id: (delta, confidence)},
    keyed by TO_JUNCTION — exactly what obs_action_spec.build_observation's
    `spillover` parameter expects for observation indices 10/11.

Resolution of §9.2's one-slot-per-row ambiguity (confirmed 2026-08-12,
Phase 5 design review): each junction's obs slot reports the forecast for
the pair where THAT junction is to_junction — "spillover about to arrive
here from upstream" — matching §8.1's own framing ("feeds the intervention
layer a head start... before downstream congestion is observed", i.e. the
DOWNSTREAM junction gets the head start). J1 has no upstream neighbor on
this corridor and is never a to_junction, so its slot is always (0.0, 0.0)
by omission from as_junction_dict()'s output — build_observation already
zero-fills any junction_id missing from the spillover dict, so J1 needs no
explicit entry. J2's OWN downstream impact on J3 is not lost either; it is
reported on J3's row instead of duplicated on J2's, which is a clean 1:1
fit for a 3-node linear chain (2 adjacency pairs, exactly 2 non-trivial
incoming slots) — not a compromise forced by the schema.

The predictor is STATEFUL (keeps the previous snapshot to compute a rate)
and MUST be reset every episode — PsychoFlowEnv.reset() calls
spillover_predictor.reset() right after twin.reset(), so episode 2's first
prediction is a genuine cold start rather than a rate computed against
episode 1's last snapshot (a huge, meaningless sim_time jump).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from twin.digital_twin import CORRIDOR_ADJACENCY

DEFAULT_HORIZON_S = 60.0

# Locked linear W->E corridor fact (§0.1) — see module docstring.
LINK_APPROACH = "west"

CONFIDENCE_BASE = 0.85
CONFIDENCE_COLD_START = 0.5
INCIDENT_CONFIDENCE_PENALTY = 0.2
CONFIDENCE_FLOOR = 0.5


def _link_queue(snapshot: dict, junction_id: str) -> float:
    """Sum of halted_count on junction_id's corridor-facing (west) lanes.

    halted_count, not vehicle_count, matching §7.1's own guidance ("this is
    what reward/scoring use, not raw count") — a queue is stopped vehicles.
    """
    lanes = snapshot["junctions"][junction_id]["lanes"]
    return sum(
        reading["halted_count"]
        for reading in lanes.values()
        if reading["approach"] == LINK_APPROACH
    )


def _incident_active_at(snapshot: dict, junction_id: str) -> bool:
    return any(
        incident["location"]["junction_id"] == junction_id
        for incident in snapshot["active_incidents"]
    )


@dataclass
class SpilloverPredictor:
    horizon_s: float = DEFAULT_HORIZON_S
    _prev: dict | None = field(default=None, init=False, repr=False)

    def reset(self) -> None:
        """Call once per PsychoFlowEnv.reset() — clears cross-episode state."""
        self._prev = None

    def forecast(self, snapshot: dict) -> list[dict]:
        """§8.1's exact output shape, one dict per CORRIDOR_ADJACENCY pair."""
        results = []
        for upstream, downstream in CORRIDOR_ADJACENCY:
            queue_now = _link_queue(snapshot, downstream)

            if self._prev is None:
                delta, confidence = 0.0, CONFIDENCE_COLD_START
            else:
                queue_prev = _link_queue(self._prev, downstream)
                dt = snapshot["sim_time"] - self._prev["sim_time"]
                rate = (queue_now - queue_prev) / dt if dt > 0 else 0.0
                delta = rate * self.horizon_s

                confidence = CONFIDENCE_BASE
                if _incident_active_at(snapshot, downstream):
                    confidence = max(CONFIDENCE_FLOOR, confidence - INCIDENT_CONFIDENCE_PENALTY)

            results.append({
                "from_junction": upstream,
                "to_junction": downstream,
                "horizon_s": self.horizon_s,
                "predicted_queue_delta": round(delta, 3),
                "confidence": round(confidence, 3),
            })

        self._prev = snapshot
        return results


def as_junction_dict(forecast: list[dict]) -> dict[str, tuple[float, float]]:
    """Adapt §8.1's list shape to build_observation's per-row expectation.

    Keyed by to_junction. J1 is intentionally absent (never a to_junction on
    this locked linear corridor) — build_observation already zero-fills any
    junction_id missing from this dict, giving J1 (0.0, 0.0) for free.
    """
    return {f["to_junction"]: (f["predicted_queue_delta"], f["confidence"]) for f in forecast}


# --------------------------------------------------------------------------
# Hand-scored scenarios (same discipline as env/reward.py's
# test_reward_scenarios — verified before this is wired into training).
# --------------------------------------------------------------------------
def _synthetic_snapshot(sim_time: float, west_halted: dict[str, int], incidents=None) -> dict:
    """Minimal §7.6-shaped snapshot with one 'west' lane per junction."""
    junctions = {}
    for jid in ("J1", "J2", "J3"):
        lane_id = f"{jid}_west_0"
        junctions[jid] = {
            "lanes": {
                lane_id: {
                    "lane_id": lane_id,
                    "approach": "west",
                    "vehicle_count": west_halted.get(jid, 0),
                    "halted_count": west_halted.get(jid, 0),
                    "type_composition": {"bike": 0, "auto": 0, "car": 0, "truck": 0, "ambulance": 0},
                    "wait_time_current": 0.0,
                    "wait_time_max_single_vehicle": 0.0,
                    "starvation_flag": False,
                }
            },
            "vision": {},
            "current_phase": 0,
            "lane_count": 2,
        }
    return {
        "sim_time": sim_time,
        "corridor_adjacency": CORRIDOR_ADJACENCY,
        "junctions": junctions,
        "active_incidents": incidents or [],
        "weather": {"state": "clear", "changed_at_sim_time": 0.0},
        "v2x_messages_recent": [],
    }


def test_spillover_scenarios() -> None:
    print("§8.1 hand-scored spillover scenarios:")
    p = SpilloverPredictor(horizon_s=60.0)

    def j2(forecast):
        return next(f for f in forecast if f["to_junction"] == "J2")

    f0 = j2(p.forecast(_synthetic_snapshot(0.0, {"J2": 4, "J3": 2})))
    print(f"  cold start      delta={f0['predicted_queue_delta']:+.2f}  conf={f0['confidence']:.2f}"
          f"   (expected +0.00 / 0.50)")
    assert f0["predicted_queue_delta"] == 0.0 and f0["confidence"] == 0.5

    f1 = j2(p.forecast(_synthetic_snapshot(5.0, {"J2": 4, "J3": 2})))  # flat
    print(f"  flat queue      delta={f1['predicted_queue_delta']:+.2f}   (expected +0.00)")
    assert f1["predicted_queue_delta"] == 0.0
    assert f1["confidence"] == CONFIDENCE_BASE

    f2 = j2(p.forecast(_synthetic_snapshot(10.0, {"J2": 8, "J3": 2})))  # 4->8 over 5s
    expected2 = (8 - 4) / 5.0 * 60.0
    print(f"  growing queue   delta={f2['predicted_queue_delta']:+.2f}   (expected {expected2:+.2f})")
    assert abs(f2["predicted_queue_delta"] - expected2) < 0.01

    f3 = j2(p.forecast(_synthetic_snapshot(15.0, {"J2": 2, "J3": 2})))  # 8->2 over 5s
    print(f"  draining queue  delta={f3['predicted_queue_delta']:+.2f}   (expected negative)")
    assert f3["predicted_queue_delta"] < 0.0

    incident = {
        "incident_id": "inc_test", "type": "lane_blocked",
        "location": {"junction_id": "J2", "lane_id": "J2_west_0"},
        "severity": "high", "affected_lanes": ["J2_west_0"],
        "reported_at_sim_time": 15.0, "estimated_duration_s": 300.0,
    }
    f4 = j2(p.forecast(_synthetic_snapshot(20.0, {"J2": 4, "J3": 2}, incidents=[incident])))
    expected_conf = CONFIDENCE_BASE - INCIDENT_CONFIDENCE_PENALTY
    print(f"  incident at J2  confidence={f4['confidence']:.2f}   (expected {expected_conf:.2f})")
    assert abs(f4["confidence"] - expected_conf) < 0.001

    p.reset()
    f5 = j2(p.forecast(_synthetic_snapshot(0.0, {"J2": 4, "J3": 2})))
    print(f"  post-reset      delta={f5['predicted_queue_delta']:+.2f}   (expected +0.00, cold start again)")
    assert f5["predicted_queue_delta"] == 0.0 and f5["confidence"] == 0.5

    print("\nAll §8.1 spillover assertions passed.")


if __name__ == "__main__":
    test_spillover_scenarios()

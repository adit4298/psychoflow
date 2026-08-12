"""Incident impact prediction (§8.2).

Input: one §7.3 incident event (a dict, e.g. from
digital_twin.snapshot["active_incidents"], which is already
Incident.to_dict()-shaped). Output: §8.2's exact shape.

`estimated_affected_junctions`: the incident's own junction (hop 0) plus
every junction reachable downstream via CORRIDOR_ADJACENCY — a forward walk,
since congestion propagates downstream along this linear corridor. Incident
at J1 -> [J1, J2, J3]; at J2 -> [J2, J3]; at J3 -> [J3] only (no downstream
neighbor).

`estimated_delay_increase_s` is the SUM of each affected junction's
hop-decayed contribution, not the max:

    contribution(hop) = BASE_DELAY_S * SEVERITY_VALUE[severity]
                         * len(affected_lanes) * DECAY_PER_HOP ** hop
    estimated_delay_increase_s = sum(contribution(hop) for each affected junction)

Sum, not max, is the deliberate choice (confirmed 2026-08-12, Phase 5 design
review): max would always equal the origin's own (hop 0) contribution
regardless of how far the incident actually ripples, which would make
DECAY_PER_HOP meaningless — the "affected_junctions" list could grow or
shrink and the reported number would never move. Sum is the total added
delay burden across the corridor, and it is the only aggregation that
actually uses the hop-decay term.

Worked example (signed off before build): incident at J1, severity=high
(1.0), 2 affected lanes, BASE_DELAY_S=30.0, DECAY_PER_HOP=0.5:
    J1 (hop 0): 30.0 * 1.0 * 2 * 0.5^0 = 60.0
    J2 (hop 1): 30.0 * 1.0 * 2 * 0.5^1 = 30.0
    J3 (hop 2): 30.0 * 1.0 * 2 * 0.5^2 = 15.0
    sum = 105.0s
See test_incident_impact_scenarios() below, which reproduces this exactly.

`horizon_s = min(MAX_HORIZON_S, estimated_duration_s)` — matches §8.2's own
example (a 600s-duration incident producing horizon_s=300) as a cap, not a
pass-through.
"""

from __future__ import annotations

from perception.incident_intake import SEVERITY_VALUE
from twin.digital_twin import CORRIDOR_ADJACENCY, CORRIDOR_JUNCTIONS

BASE_DELAY_S = 30.0
DECAY_PER_HOP = 0.5
MAX_HORIZON_S = 300.0


def _downstream_neighbor() -> dict[str, str | None]:
    m: dict[str, str | None] = {j: None for j in CORRIDOR_JUNCTIONS}
    for upstream, downstream in CORRIDOR_ADJACENCY:
        m[upstream] = downstream
    return m


_DOWNSTREAM = _downstream_neighbor()


def _affected_with_hops(origin: str) -> list[tuple[str, int]]:
    """[(junction, hop)] for origin (hop 0) and every downstream neighbor."""
    out = [(origin, 0)]
    current, hop = origin, 1
    while _DOWNSTREAM[current] is not None:
        current = _DOWNSTREAM[current]
        out.append((current, hop))
        hop += 1
    return out


def predict_incident_impact(incident: dict) -> dict:
    origin = incident["location"]["junction_id"]
    severity_value = SEVERITY_VALUE[incident["severity"]]
    n_lanes = max(1, len(incident["affected_lanes"]))

    affected = _affected_with_hops(origin)
    total_delay = sum(
        BASE_DELAY_S * severity_value * n_lanes * (DECAY_PER_HOP ** hop)
        for _, hop in affected
    )

    return {
        "incident_id": incident["incident_id"],
        "estimated_affected_junctions": [j for j, _ in affected],
        "estimated_delay_increase_s": round(total_delay, 2),
        "horizon_s": min(MAX_HORIZON_S, incident["estimated_duration_s"]),
    }


# --------------------------------------------------------------------------
# Hand-scored scenarios (same discipline as env/reward.py's
# test_reward_scenarios — verified before this is wired into training).
# --------------------------------------------------------------------------
def test_incident_impact_scenarios() -> None:
    print("§8.2 hand-scored incident-impact scenarios:")

    inc_j1_high = {
        "incident_id": "inc_0001", "type": "accident",
        "location": {"junction_id": "J1", "lane_id": "N1_J1_0"},
        "severity": "high", "affected_lanes": ["N1_J1_0", "N1_J1_1"],
        "reported_at_sim_time": 100.0, "estimated_duration_s": 600.0,
    }
    r1 = predict_incident_impact(inc_j1_high)
    print(f"  A  J1 high, 2 lanes   affected={r1['estimated_affected_junctions']}  "
          f"delay={r1['estimated_delay_increase_s']:.2f}s  horizon={r1['horizon_s']:.0f}s")
    print(f"     (expected [J1, J2, J3] / 105.00s / 300s -- capped, duration is 600s)")
    assert r1["estimated_affected_junctions"] == ["J1", "J2", "J3"]
    assert abs(r1["estimated_delay_increase_s"] - 105.0) < 0.01
    assert r1["horizon_s"] == 300.0

    inc_j3_low = {
        "incident_id": "inc_0002", "type": "roadworks",
        "location": {"junction_id": "J3", "lane_id": "S3_J3_0"},
        "severity": "low", "affected_lanes": ["S3_J3_0"],
        "reported_at_sim_time": 100.0, "estimated_duration_s": 120.0,
    }
    r2 = predict_incident_impact(inc_j3_low)
    print(f"  B  J3 low, 1 lane     affected={r2['estimated_affected_junctions']}  "
          f"delay={r2['estimated_delay_increase_s']:.2f}s  horizon={r2['horizon_s']:.0f}s")
    print(f"     (expected [J3] only -- no downstream neighbor / horizon=120s, under the cap)")
    assert r2["estimated_affected_junctions"] == ["J3"]
    assert r2["horizon_s"] == 120.0

    inc_j2_high = dict(inc_j1_high, incident_id="inc_0003",
                        location={"junction_id": "J2", "lane_id": "x"})
    r3 = predict_incident_impact(inc_j2_high)
    print(f"  C  J2 high, 2 lanes   affected={r3['estimated_affected_junctions']}  "
          f"delay={r3['estimated_delay_increase_s']:.2f}s")
    print(f"     (expected [J2, J3] / 90.00s = 60.0 (hop0) + 30.0 (hop1))")
    assert r3["estimated_affected_junctions"] == ["J2", "J3"]
    assert abs(r3["estimated_delay_increase_s"] - 90.0) < 0.01

    inc_j1_low = dict(inc_j1_high, incident_id="inc_0004", severity="low")
    r_low = predict_incident_impact(inc_j1_low)
    ratio = r1["estimated_delay_increase_s"] / r_low["estimated_delay_increase_s"]
    expected_ratio = 1.0 / 0.33
    print(f"\n  severity scaling: high={r1['estimated_delay_increase_s']:.2f}s vs "
          f"low={r_low['estimated_delay_increase_s']:.2f}s  ratio={ratio:.2f}  "
          f"(expected {expected_ratio:.2f})")
    assert abs(ratio - expected_ratio) < 0.05

    print("\nAll §8.2 incident-impact assertions passed.")


if __name__ == "__main__":
    test_incident_impact_scenarios()

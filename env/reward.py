"""Reward function (§9.4).

    reward = -penalty_starvation(lane_wait_times)      # non-linear
             + bonus_throughput(vehicles_cleared_this_step)
             - large_penalty_if(emergency_vehicle_not_prioritized)
             - small_penalty_if(phase_switched_this_step)

One corridor-level SCALAR per step, not per-junction rewards. §9.5's
shared-policy fallback depends on exactly this: "coordination emerges
only implicitly, through the shared reward signal across the corridor."

All weights live in RewardConfig so Checkpoint 1 (§16) can tune them
without touching this logic — §0.1 explicitly calls the 90s starvation
threshold a starting default to be tuned as the agent trains.

Hand-verified against three states before any training time was spent
(§9.4 requires this): balanced -> +1.28, one lane at the 90s threshold
-> +0.30, one lane at 200s -> -9.93. See test_reward_scenarios() at the
bottom, which reproduces those numbers as an executable check.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from perception.lane_sensor import DEFAULT_STARVATION_THRESHOLD_S
from twin.digital_twin import CORRIDOR_JUNCTIONS


@dataclass
class RewardConfig:
    starvation_threshold_s: float = DEFAULT_STARVATION_THRESHOLD_S  # 90.0

    # Non-linearity. §9.4: "2x wait != 2x penalty, much worse."
    # p = r^2 + knee * max(0, r-1)^2, where r = wait / threshold.
    # The r^2 term applies at every wait level so there is dense gradient
    # long before anything is starved; the hinge adds a sharp knee exactly
    # at the threshold so crossing it is qualitatively worse.
    starvation_knee: float = 4.0

    # Per junction: mean_weight * mean(lane penalties) + max_weight * max.
    #
    # The max term carries most of the weight for a specific reason. Pure
    # mean would break §16's Stage 2 check ("consistent across all 3
    # lane-counts"): one starved lane among a 4-lane junction's 16 lanes
    # is diluted 16x, but among a 2-lane junction's 8 lanes only 8x, so
    # identical physical starvation would score differently purely because
    # of lane count — the agent would look great on 4-lane and terrible on
    # 2-lane, which is that checkpoint's exact red flag. max is
    # lane-count invariant, and it is also the honest definition of
    # starvation: §9.1 is about the worst-off lane, not the average one.
    starvation_mean_weight: float = 0.5
    starvation_max_weight: float = 1.0

    w_starvation: float = 1.0
    w_throughput: float = 0.25
    w_emergency: float = 20.0
    w_switch: float = 0.5


@dataclass
class IntervalStats:
    """What happened during one decision interval, from the env's loop.

    `arrived` is accumulated across every SUMO sub-step, not read once at
    the end — the twin updates once per decision interval, so a single
    read would miss 4 of every 5 simulation steps.
    """

    arrived: int = 0
    switched_junctions: tuple[str, ...] = ()
    green_lanes: dict[str, set[str]] = field(default_factory=dict)


def lane_starvation_penalty(wait_s: float, config: RewardConfig) -> float:
    """Non-linear per-lane wait penalty. Exposed for hand-checking."""
    r = wait_s / config.starvation_threshold_s
    excess = max(0.0, r - 1.0)
    return r * r + config.starvation_knee * excess * excess


def compute_reward(
    snapshot: dict,
    stats: IntervalStats,
    config: RewardConfig | None = None,
) -> tuple[float, dict]:
    """Returns (reward, breakdown).

    The breakdown feeds §12.1's `score_breakdown` directly, so the
    explainability layer reports the numbers the agent was actually
    trained on rather than recomputing its own version of them.
    """
    config = config or RewardConfig()

    # ---- Term 1: starvation (non-linear, per lane) ----------------------
    starvation_total = 0.0
    per_junction: dict[str, float] = {}
    worst = {"junction_id": None, "lane_id": None, "wait_s": 0.0, "penalty": 0.0}

    for junction_id in CORRIDOR_JUNCTIONS:
        lanes = snapshot["junctions"][junction_id]["lanes"]
        if not lanes:
            per_junction[junction_id] = 0.0
            continue

        penalties = []
        for lane_id, reading in lanes.items():
            wait_s = reading["wait_time_max_single_vehicle"]
            penalty = lane_starvation_penalty(wait_s, config)
            penalties.append(penalty)
            if penalty > worst["penalty"]:
                worst = {
                    "junction_id": junction_id,
                    "lane_id": lane_id,
                    "wait_s": wait_s,
                    "penalty": round(penalty, 4),
                }

        junction_penalty = (
            config.starvation_mean_weight * (sum(penalties) / len(penalties))
            + config.starvation_max_weight * max(penalties)
        )
        per_junction[junction_id] = junction_penalty
        starvation_total += junction_penalty

    starvation_penalty = config.w_starvation * starvation_total

    # ---- Term 2: throughput --------------------------------------------
    # Vehicles that reached their destination. Chosen over "vehicles
    # crossing a junction" because it is the same quantity as §15.2's
    # total_throughput — the training signal and the evaluation metric are
    # then literally the same number.
    throughput_bonus = config.w_throughput * stats.arrived

    # ---- Term 3: emergency not prioritized (large) ----------------------
    # §10 makes ignoring an ambulance structurally impossible, but the
    # reward must AGREE with the validator — otherwise the policy spends
    # training fighting a gate it cannot win against, and the override
    # reads as an external correction rather than something the policy
    # also wants.
    emergency_blocked: list[str] = []
    for junction_id in CORRIDOR_JUNCTIONS:
        green = stats.green_lanes.get(junction_id, set())
        for lane_id, reading in snapshot["junctions"][junction_id]["lanes"].items():
            if reading["type_composition"].get("ambulance", 0) > 0 and lane_id not in green:
                emergency_blocked.append(junction_id)
                break  # one penalty per junction, not per lane

    emergency_penalty = config.w_emergency * len(emergency_blocked)

    # ---- Term 4: phase switch (small) -----------------------------------
    # Small on purpose: MIN_GREEN_S masking already bounds how often a
    # junction can switch, so this only needs to break ties against
    # pointless switching, not police it.
    switch_penalty = config.w_switch * len(stats.switched_junctions)

    reward = throughput_bonus - starvation_penalty - emergency_penalty - switch_penalty

    breakdown = {
        "total": round(reward, 4),
        "starvation_penalty": round(starvation_penalty, 4),
        "throughput_bonus": round(throughput_bonus, 4),
        "emergency_penalty": round(emergency_penalty, 4),
        "switch_penalty": round(switch_penalty, 4),
        "per_junction_starvation": {k: round(v, 4) for k, v in per_junction.items()},
        "worst_lane": worst,
        "emergency_blocked_junctions": emergency_blocked,
        "arrived_this_interval": stats.arrived,
        "switched_junctions": list(stats.switched_junctions),
    }
    return reward, breakdown


# --------------------------------------------------------------------------
# Hand-scored scenarios (§9.4: "Print reward for hand-built test scenarios
# before training — an intentionally-starved lane must produce a clearly
# worse reward than a balanced one, or the formula is wrong before any
# training time is spent.")
# --------------------------------------------------------------------------
def _synthetic_snapshot(waits: dict[str, list[float]], ambulance_lane: str | None = None) -> dict:
    """Build a minimal §7.6-shaped snapshot from per-junction wait lists."""
    junctions = {}
    for junction_id, lane_waits in waits.items():
        lanes = {}
        for i, wait_s in enumerate(lane_waits):
            lane_id = f"{junction_id}_lane_{i}"
            lanes[lane_id] = {
                "lane_id": lane_id,
                "approach": "north",
                "vehicle_count": 0,
                "halted_count": 0,
                "type_composition": {
                    "bike": 0, "auto": 0, "car": 0, "truck": 0,
                    "ambulance": 1 if lane_id == ambulance_lane else 0,
                },
                "wait_time_current": 0.0,
                "wait_time_max_single_vehicle": wait_s,
                "starvation_flag": wait_s > 90.0,
            }
        junctions[junction_id] = {"lanes": lanes, "vision": {}, "current_phase": 0,
                                  "lane_count": len(lane_waits) // 4}
    return {
        "sim_time": 0.0,
        "corridor_adjacency": [["J1", "J2"], ["J2", "J3"]],
        "junctions": junctions,
        "active_incidents": [],
        "weather": {"state": "clear", "changed_at_sim_time": 0.0},
        "v2x_messages_recent": [],
    }


def test_reward_scenarios() -> None:
    """Executable version of the hand-calculations signed off before build."""
    config = RewardConfig()
    balanced_j = [20.0] * 8  # a 2-lane junction: 4 approaches x 2 lanes

    def run(label, waits, stats, expected):
        reward, breakdown = compute_reward(_synthetic_snapshot(*waits), stats, config)
        status = "OK" if abs(reward - expected) < 0.02 else "MISMATCH"
        print(f"  {label:<34} reward = {reward:+8.3f}   (expected {expected:+.2f})  [{status}]")
        return reward, breakdown

    print("§9.4 hand-scored reward scenarios (6 vehicles arrived per interval):")
    six = IntervalStats(arrived=6)

    r_a, _ = run("A  balanced corridor",
                 ({"J1": balanced_j, "J2": balanced_j, "J3": balanced_j},), six, +1.28)
    r_b, _ = run("B  one lane at 90s threshold",
                 ({"J1": [90.0] + [20.0] * 15, "J2": balanced_j, "J3": balanced_j},), six, +0.30)
    r_c, _ = run("C  one lane at 200s",
                 ({"J1": [200.0] + [20.0] * 15, "J2": balanced_j, "J3": balanced_j},), six, -9.93)

    assert r_a > r_b > r_c, "starved states must score strictly worse than balanced"

    p100 = lane_starvation_penalty(100.0, config)
    p200 = lane_starvation_penalty(200.0, config)
    print(f"\n  non-linearity: penalty(100s) = {p100:.3f}, penalty(200s) = {p200:.3f}"
          f"  ->  2x wait costs {p200 / p100:.1f}x penalty")
    assert p200 / p100 > 4.0, "§9.4 requires 2x wait to be MUCH worse than 2x penalty"

    print()
    amb_snapshot = ({"J1": balanced_j, "J2": balanced_j, "J3": balanced_j}, "J2_lane_0")
    ignored, _ = compute_reward(
        _synthetic_snapshot(*amb_snapshot), IntervalStats(arrived=6, green_lanes={}), config)
    served, _ = compute_reward(
        _synthetic_snapshot(*amb_snapshot),
        IntervalStats(arrived=6, switched_junctions=("J2",), green_lanes={"J2": {"J2_lane_0"}}),
        config)
    print(f"  D  ambulance ignored               reward = {ignored:+8.3f}   (expected -18.72)")
    print(f"     ambulance prioritized           reward = {served:+8.3f}   (expected  +0.78)")
    print(f"     gap in favour of prioritizing   {served - ignored:.2f}")
    assert served - ignored > 15.0, "emergency term must dominate throughput"

    flicker, _ = compute_reward(
        _synthetic_snapshot({"J1": balanced_j, "J2": balanced_j, "J3": balanced_j}),
        IntervalStats(arrived=6, switched_junctions=("J1", "J2", "J3")), config)
    print(f"\n  E  all three junctions switch      reward = {flicker:+8.3f}   (expected -0.22)")
    assert flicker < r_a, "switching for no reason must cost something"

    print("\nAll §9.4 reward assertions passed.")


if __name__ == "__main__":
    test_reward_scenarios()

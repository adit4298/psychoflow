"""§18 Phase 3 verification harness — env reset() + a handful of step()s.

Checks the Phase 3 done bar against a live SUMO instance:
  - one manual reset() and several step() calls on the Phase 1 corridor,
    driving the Phase 2 digital twin
  - the actual observation shape and values for one step
  - a masked action genuinely REJECTED, two independent ways

Not part of §6's folder structure — verification scaffolding, same as
sim/run_perception_episode.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from env.obs_action_spec import (  # noqa: E402
    JUNCTION_BLOCK_START,
    LANE_FEATURES,
    MAX_PHASES,
    WEATHER_BLOCK_START,
    describe,
)
from env.psychoflow_env import MIN_GREEN_S, InvalidActionError, PsychoFlowEnv, ScenarioConfig  # noqa: E402
from twin.digital_twin import CORRIDOR_JUNCTIONS  # noqa: E402

RULE = "=" * 78


def show_masks(env, label):
    masks = env.action_masks()
    print(f"  {label}")
    for j, junction_id in enumerate(CORRIDOR_JUNCTIONS):
        block = masks[j * MAX_PHASES : (j + 1) * MAX_PHASES]
        valid = [s for s in range(MAX_PHASES) if block[s]]
        rt = env._runtime()[junction_id]
        print(f"    {junction_id}: mask={block.astype(int).tolist()} valid_slots={valid} "
              f"(n_green={rt['n_green_phases']}, green_age={rt['time_since_switch_s']:.0f}s)")
    return masks


def run_full_episode() -> None:
    """§18 Phase 3 done bar: 'random-action agent runs a full episode
    without crashing'. Full episode = 3600 simulated seconds (§9.2), or
    early termination when every vehicle has cleared."""
    env = PsychoFlowEnv(scenario_config=ScenarioConfig(lane_counts=(4, 3, 2)), seed=7)
    obs, info = env.reset()
    rng = np.random.default_rng(7)

    steps = 0
    total_reward = 0.0
    worst_wait = 0.0
    starvation_steps = 0
    terminated = truncated = False

    while not (terminated or truncated):
        masks = env.action_masks()
        action = [
            int(rng.choice([s for s in range(MAX_PHASES) if masks[j * MAX_PHASES + s]]))
            for j in range(len(CORRIDOR_JUNCTIONS))
        ]
        obs, reward, terminated, truncated, info = env.step(action)
        steps += 1
        total_reward += reward
        b = info["reward_breakdown"]
        worst_wait = max(worst_wait, b["worst_lane"]["wait_s"])
        if b["worst_lane"]["wait_s"] > 90.0:
            starvation_steps += 1
        if steps % 120 == 0:
            print(f"    step {steps:4d}  t={info['sim_time']:6.0f}s  "
                  f"reward={reward:+8.3f}  cum={total_reward:+10.1f}  "
                  f"arrived={info['arrived_total']:5d}  worst_wait={b['worst_lane']['wait_s']:6.1f}s")

    print(f"\n    EPISODE END  steps={steps}  sim_time={info['sim_time']:.0f}s  "
          f"terminated={terminated}  truncated={truncated}")
    print(f"    total_reward={total_reward:.1f}  mean_reward/step={total_reward / steps:+.3f}")
    print(f"    vehicles arrived={info['arrived_total']}  "
          f"worst single-vehicle wait={worst_wait:.1f}s  "
          f"steps with a starved lane={starvation_steps}/{steps}")
    assert steps > 0 and (terminated or truncated), "episode did not end cleanly"
    env.close()


def main() -> None:
    if "--full-episode" in sys.argv:
        print(RULE)
        print("FULL EPISODE — random masked actions, 3600 simulated seconds")
        print(RULE)
        run_full_episode()
        return

    env = PsychoFlowEnv(
        scenario_config=ScenarioConfig(lane_counts=(4, 3, 2)),
        seed=42,
    )

    print(RULE)
    print("SPACES")
    print(RULE)
    print(f"  observation_space = {env.observation_space}")
    print(f"  action_space      = {env.action_space}")

    obs, info = env.reset()
    print(f"\n  reset() -> obs.shape={obs.shape} dtype={obs.dtype} "
          f"lane_counts={info['lane_counts']}")
    assert obs.shape == (3, 191), f"expected (3, 191), got {obs.shape}"
    assert env.observation_space.contains(obs), "observation outside declared space"

    print()
    print(RULE)
    print("STEPPING — 6 decision steps (5 simulated seconds each)")
    print(RULE)
    rng = np.random.default_rng(0)
    for i in range(6):
        masks = env.action_masks()
        # Sample only from valid slots, the way MaskablePPO does.
        action = [
            int(rng.choice([s for s in range(MAX_PHASES) if masks[j * MAX_PHASES + s]]))
            for j in range(len(CORRIDOR_JUNCTIONS))
        ]
        obs, reward, terminated, truncated, info = env.step(action)
        b = info["reward_breakdown"]
        print(f"  step {i+1}  t={info['sim_time']:6.1f}s  action={action}  "
              f"reward={reward:+7.3f}  (starv={b['starvation_penalty']:.3f} "
              f"thru={b['throughput_bonus']:.2f} switch={b['switch_penalty']:.2f})  "
              f"arrived_total={info['arrived_total']}  term={terminated} trunc={truncated}")

    print()
    print(RULE)
    print(f"OBSERVATION FOR THIS STEP — shape {obs.shape}, {obs.size} floats")
    print(RULE)
    readback = describe(obs)
    for junction_id, jd in readback.items():
        print(f"\n  {junction_id}: lane_count={jd['lane_count']} "
              f"active_lane_slots={jd['active_lane_slots']}/16 "
              f"current_green_slot={jd['current_green_slot']} "
              f"n_green_phases={jd['n_green_phases']}")
        print(f"        time_since_switch={jd['time_since_switch_s']}s  "
              f"weather={jd['weather']}  incident={jd['incident_active']} "
              f"(sev {jd['incident_severity']})  "
              f"spillover=({jd['spillover_delta']}, {jd['spillover_confidence']})")
        for name, lane in list(jd["lanes"].items())[:3]:
            print(f"        {name}: count={lane['vehicle_count']:.0f} "
                  f"halted={lane['halted_count']:.0f} wait_cur={lane['wait_current']:.1f} "
                  f"wait_max={lane['wait_max']:.1f} starved={lane['starved']} "
                  f"types={ {k: v for k, v in lane['types'].items() if v} }")
        print(f"        ... {jd['active_lane_slots'] - min(3, jd['active_lane_slots'])} more active slots")

    print()
    print("  RAW FLOATS (J3 row — the 2-lane junction, so slots 2-3 of each approach are padding)")
    j3 = obs[2]
    print(f"    lane slot 0 (north lane 0), 11 features : {np.round(j3[0:11], 4).tolist()}")
    print(f"    lane slot 1 (north lane 1), 11 features : {np.round(j3[11:22], 4).tolist()}")
    print(f"    lane slot 2 (PADDING),      11 features : {np.round(j3[22:33], 4).tolist()}")
    print(f"    lane slot 3 (PADDING),      11 features : {np.round(j3[33:44], 4).tolist()}")
    print(f"    junction scalars [{JUNCTION_BLOCK_START}:{WEATHER_BLOCK_START}]      : "
          f"{np.round(j3[JUNCTION_BLOCK_START:WEATHER_BLOCK_START], 4).tolist()}")
    print(f"    weather one-hot  [{WEATHER_BLOCK_START}:191]     : "
          f"{np.round(j3[WEATHER_BLOCK_START:], 4).tolist()}")
    padding_ok = not j3[22:44].any()
    print(f"\n    padding slots all zero (incl. valid_mask): {padding_ok}")
    assert padding_ok, "§9.2 requires unused lane slots zero-filled"

    print()
    print(RULE)
    print("MASKING — deliberately submitting invalid actions")
    print(RULE)
    show_masks(env, "current masks:")

    # --- Rejection 1: a phase slot that does not exist at this junction ---
    print("\n  [1] J3 is 2-lane -> 2 green phases, so slot 2 is padding and never valid.")
    print("      Submitting action [0, 0, 2] ...")
    try:
        env.step([0, 0, 2])
        print("      *** NOT REJECTED — masking is broken ***")
        raise SystemExit(1)
    except InvalidActionError as exc:
        print(f"      REJECTED: InvalidActionError: {exc}")

    # --- Rejection 2: switching before MIN_GREEN_S has elapsed ------------
    print(f"\n  [2] Force a switch at J1, then immediately try to switch again")
    print(f"      before MIN_GREEN_S={MIN_GREEN_S:.0f}s has elapsed.")
    masks = env.action_masks()
    j1_valid = [s for s in range(MAX_PHASES) if masks[s]]
    target = next(s for s in j1_valid if s != env._runtime()["J1"]["current_green_slot"])
    env.step([target, 0, 0])
    rt = env._runtime()["J1"]
    print(f"      after switch: J1 green_slot={rt['current_green_slot']} "
          f"green_age={rt['time_since_switch_s']:.0f}s")
    show_masks(env, "masks now:")
    other = next(s for s in range(MAX_PHASES) if s != rt["current_green_slot"])
    print(f"      Submitting action [{other}, 0, 0] ...")
    try:
        env.step([other, 0, 0])
        print("      *** NOT REJECTED — min-green masking is broken ***")
        raise SystemExit(1)
    except InvalidActionError as exc:
        print(f"      REJECTED: InvalidActionError: {exc}")

    # --- Control: the same action IS accepted once min green has elapsed --
    print(f"\n  [3] Control — hold until min green elapses, then the same switch is accepted.")
    while env._runtime()["J1"]["time_since_switch_s"] < MIN_GREEN_S:
        env.step([env._runtime()["J1"]["current_green_slot"], 0, 0])
    rt = env._runtime()["J1"]
    print(f"      J1 green_age now {rt['time_since_switch_s']:.0f}s")
    obs, reward, _, _, info = env.step([other, 0, 0])
    print(f"      action [{other}, 0, 0] ACCEPTED — switched_junctions={info['switched_junctions']}")

    print()
    print(RULE)
    print("All Phase 3 done-bar checks passed.")
    print(RULE)
    env.close()


if __name__ == "__main__":
    # Tier 1 SUMO beacon (sim/sumo_activity.py): refuse to launch
    # concurrent SUMO while a training run or the backend is live.
    from sim.sumo_activity import require_free
    require_free('env smoke test')
    main()

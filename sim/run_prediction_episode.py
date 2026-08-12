"""§18 Phase 5 verification harness — prediction (§8.1 spillover, §8.2 incident
impact), exercised through the REAL integration path (PsychoFlowEnv +
build_observation), not just the standalone predictor.

Done bar: "predictions visibly change when an incident is manually injected."
C1 additionally demonstrates §8.1 responding to genuine queue growth, not
just a manufactured incident.

  --c1   spillover: withhold J2's west approach (fed from J1) and show
         obs indices 10/11 for J2 — decoded via obs_action_spec.describe(),
         the exact readback the policy itself would see — trend upward as
         the real queue grows.
  --c2   incident impact: inject an incident at J1 mid-episode and show
         predict_incident_impact()'s output change from "no active incident"
         to a real, corridor-ripple prediction.

No arguments runs both.

Not part of §6's folder structure — verification scaffolding, same category
as run_perception_episode.py, run_env_smoke.py, run_tier0_episode.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from env.obs_action_spec import MAX_PHASES, describe  # noqa: E402
from env.psychoflow_env import PsychoFlowEnv, ScenarioConfig  # noqa: E402
from prediction.incident_impact import predict_incident_impact  # noqa: E402
from prediction.spillover import SpilloverPredictor  # noqa: E402
from twin.digital_twin import CORRIDOR_JUNCTIONS  # noqa: E402

RULE = "=" * 78


def victim_lanes_for(env, junction_id: str, approach: str) -> frozenset[str]:
    lanes = env.twin.snapshot["junctions"][junction_id]["lanes"]
    return frozenset(
        lane_id for lane_id, reading in lanes.items() if reading["approach"] == approach
    )


class WithholdController:
    """Never serves `approach` at `junction_id`; random elsewhere.

    Manufactures real queue growth on that approach so §8.1's forecast has
    a genuine trend to respond to — same spirit as run_tier0_episode.py's
    AdversarialController, simplified for this harness's own step loop.
    """

    def __init__(self, junction_id: str, victim_lanes: frozenset[str], rng):
        self.junction_id = junction_id
        self.victim_lanes = victim_lanes
        self.rng = rng

    def act(self, masks, served) -> list[int]:
        action = []
        for j, jid in enumerate(CORRIDOR_JUNCTIONS):
            valid = [s for s in range(MAX_PHASES) if masks[j * MAX_PHASES + s]]
            if jid == self.junction_id:
                choice = min(
                    valid,
                    key=lambda s: (len(served[jid].get(s, frozenset()) & self.victim_lanes), s),
                )
            else:
                choice = int(self.rng.choice(valid))
            action.append(choice)
        return action


def c1_spillover(seed=41, steps=200, sample_every=10):
    print(RULE)
    print("C1 — spillover: withhold J2's west approach (fed from J1)")
    print("     Read via describe(obs) — the exact obs indices 10/11 the policy sees.")
    print(RULE)

    predictor = SpilloverPredictor()
    env = PsychoFlowEnv(
        scenario_config=ScenarioConfig(lane_counts=(4, 3, 2)),
        spillover_predictor=predictor,
        seed=seed,
    )
    obs, info = env.reset()
    served = env.phase_served_lanes()
    victim = victim_lanes_for(env, "J2", "west")
    controller = WithholdController("J2", victim, np.random.default_rng(seed))

    print(f"  withholding J2 west lanes: {sorted(victim)}")
    print(f"  {'step':>5}  {'sim_time':>9}  {'J2 west halted':>15}  "
          f"{'J1->J2 delta':>13}  {'confidence':>10}")

    history = []
    for step in range(1, steps + 1):
        masks = env.action_masks()
        action = controller.act(masks, served)
        obs, reward, terminated, truncated, info = env.step(action)

        if step % sample_every == 0 or step == steps:
            snap = env.twin.snapshot
            halted = sum(
                r["halted_count"] for r in snap["junctions"]["J2"]["lanes"].values()
                if r["approach"] == "west"
            )
            decoded = describe(obs)["J2"]
            history.append((step, snap["sim_time"], halted,
                             decoded["spillover_delta"], decoded["spillover_confidence"]))
            print(f"  {step:5d}  {snap['sim_time']:9.0f}  {halted:15d}  "
                  f"{decoded['spillover_delta']:+13.2f}  {decoded['spillover_confidence']:10.2f}")

        if terminated or truncated:
            break

    env.close()

    early = history[len(history) // 3]
    late = history[-1]
    print(f"\n  early (step {early[0]}): halted={early[2]}  delta={early[3]:+.2f}")
    print(f"  late  (step {late[0]}): halted={late[2]}  delta={late[3]:+.2f}")
    if late[2] > early[2] and late[3] > early[3]:
        print("  PASS — queue grew and predicted_queue_delta grew with it")
    else:
        print("  *** WARNING — queue/delta did not both rise; inspect the trace above ***")


def c2_incident_impact(seed=41):
    print(RULE)
    print("C2 — incident impact: inject at J1, confirm the prediction changes")
    print(RULE)

    env = PsychoFlowEnv(scenario_config=ScenarioConfig(lane_counts=(4, 3, 2)), seed=seed)
    obs, info = env.reset()

    print("  BEFORE any incident: active_incidents =", env.twin.snapshot["active_incidents"])

    incident = env.twin.incidents.report(
        incident_type="accident",
        junction_id="J1",
        lane_id="N1_J1_0",
        severity="high",
        affected_lanes=["N1_J1_0", "N1_J1_1"],
        reported_at_sim_time=env.twin.snapshot["sim_time"],
        estimated_duration_s=600.0,
    )
    print(f"  INJECTED: {incident.incident_id} at J1, severity=high, 2 lanes")

    masks = env.action_masks()
    action = [next(s for s in range(MAX_PHASES) if masks[j * MAX_PHASES + s]) for j in range(3)]
    obs, reward, terminated, truncated, info = env.step(action)

    active = env.twin.snapshot["active_incidents"]
    print(f"  AFTER one step: active_incidents = {[i['incident_id'] for i in active]}")
    assert active, "incident should still be active"

    result = predict_incident_impact(active[0])
    print(f"\n  §8.2 prediction:")
    print(f"    incident_id                  = {result['incident_id']}")
    print(f"    estimated_affected_junctions = {result['estimated_affected_junctions']}")
    print(f"    estimated_delay_increase_s   = {result['estimated_delay_increase_s']:.2f}s")
    print(f"    horizon_s                    = {result['horizon_s']:.0f}s")

    assert result["estimated_affected_junctions"] == ["J1", "J2", "J3"], (
        "an incident at J1 must ripple through the whole downstream corridor"
    )
    assert result["estimated_delay_increase_s"] > 0.0
    print("\n  PASS — prediction reflects the injected incident and ripples downstream")

    env.close()


def main() -> None:
    args = set(sys.argv[1:])
    run_all = not args or "--all" in args
    if run_all or "--c1" in args:
        c1_spillover()
        print()
    if run_all or "--c2" in args:
        c2_incident_impact()
        print()


if __name__ == "__main__":
    main()

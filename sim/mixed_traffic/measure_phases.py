"""Item 3c: ACTUAL sim-time green-phase durations under the deployed Stage 4
policy on the demo corridor (4,3,2).

MEASUREMENT ONLY. Nothing here modifies Stage 4's decision timing, the RL
policy, DECISION_INTERVAL_S, MIN_GREEN_S, the reward or the validator. It
reuses `run_episode` + `ppo_picker` verbatim from the existing harnesses --
the same code path every recorded Stage 4 number came from -- and only adds a
read-only `on_step` observer, which `run_episode` already supports.

A green's duration is measured as the sim-time between the step at which a
junction's `current_green_slot` changes and the step at which it next changes.
`on_step` fires once per decision step (5s), which is exactly the resolution
phase changes can occur at, so nothing is aliased away here (unlike item 1,
which genuinely needs 1s).
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# ^ Derived, not hardcoded. This harness ran from a scratchpad during the
#   mixed-traffic work and carried an absolute path to one machine's checkout.
#   parents[2] is the repo root from sim/mixed_traffic/. See README.md here.
sys.path.insert(0, str(REPO_ROOT))

from sb3_contrib import MaskablePPO  # noqa: E402

from env.psychoflow_env import (  # noqa: E402
    DECISION_INTERVAL_S, MIN_GREEN_S, PsychoFlowEnv, ScenarioConfig,
)
from prediction.spillover import SpilloverPredictor  # noqa: E402
from training.evaluate_stage import ppo_picker  # noqa: E402
from sim.run_tier0_episode import run_episode  # noqa: E402

CKPT = REPO_ROOT / "training" / "checkpoints" / "stage4" / \
    "psychoflow_stage4_153600_steps_final.zip"
DEMO_VTYPES = REPO_ROOT / "sim" / "networks" / "vehicle_types_demo.add.xml"
JUNCTIONS = ("J1", "J2", "J3")


def main(demo: bool = False) -> None:
    extra = {'vtype_file': DEMO_VTYPES, 'lateral_resolution': 0.4} if demo else {}
    out = Path(__file__).parent / (
        'measure_phases_demo.json' if demo else 'measure_phases.json')
    env = PsychoFlowEnv(
        scenario_config=ScenarioConfig(lane_counts=(4, 3, 2)),
        seed=7,
        spillover_predictor=SpilloverPredictor(),
        **extra,
    )
    model = MaskablePPO.load(str(CKPT))

    # {junction: (slot, started_at_sim_time)}
    open_green: dict[str, tuple[int, float]] = {}
    durations: dict[str, list[float]] = defaultdict(list)
    per_slot: dict[str, list[float]] = defaultdict(list)

    def on_step(e, info):
        t = info["sim_time"]
        rt = e._runtime()
        for jid in JUNCTIONS:
            slot = rt[jid]["current_green_slot"]
            prev = open_green.get(jid)
            if prev is None:
                open_green[jid] = (slot, t)
            elif prev[0] != slot:
                durations[jid].append(t - prev[1])
                per_slot[f"{jid}_slot{prev[0]}"].append(t - prev[1])
                open_green[jid] = (slot, t)

    stats = run_episode(env, ppo_picker(env, model, deterministic=True),
                        on_step=on_step)
    env.close()

    def summarize(vals):
        v = sorted(vals)
        return {
            "n": len(v),
            "min": v[0], "p25": v[int(0.25 * (len(v) - 1))],
            "median": statistics.median(v),
            "p75": v[int(0.75 * (len(v) - 1))],
            "p90": v[int(0.90 * (len(v) - 1))],
            "max": v[-1],
            "mean": round(statistics.mean(v), 2),
        }

    result = {
        "checkpoint": CKPT.name,
        "demo_driving": demo,
        "corridor": "4/3/2", "seed": 7, "deterministic": True,
        "DECISION_INTERVAL_S": DECISION_INTERVAL_S,
        "MIN_GREEN_S": MIN_GREEN_S,
        "episode": {
            "steps": stats["steps"], "sim_time": stats["sim_time"],
            "arrived": stats["arrived"], "mean_reward": round(stats["mean_reward"], 4),
            "worst_wait": stats["worst_wait"],
            "terminated": stats["terminated"], "truncated": stats["truncated"],
        },
        "green_duration_s": {j: summarize(durations[j]) for j in JUNCTIONS
                             if durations[j]},
        "green_duration_s_all": summarize(
            [x for j in JUNCTIONS for x in durations[j]]
        ),
        "per_slot": {k: summarize(v) for k, v in sorted(per_slot.items()) if v},
        "switches_per_junction": {j: len(durations[j]) for j in JUNCTIONS},
    }
    out.write_text(json.dumps(result, indent=2))
    a = result['green_duration_s_all']
    print(f"demo_driving={demo}  episode: {result['episode']}")
    print(f"  slot-interval s: n={a['n']} min={a['min']} p25={a['p25']} "
          f"median={a['median']} p75={a['p75']} p90={a['p90']} max={a['max']} mean={a['mean']}")


if __name__ == "__main__":
    from sim.sumo_activity import require_free
    require_free("measure_phases.py (item 3c phase-duration measurement)")
    main(demo="--demo" in sys.argv)

"""§16 Stage 1 checkpoint: "beats random-action baseline on wait time."

Drives a trained MaskablePPO checkpoint through PsychoFlowEnv using the same
episode-driver and metrics (`run_episode`, `report`) as
sim/run_tier0_episode.py's B1/B4, on the identical corridor/seed, so the
printed numbers sit directly next to the already-recorded baseline rows
rather than requiring a fresh, differently-seeded baseline run.

Same seed (7), same corridor (4/3/2), validator ON — matching B1/B4 exactly.
Also constructs the env with a live SpilloverPredictor (unlike B1/B4, which
predate Phase 5 and run predictor-less): the checkpoint was TRAINED with a
live predictor, so evaluating it against permanently-zero spillover inputs
would score it on an observation distribution it never trained on.

Usage:
    python -m training.evaluate_stage <checkpoint.zip> [--seed 7]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sb3_contrib import MaskablePPO  # noqa: E402

from env.obs_action_spec import build_observation  # noqa: E402
from env.psychoflow_env import PsychoFlowEnv, ScenarioConfig  # noqa: E402
from prediction.spillover import SpilloverPredictor  # noqa: E402
from sim.run_tier0_episode import RULE, run_episode  # noqa: E402

# §16's measured baselines this run is judged against — see CLAUDE.md §8's
# Checkpoint-1 table / master plan §16. Both already corridor 4/3/2, seed 7,
# validator ON. Quoted here rather than re-derived.
SHIELDED_RANDOM_BASELINE = {
    "label": "Random, validator ON (Phase 4, run_tier0_episode.py --b4)",
    "steps": 646, "sim_time": 3240, "terminated": True,
    "mean_reward": -2.4, "arrived": 4668, "worst_wait": 141.0,
    "starved_steps": 543,
}
TIER0_BASELINE = {
    "label": "Tier 0 (Phase 4, run_tier0_episode.py --b1)",
    "steps": 627, "sim_time": 3145, "terminated": True,
    "mean_reward": 1.2, "arrived": 4668, "worst_wait": 41.0,
    "starved_steps": 0,
}


def ppo_picker(env, model, deterministic=True):
    """Adapts run_episode's (snapshot, runtime, masks, served) -> action
    contract to a trained policy, which needs the actual observation array
    rather than the raw snapshot. build_observation() is a pure function of
    (snapshot, runtime, spillover) — the same one PsychoFlowEnv.step() calls
    internally — so reconstructing obs here reproduces exactly what the env
    would have produced, without modifying run_episode() itself.
    """

    def pick(snapshot, runtime, masks, served):
        obs = build_observation(snapshot, runtime, env._spillover())
        action, _ = model.predict(obs, action_masks=masks, deterministic=deterministic)
        return action, {}

    return pick


def report_three_way(label, stats, baseline_a, baseline_b):
    print(f"\n  {label}")
    print(f"    steps={stats['steps']}  sim_time={stats['sim_time']:.0f}s  "
          f"terminated={stats['terminated']} truncated={stats['truncated']}")

    rows = [
        ("mean reward / step", f"{stats['mean_reward']:+.1f}",
         f"{baseline_a['mean_reward']:+.1f}", f"{baseline_b['mean_reward']:+.1f}"),
        ("vehicles arrived", f"{stats['arrived']}",
         f"{baseline_a['arrived']}", f"{baseline_b['arrived']}"),
        ("worst single-vehicle wait", f"{stats['worst_wait']:.1f}s",
         f"{baseline_a['worst_wait']:.1f}s", f"{baseline_b['worst_wait']:.1f}s"),
        ("steps with a starved lane",
         f"{stats['starved_steps']}/{stats['steps']} "
         f"({100 * stats['starved_steps'] / max(1, stats['steps']):.0f}%)",
         f"{baseline_a['starved_steps']}/{baseline_a['steps']} "
         f"({100 * baseline_a['starved_steps'] / baseline_a['steps']:.0f}%)",
         f"{baseline_b['starved_steps']}/{baseline_b['steps']} "
         f"({100 * baseline_b['starved_steps'] / baseline_b['steps']:.0f}%)"),
    ]
    width = max(len(r[0]) for r in rows)
    print(f"    {'metric':<{width}}   {'this checkpoint':>18}   "
          f"{'shielded-random':>18}   {'Tier 0':>18}")
    for name, mine, a, b in rows:
        print(f"    {name:<{width}}   {mine:>18}   {a:>18}   {b:>18}")

    if stats["overrides"]:
        by_rule = {}
        for record in stats["overrides"]:
            key = (record["rule"], record["outcome"])
            by_rule[key] = by_rule.get(key, 0) + 1
        print(f"    §10 overrides: {len(stats['overrides'])} total")
        for (rule, outcome), n in sorted(by_rule.items()):
            print(f"        {rule:<20} {outcome:<22} {n}")
    else:
        print("    §10 overrides: none")


def evaluate(checkpoint_path: Path, seed: int = 7, deterministic: bool = True) -> dict:
    print(RULE)
    mode = "deterministic" if deterministic else "stochastic"
    print(f"EVALUATE — {checkpoint_path.name}, corridor 4/3/2, seed {seed}, "
          f"validator ON, {mode}")
    print(RULE)

    env = PsychoFlowEnv(
        scenario_config=ScenarioConfig(lane_counts=(4, 3, 2)),
        spillover_predictor=SpilloverPredictor(),
        seed=seed,
    )
    model = MaskablePPO.load(str(checkpoint_path))
    stats = run_episode(env, ppo_picker(env, model, deterministic=deterministic))
    env.close()

    report_three_way(
        f"trained policy vs. §16's shielded-random and Tier 0 baselines "
        f"(corridor 4/3/2, seed {seed}, {mode})",
        stats, SHIELDED_RANDOM_BASELINE, TIER0_BASELINE,
    )
    # Full precision, since report_three_way's table rounds to 1 decimal —
    # needed to compare stochastic-episode averages against each other and
    # against the deterministic run without that rounding collapsing them.
    print(f"    mean_reward (full precision): {stats['mean_reward']:.6f}")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--stochastic", action="store_true",
                         help="Sample actions instead of taking the argmax.")
    args = parser.parse_args()
    evaluate(args.checkpoint, seed=args.seed, deterministic=not args.stochastic)


if __name__ == "__main__":
    main()

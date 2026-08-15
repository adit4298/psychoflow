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

import pandas as pd  # noqa: E402
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


# §16 Stage 2 checkpoint: "consistent across all 3 lane-counts." Approved
# design (see CLAUDE.md §8's Stage 2 status note): 5 representative combos —
# not all 27, for cost — times 3 seeds each, not 1, because Stage 1's own
# seed spread (seeds 1/3/7/42 on the SAME 4/3/2 topology) already showed a
# 93-124s worst-wait range from seed noise alone; a single seed per combo
# could not distinguish a real topology effect from that noise. No
# per-combo baseline exists outside 4/3/2, so this sweep is self-referential
# — comparing the trained policy's own numbers across combos, not against a
# recorded baseline.
SWEEP_COMBOS: list[tuple[int, int, int]] = [(4, 3, 2), (2, 2, 2), (4, 4, 4), (2, 4, 2), (4, 2, 4)]
SWEEP_SEEDS: list[int] = [1, 7, 42]


def run_consistency_sweep(checkpoint_path: Path) -> dict:
    """5 combos x 3 seeds = 15 deterministic episodes against ONE loaded
    model instance (loading once avoids MaskablePPO.load()'s
    set_random_seed(self.seed) call re-seeding identically on every fresh
    load — irrelevant to determinism itself since deterministic=True takes
    the argmax regardless of RNG state, but loading once is still cheaper
    than 15 separate process-level loads).
    """
    print(RULE)
    print(f"STAGE 2 CONSISTENCY SWEEP — {checkpoint_path.name}")
    print(f"combos: {SWEEP_COMBOS}")
    print(f"seeds: {SWEEP_SEEDS}  (15 runs total, deterministic)")
    print(RULE)

    model = MaskablePPO.load(str(checkpoint_path))
    rows = []
    for combo in SWEEP_COMBOS:
        for seed in SWEEP_SEEDS:
            env = PsychoFlowEnv(
                scenario_config=ScenarioConfig(lane_counts=combo, randomize_lane_counts=False),
                spillover_predictor=SpilloverPredictor(),
                seed=seed,
            )
            stats = run_episode(env, ppo_picker(env, model, deterministic=True))
            env.close()
            starved_pct = 100 * stats["starved_steps"] / max(1, stats["steps"])
            rows.append({
                "combo": combo, "seed": seed, "steps": stats["steps"],
                "mean_reward": stats["mean_reward"], "worst_wait": stats["worst_wait"],
                "starved_pct": starved_pct, "arrived": stats["arrived"],
                "terminated": stats["terminated"],
            })
            print(f"  combo={combo} seed={seed:>2}  steps={stats['steps']:>4}  "
                  f"mean_reward={stats['mean_reward']:>8.4f}  worst_wait={stats['worst_wait']:>6.1f}s  "
                  f"starved={starved_pct:>5.1f}%  terminated={stats['terminated']}")

    df = pd.DataFrame(rows)
    print(f"\n{'combo':>14} {'mean_reward':>26} {'worst_wait_s':>26} {'starved_pct':>26}")
    print(f"{'':>14} {'mean':>8}{'min':>9}{'max':>9} {'mean':>8}{'min':>9}{'max':>9} "
          f"{'mean':>8}{'min':>9}{'max':>9}")
    for combo in SWEEP_COMBOS:
        g = df[df["combo"] == combo]
        print(f"{str(combo):>14} "
              f"{g['mean_reward'].mean():>8.3f}{g['mean_reward'].min():>9.3f}{g['mean_reward'].max():>9.3f} "
              f"{g['worst_wait'].mean():>8.1f}{g['worst_wait'].min():>9.1f}{g['worst_wait'].max():>9.1f} "
              f"{g['starved_pct'].mean():>8.1f}{g['starved_pct'].min():>9.1f}{g['starved_pct'].max():>9.1f}")

    return {"rows": rows, "df": df}


# §16 Stage 3 checkpoint density-consistency check (item 6 of the approved
# Stage 3 design plan, deferred until after Burst B — that point is now).
# Same 5 combos/3 seeds as the Stage 2 sweep, times 3 pinned density levels.
# Base veh/hour rates are read from ScenarioConfig()'s own dataclass
# defaults at call time, not hardcoded here, so this sweep can't silently
# drift from the env's actual defaults if they ever change.
SWEEP_DENSITY_LEVELS: list[float] = [0.7, 1.0, 1.3]


def run_density_sweep(checkpoint_path: Path) -> dict:
    """5 combos x 3 seeds x 3 density levels = 45 deterministic episodes
    against ONE loaded model instance (see run_consistency_sweep's
    docstring for why loading once is done regardless).

    Density is pinned deterministically per level by setting
    randomize_density=False and scaling ScenarioConfig's own
    corridor_veh_per_hour/cross_veh_per_hour base rates directly —
    write_route_file() only draws a random per-flow multiplier when
    randomize_density=True (scenario_generator.py), so this reuses that
    existing off-switch rather than needing any change to the route
    generator itself.
    """
    base = ScenarioConfig()
    base_corridor_vph = base.corridor_veh_per_hour
    base_cross_vph = base.cross_veh_per_hour

    print(RULE)
    print(f"STAGE 3 DENSITY-CONSISTENCY SWEEP — {checkpoint_path.name}")
    print(f"combos: {SWEEP_COMBOS}")
    print(f"seeds: {SWEEP_SEEDS}")
    print(f"density levels: {SWEEP_DENSITY_LEVELS}  "
          f"(base corridor_veh_per_hour={base_corridor_vph}, cross_veh_per_hour={base_cross_vph}, "
          f"read from ScenarioConfig() defaults)")
    print(f"{len(SWEEP_COMBOS) * len(SWEEP_SEEDS) * len(SWEEP_DENSITY_LEVELS)} runs total, deterministic")
    print(RULE)

    model = MaskablePPO.load(str(checkpoint_path))
    rows = []
    for combo in SWEEP_COMBOS:
        for level in SWEEP_DENSITY_LEVELS:
            for seed in SWEEP_SEEDS:
                env = PsychoFlowEnv(
                    scenario_config=ScenarioConfig(
                        lane_counts=combo,
                        randomize_lane_counts=False,
                        randomize_density=False,
                        corridor_veh_per_hour=base_corridor_vph * level,
                        cross_veh_per_hour=base_cross_vph * level,
                    ),
                    spillover_predictor=SpilloverPredictor(),
                    seed=seed,
                )
                stats = run_episode(env, ppo_picker(env, model, deterministic=True))
                env.close()
                starved_pct = 100 * stats["starved_steps"] / max(1, stats["steps"])
                rows.append({
                    "combo": combo, "density_level": level, "seed": seed,
                    "steps": stats["steps"], "mean_reward": stats["mean_reward"],
                    "worst_wait": stats["worst_wait"], "starved_pct": starved_pct,
                    "arrived": stats["arrived"], "terminated": stats["terminated"],
                })
                print(f"  combo={combo} density={level:.1f}x seed={seed:>2}  steps={stats['steps']:>4}  "
                      f"mean_reward={stats['mean_reward']:>8.4f}  worst_wait={stats['worst_wait']:>6.1f}s  "
                      f"starved={starved_pct:>5.1f}%  terminated={stats['terminated']}")

    df = pd.DataFrame(rows)
    print(f"\n{'combo':>14} {'density':>8} {'mean_reward':>26} {'worst_wait_s':>26} {'starved_pct':>26}")
    print(f"{'':>14} {'':>8} {'mean':>8}{'min':>9}{'max':>9} {'mean':>8}{'min':>9}{'max':>9} "
          f"{'mean':>8}{'min':>9}{'max':>9}")
    for combo in SWEEP_COMBOS:
        for level in SWEEP_DENSITY_LEVELS:
            g = df[(df["combo"] == combo) & (df["density_level"] == level)]
            print(f"{str(combo):>14} {level:>7.1f}x "
                  f"{g['mean_reward'].mean():>8.3f}{g['mean_reward'].min():>9.3f}{g['mean_reward'].max():>9.3f} "
                  f"{g['worst_wait'].mean():>8.1f}{g['worst_wait'].min():>9.1f}{g['worst_wait'].max():>9.1f} "
                  f"{g['starved_pct'].mean():>8.1f}{g['starved_pct'].min():>9.1f}{g['starved_pct'].max():>9.1f}")

    return {"rows": rows, "df": df}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--stochastic", action="store_true",
                         help="Sample actions instead of taking the argmax.")
    parser.add_argument("--sweep", action="store_true",
                         help="Run the §16 Stage 2 consistency sweep (5 combos x 3 seeds) "
                              "instead of the single-seed evaluation.")
    parser.add_argument("--density-sweep", action="store_true",
                         help="Run the §16 Stage 3 density-consistency sweep "
                              "(5 combos x 3 seeds x 3 density levels = 45 runs).")
    args = parser.parse_args()
    if args.density_sweep:
        run_density_sweep(args.checkpoint)
    elif args.sweep:
        run_consistency_sweep(args.checkpoint)
    else:
        evaluate(args.checkpoint, seed=args.seed, deterministic=not args.stochastic)


if __name__ == "__main__":
    main()

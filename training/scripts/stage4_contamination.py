"""Does the STAGES[4]-config eval draw scenarios Stage 4 TRAINED on?

The 2026-08-18 entry established that every training burst restarts its
scenario sequence from `seed=7`, and warned: "any future evaluation that
matches the training config ... silently evaluates on scenarios the policy
trained on, and reports memorization as generalization. Nothing raises."

`evaluate_stage.py --j1-recheck` and `checkpoint_bakeoff.py` are safe: they
pin `randomize_density=False` / `spawn_emergencies=False`, which no training
episode has. But `phase0_baselines.py` — the harness behind Stage 4's headline
0.885 proposal quality — runs `STAGES[4]`, the FULL training config, over
seeds {1, 7, 42}. Training used seed 7.

This script checks the overlap WITHOUT starting SUMO. `_draw_scenario()` is a
pure function of `env._rng` (it calls `_ensure_corridor` and `write_route_file`,
neither of which touches traci), and `reset(seed=s)` does exactly
`self._rng = random.Random(s)` before calling it. So setting the rng by hand and
calling `_draw_scenario()` reproduces precisely what episode 1 of a
`reset(seed=s)` eval would draw, at zero simulation cost.

Compared against the committed `stage4/monitor*.csv`, which logs
`lane_counts`, both density multipliers, and the ambulance route/timing per
training episode.
"""

from __future__ import annotations

import csv
import random
import sys
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
assert (REPO_ROOT / "env" / "psychoflow_env.py").is_file(), f"bad root {REPO_ROOT}"
sys.path.insert(0, str(REPO_ROOT))

warnings.filterwarnings("ignore")  # the spillover_predictor=None tripwire

from env.psychoflow_env import PsychoFlowEnv  # noqa: E402
from training.curriculum import STAGES  # noqa: E402

STAGE4_DIR = REPO_ROOT / "training" / "checkpoints" / "stage4"
EVAL_SEEDS = [1, 7, 42]          # phase0_baselines.SEEDS
TRAIN_SEED = 7                   # train.py DEFAULT_SEED
N_DRAW = 12                      # eval draws only episode 1; more for context
TOL = 1e-9


def training_scenarios(path: Path, limit: int) -> list[dict]:
    rows = []
    with path.open() as fh:
        lines = [ln for ln in fh if not ln.startswith("#")]
    for row in csv.DictReader(lines):
        rows.append({
            "lane_counts": row["lane_counts"],
            "dc": float(row["density_mult_corridor"]),
            "dx": float(row["density_mult_cross"]),
            "route": row["emergency_route"],
            "depart": float(row["emergency_depart_s"]),
        })
        if len(rows) >= limit:
            break
    return rows


def eval_draws(seed: int, n: int) -> list[dict]:
    """Reproduce the scenarios `reset(seed=seed)` would draw, without SUMO."""
    env = PsychoFlowEnv(scenario_config=STAGES[4], seed=seed)
    env._rng = random.Random(seed)          # exactly what reset(seed=) does
    out = []
    for _ in range(n):
        env._draw_scenario()
        out.append({
            "lane_counts": str(tuple(env._lane_counts)),
            "dc": env._density_mult["corridor_mean"],
            "dx": env._density_mult["cross_mean"],
            "route": env._emergency_route,
            "depart": env._emergency_depart_s,
        })
    return out


def same(a: dict, b: dict) -> bool:
    return (a["lane_counts"] == b["lane_counts"]
            and abs(a["dc"] - b["dc"]) < TOL and abs(a["dx"] - b["dx"]) < TOL
            and a["route"] == b["route"] and abs(a["depart"] - b["depart"]) < TOL)


def fmt(s: dict) -> str:
    return (f"{s['lane_counts']:>10} dc={s['dc']:.6f} dx={s['dx']:.6f} "
            f"{s['route']:>6}@{s['depart']:8.1f}s")


def main() -> None:
    print("=" * 96)
    print("CONTAMINATION CHECK — does the STAGES[4] eval config replay Stage 4's")
    print("own training scenarios?  (no SUMO: _draw_scenario() is rng-pure)")
    print("=" * 96)

    train = training_scenarios(STAGE4_DIR / "monitor.csv", N_DRAW)
    print(f"\nStage 4 TRAINING draws (stage4/monitor.csv, train.py --seed {TRAIN_SEED}):")
    for i, s in enumerate(train[:5], 1):
        print(f"  ep{i:>2}  {fmt(s)}")

    # Cross-burst replay, re-verified here rather than taken on trust.
    burst_b = training_scenarios(STAGE4_DIR / "monitor_burstB.csv.monitor.csv", 5)
    n_replay = sum(1 for a, b in zip(train, burst_b) if same(a, b))
    print(f"\n  Burst A vs Burst B first 5: {n_replay}/5 identical "
          f"({'replay confirmed' if n_replay == 5 else 'NOT a clean replay'})")

    train_set = {fmt(s) for s in training_scenarios(STAGE4_DIR / "monitor.csv", 10**9)}
    train_set |= {fmt(s) for s in training_scenarios(
        STAGE4_DIR / "monitor_burstB.csv.monitor.csv", 10**9)}
    print(f"  distinct training scenarios across both bursts: {len(train_set)}")

    print(f"\nEVAL draws — phase0_baselines.py runs STAGES[4] with reset(seed=s),")
    print(f"and reports only EPISODE 1 per seed (the episode ends the run):")
    verdict = {}
    for seed in EVAL_SEEDS:
        draws = eval_draws(seed, 1)
        ep1 = draws[0]
        hit = fmt(ep1) in train_set
        verdict[seed] = hit
        print(f"\n  seed {seed:>2} ep1  {fmt(ep1)}")
        print(f"           -> in Stage 4's training set: "
              f"{'*** YES — CONTAMINATED ***' if hit else 'no (clean)'}")
        if hit:
            for i, s in enumerate(train, 1):
                if same(s, ep1):
                    print(f"           -> exact match: training episode {i}")
                    break

    print("\n" + "=" * 96)
    n_bad = sum(verdict.values())
    print(f"VERDICT: {n_bad} of {len(EVAL_SEEDS)} eval seeds draw a scenario Stage 4 "
          f"trained on.")
    for seed, hit in verdict.items():
        print(f"   seed {seed:>2}: {'CONTAMINATED' if hit else 'clean'}")
    if n_bad:
        print("\nConsequence: any pooled figure over these seeds mixes held-out")
        print("performance with performance on a memorised scenario. Seeds that")
        print("collide with the training draw must be excluded or reported apart.")
    print("=" * 96)


if __name__ == "__main__":
    # Tier 1 SUMO beacon (sim/sumo_activity.py): refuse to launch
    # concurrent SUMO while a training run or the backend is live.
    from sim.sumo_activity import require_free
    require_free('stage 4 contamination check')
    main()

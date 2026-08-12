"""PPO training entry point (§16, Phase 6). Single-agent, Stages 1-4 only.

Runs ONE stage for a bounded number of timesteps per invocation, then stops
— no auto-advance to the next stage and no auto-continuation past the
requested budget within a stage. Stage-to-stage and burst-to-burst
progression is a deliberate, separate --resume invocation gated on a human
reading the §16 checkpoint for the burst just finished.

Policy: MaskablePPO with SB3's default MlpPolicy (flattens the (3, 191) Box
observation automatically). The custom graph-attention feature extractor
(§9.5) is Stage 5/MARL scope, not built here. PsychoFlowEnv already exposes
an `action_masks()` method under the exact name sb3-contrib looks for
(EXPECTED_METHOD_NAME, verified via
sb3_contrib.common.maskable.utils.is_masking_supported), reachable through
Monitor/DummyVecEnv's get_wrapper_attr/has_attr — no ActionMasker wrapper
needed.

Usage:
    python -m training.train --stage 1 --timesteps 10000
        (Burst A: fresh model, monitor.csv)
    python -m training.train --stage 1 --timesteps 40000 \\
        --resume training/checkpoints/stage1/psychoflow_stage1_10000_steps.zip \\
        --monitor-name monitor_burstB.csv
        (Burst B: resumes to 50000 total; see reset_num_timesteps note below)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sb3_contrib import MaskablePPO
from stable_baselines3.common.callbacks import CheckpointCallback
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv

from env.psychoflow_env import PsychoFlowEnv
from prediction.spillover import SpilloverPredictor
from training.curriculum import STAGES

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKPOINTS_ROOT = REPO_ROOT / "training" / "checkpoints"

# Seed 7 matches every measured baseline this project has recorded
# (CLAUDE.md §8's Checkpoint-1 table, run_tier0_episode.py's B1/B4) so a
# trained policy's eval numbers land next to those rows without re-deriving
# a baseline under a different seed.
DEFAULT_SEED = 7

DEFAULT_CHECKPOINT_FREQ = 5_000


def build_env(stage: int, seed: int, monitor_path: Path) -> DummyVecEnv:
    raw_env = PsychoFlowEnv(
        scenario_config=STAGES[stage],
        spillover_predictor=SpilloverPredictor(),
        seed=seed,
    )

    # Phase 6 prerequisite (CLAUDE.md §3): psychoflow_env.py itself only
    # warns on a None predictor (smoke tests / random-action rollouts / unit
    # tests are legitimate without one) — training must hard-fail instead.
    # As constructed above this can never actually be None (no debug/eval
    # path drops the predictor), so this is a regression-guard against a
    # future refactor, not a live check on this code path today. Kept
    # anyway because CLAUDE.md §3 names it as a required artifact of
    # train.py specifically, independent of whether today's path can trip it.
    assert raw_env.spillover_predictor is not None, (
        "Phase 6 prerequisite (CLAUDE.md §3): spillover_predictor must be "
        "non-None before model.learn(). PsychoFlowEnv only warns; training "
        "must hard-fail."
    )

    monitor_path.parent.mkdir(parents=True, exist_ok=True)
    monitored = Monitor(raw_env, filename=str(monitor_path))
    return DummyVecEnv([lambda: monitored])


def train_stage(
    stage: int,
    timesteps: int,
    seed: int = DEFAULT_SEED,
    resume: Path | None = None,
    monitor_name: str = "monitor.csv",
    checkpoint_freq: int = DEFAULT_CHECKPOINT_FREQ,
) -> None:
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {sorted(STAGES)}, got {stage}")

    stage_dir = CHECKPOINTS_ROOT / f"stage{stage}"
    stage_dir.mkdir(parents=True, exist_ok=True)
    monitor_path = stage_dir / monitor_name

    env = build_env(stage, seed, monitor_path)

    checkpoint_callback = CheckpointCallback(
        save_freq=checkpoint_freq,
        save_path=str(stage_dir),
        name_prefix=f"psychoflow_stage{stage}",
    )

    if resume is not None:
        print(f"Resuming from {resume} (reset_num_timesteps=False)")
        model = MaskablePPO.load(str(resume), env=env)
        model.learn(
            total_timesteps=timesteps,
            reset_num_timesteps=False,
            callback=checkpoint_callback,
            progress_bar=False,
        )
    else:
        print(f"Fresh MaskablePPO, stage {stage}, seed {seed}")
        model = MaskablePPO(
            "MlpPolicy",
            env,
            seed=seed,
            verbose=1,
            tensorboard_log=str(stage_dir / "tb"),
        )
        model.learn(
            total_timesteps=timesteps,
            reset_num_timesteps=True,
            callback=checkpoint_callback,
            progress_bar=False,
        )

    # Explicit final save, distinct from CheckpointCallback's periodic ones.
    # save_freq alignment only produces round-numbered filenames when the
    # run starts at num_timesteps=0 (Burst A). A resumed run (Burst B)
    # starts from whatever the loaded checkpoint's num_timesteps was, so
    # periodic saves land on that offset (e.g. 15240, 20240, ...) and the
    # true end-of-run step count may not coincide with any of them. This
    # guarantees one unambiguous "the run really ended here" artifact.
    final_path = stage_dir / f"psychoflow_stage{stage}_{model.num_timesteps}_steps_final.zip"
    model.save(str(final_path))
    print(f"Saved final checkpoint: {final_path}")

    print(f"Done. num_timesteps={model.num_timesteps}")
    env.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=int, required=True, choices=sorted(STAGES))
    parser.add_argument("--timesteps", type=int, required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--resume", type=Path, default=None)
    parser.add_argument("--monitor-name", type=str, default="monitor.csv")
    parser.add_argument("--checkpoint-freq", type=int, default=DEFAULT_CHECKPOINT_FREQ)
    args = parser.parse_args()

    train_stage(
        stage=args.stage,
        timesteps=args.timesteps,
        seed=args.seed,
        resume=args.resume,
        monitor_name=args.monitor_name,
        checkpoint_freq=args.checkpoint_freq,
    )


if __name__ == "__main__":
    main()

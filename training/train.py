"""PPO training entry point (§16, Phase 6). Stages 1-4 single-agent, Stage 5 MARL.

Runs ONE stage for a bounded number of timesteps per invocation, then stops
— no auto-advance to the next stage and no auto-continuation past the
requested budget within a stage. Stage-to-stage and burst-to-burst
progression is a deliberate, separate --resume invocation gated on a human
reading the §16 checkpoint for the burst just finished.

Policy: Stages 1-4 use MaskablePPO with SB3's default MlpPolicy (flattens
the (3, 191) Box observation automatically). Stage 5 (MARL, §9.5) swaps in a
custom feature extractor via --coordination-mode {graph_attention,
shared_policy} — see agents/config.py. Stages 1-4's policy is deliberately
left untouched so their recorded checkpoints and baselines stay valid.
PsychoFlowEnv already exposes
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
import math
import re
from pathlib import Path

from sb3_contrib import MaskablePPO
from stable_baselines3.common.callbacks import BaseCallback, CallbackList, CheckpointCallback
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv

from agents.config import COORDINATION_MODE, VALID_MODES, get_feature_extractor
from env.psychoflow_env import PsychoFlowEnv, ScenarioConfig
from prediction.spillover import SpilloverPredictor
from sim.sumo_activity import beat as _sumo_beat, clear as _sumo_clear
from training.curriculum import STAGES


class _SumoBeaconCallback(BaseCallback):
    """Refreshes the Tier 1 SUMO beacon so it never looks stale mid-run.

    Beats on every rollout end AND periodically within a rollout: a rollout is
    2048 steps (~2 min), comfortably inside STALE_AFTER_S, but a slow or
    stalled rollout should not make a live trainer look dead to a sweep.
    """

    def __init__(self, kind: str, note: str, every_steps: int = 500):
        super().__init__()
        self._kind, self._note, self._every = kind, note, every_steps

    def _on_training_start(self) -> None:
        _sumo_beat(self._kind, self._note)

    def _on_rollout_end(self) -> None:
        _sumo_beat(self._kind, self._note)

    def _on_step(self) -> bool:
        if self.n_calls % self._every == 0:
            _sumo_beat(self._kind, self._note)
        return True


REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKPOINTS_ROOT = REPO_ROOT / "training" / "checkpoints"

# §16's Stage 5 — MARL (§9.5). Not in curriculum.py's STAGES: that module
# is explicitly scoped to the single-agent stages, and Stage 5 differs from
# Stage 4 by POLICY ARCHITECTURE, not by ScenarioConfig. It reuses Stage 4's
# full config (all randomisation axes on) so §9.5's coordination claim is
# tested under the hardest, most general conditions rather than a
# re-narrowed scenario.
MARL_STAGE = 5
MARL_SCENARIO_SOURCE_STAGE = 4

# Seed 7 matches every measured baseline this project has recorded
# (CLAUDE.md §8's Checkpoint-1 table, run_tier0_episode.py's B1/B4) so a
# trained policy's eval numbers land next to those rows without re-deriving
# a baseline under a different seed.
DEFAULT_SEED = 7

DEFAULT_CHECKPOINT_FREQ = 5_000


def preflight_timesteps(requested: int, resume_from: int = 0, n_steps: int = 2048) -> int:
    """Actual `num_timesteps` a run will END on, given `--timesteps`.

    PPO collects whole rollouts of `n_steps` and only stops on a rollout
    boundary, so it ALWAYS overshoots unless `requested` is an exact
    multiple. The overshoot is silent and has already caused one real
    error: Stage 5's `shared_policy` was launched with `--timesteps 41000`
    from 10,240 intending to land on 51,200, but 41,000 is 40 steps past
    the 40,960 (20-rollout) boundary, so it took a 21st rollout and landed
    on 53,248 — handing that mode a 3.1% budget advantage in what was
    supposed to be a budget-matched A/B. This is printed BEFORE training
    starts so the number can be checked against intent, rather than
    discovered afterwards.
    """
    rollouts = math.ceil(requested / n_steps)
    return resume_from + rollouts * n_steps


def scenario_for_stage(stage: int) -> ScenarioConfig:
    """ScenarioConfig for a stage. Stage 5 reuses Stage 4's (see MARL_STAGE)."""
    if stage == MARL_STAGE:
        return STAGES[MARL_SCENARIO_SOURCE_STAGE]
    return STAGES[stage]


def valid_stages() -> list[int]:
    return sorted(STAGES) + [MARL_STAGE]


def build_env(stage: int, seed: int, monitor_path: Path) -> DummyVecEnv:
    raw_env = PsychoFlowEnv(
        scenario_config=scenario_for_stage(stage),
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
    monitored = Monitor(
        raw_env,
        filename=str(monitor_path),
        info_keywords=(
            "lane_counts", "density_mult_corridor", "density_mult_cross",
            "emergency_route", "emergency_route_type", "emergency_depart_s",
        ),
    )
    return DummyVecEnv([lambda: monitored])


def train_stage(
    stage: int,
    timesteps: int,
    seed: int = DEFAULT_SEED,
    resume: Path | None = None,
    monitor_name: str = "monitor.csv",
    checkpoint_freq: int = DEFAULT_CHECKPOINT_FREQ,
    coordination_mode: str = COORDINATION_MODE,
    run_tag: str = "",
) -> None:
    if stage not in valid_stages():
        raise ValueError(f"stage must be one of {valid_stages()}, got {stage}")

    # Stage 5 only. Stages 1-4 keep SB3's default MlpPolicy exactly as they
    # were trained — swapping their extractor now would silently invalidate
    # every checkpoint and baseline already recorded for them.
    is_marl = stage == MARL_STAGE
    if is_marl and coordination_mode not in VALID_MODES:
        raise ValueError(f"--coordination-mode must be one of {VALID_MODES}, "
                         f"got {coordination_mode!r}")

    # Each mode gets its own directory: §9.5 expects the flag to be flipped
    # and the stage re-run, and a shared directory would let the second run
    # overwrite the first's checkpoints and monitor file.
    stage_dir = CHECKPOINTS_ROOT / (f"stage{stage}_{coordination_mode}" if is_marl
                                    else f"stage{stage}")
    # --run-tag isolates a NEW experiment from an existing one. Without it a
    # fresh --stage 5 run writes psychoflow_stage5_5000_steps.zip etc. straight
    # into stage5_graph_attention/, OVERWRITING the checkpoint series an
    # earlier run left there — which for Stage 5 is the evidence base behind
    # the §9.5 decision, the 16-point j1 curve and the Phase 0 matrix. The
    # CheckpointCallback name_prefix depends only on the stage number, so the
    # directory is the only thing separating two runs of the same stage.
    if run_tag:
        stage_dir = stage_dir.parent / f"{stage_dir.name}_{run_tag}"
    stage_dir.mkdir(parents=True, exist_ok=True)
    monitor_path = stage_dir / monitor_name

    # Pre-flight (see preflight_timesteps): print the ACTUAL end point before
    # spending any compute, so a rollout-boundary overshoot is caught here
    # rather than discovered in the results.
    resume_from = 0
    if resume is not None:
        m = re.search(r"_(\d+)_steps", Path(resume).name)
        resume_from = int(m.group(1)) if m else 0
    projected = preflight_timesteps(timesteps, resume_from)
    print(f"PRE-FLIGHT: --timesteps {timesteps} from num_timesteps={resume_from} "
          f"-> will END on num_timesteps={projected} "
          f"({math.ceil(timesteps / 2048)} rollouts x 2048"
          f"{f'; overshoot +{projected - resume_from - timesteps}' if projected - resume_from != timesteps else '; exact'})")

    env = build_env(stage, seed, monitor_path)

    checkpoint_callback = CheckpointCallback(
        save_freq=checkpoint_freq,
        save_path=str(stage_dir),
        name_prefix=f"psychoflow_stage{stage}",
    )

    # Tier 1 SUMO beacon (sim/sumo_activity.py): announce that this process is
    # driving SUMO, so a sweep harness in another session refuses to launch
    # concurrent instances instead of crashing this run with
    # FatalTraCIError: Could not connect. Advisory, self-clearing, and never
    # fatal to training — see that module's docstring.
    beacon_callback = _SumoBeaconCallback(
        kind="training",
        note=f"stage {stage} ({coordination_mode}) -> {stage_dir.name}",
    )
    callbacks = CallbackList([checkpoint_callback, beacon_callback])

    if resume is not None:
        print(f"Resuming from {resume} (reset_num_timesteps=False)")
        model = MaskablePPO.load(str(resume), env=env)

        # MEASURED, not assumed: loading a Stage 1-4 checkpoint under
        # --stage 5 does NOT raise. SB3 restores policy_kwargs from the saved
        # file, so it silently rebuilds the default FlattenExtractor
        # (features_dim=573) and would happily train it — producing a
        # checkpoint in stage5_<mode>/ labelled MARL but containing no
        # attention whatsoever. A silent wrong-architecture run is far worse
        # than a crash. Checking the LOADED extractor (rather than the resume
        # path) also catches resuming a graph_attention checkpoint under
        # --coordination-mode shared_policy, which is the same class of
        # mistake in the other direction. Nothing has trained at this point,
        # so raising here costs only the load.
        if is_marl:
            expected = get_feature_extractor(coordination_mode)
            actual = type(model.policy.features_extractor)
            if actual is not expected:
                raise ValueError(
                    f"--stage {MARL_STAGE} --coordination-mode {coordination_mode} expects a "
                    f"{expected.__name__} policy, but {resume} restored a {actual.__name__}. "
                    f"Stage 5's §9.5 extractor parameters do not exist in a Stage 1-4 "
                    f"checkpoint, and MaskablePPO.load() does NOT fail on this — it would "
                    f"silently train the wrong architecture. Start Stage 5 fresh (omit "
                    f"--resume), or resume a checkpoint trained in this same mode."
                )
        model.learn(
            total_timesteps=timesteps,
            reset_num_timesteps=False,
            callback=callbacks,
            progress_bar=False,
        )
    else:
        # Stage 5's custom extractor has a different parameter set than the
        # default MlpPolicy, so a Stage 1-4 checkpoint CANNOT be resumed into
        # it (MaskablePPO.load() requires matching shapes). Stage 5 therefore
        # always starts fresh — a property of the architecture change, not a
        # break in the curriculum-resume discipline used for Stages 2->3->4.
        policy_kwargs = None
        if is_marl:
            policy_kwargs = dict(
                features_extractor_class=get_feature_extractor(coordination_mode),
            )
            print(f"MARL stage {stage}: coordination_mode={coordination_mode}, "
                  f"extractor={policy_kwargs['features_extractor_class'].__name__}")
        print(f"Fresh MaskablePPO, stage {stage}, seed {seed}")
        model = MaskablePPO(
            "MlpPolicy",
            env,
            policy_kwargs=policy_kwargs,
            seed=seed,
            verbose=1,
            tensorboard_log=str(stage_dir / "tb"),
        )
        model.learn(
            total_timesteps=timesteps,
            reset_num_timesteps=True,
            callback=callbacks,
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

    # Release the Tier 1 SUMO beacon on a clean exit. Not in a finally block
    # on purpose: if this run dies instead, the beacon self-clears anyway
    # (sim/sumo_activity.holder() ignores a beacon whose PID is gone), so
    # there is no stale-lock failure mode to defend against here.
    _sumo_clear()
    env.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=int, required=True, choices=valid_stages())
    parser.add_argument("--timesteps", type=int, required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--resume", type=Path, default=None)
    parser.add_argument("--monitor-name", type=str, default="monitor.csv")
    parser.add_argument("--checkpoint-freq", type=int, default=DEFAULT_CHECKPOINT_FREQ)
    parser.add_argument("--coordination-mode", type=str, default=COORDINATION_MODE,
                        choices=VALID_MODES,
                        help="§9.5 MARL feature extractor. Stage 5 only; ignored "
                             "for Stages 1-4, which keep SB3's default MlpPolicy.")
    parser.add_argument("--run-tag", type=str, default="",
                        help="Suffix the checkpoint DIRECTORY, isolating a new "
                             "experiment from an existing run of the same stage. "
                             "Without it, a fresh run overwrites the earlier "
                             "run's checkpoint series (name_prefix depends only "
                             "on the stage number).")
    args = parser.parse_args()

    train_stage(
        stage=args.stage,
        timesteps=args.timesteps,
        seed=args.seed,
        resume=args.resume,
        monitor_name=args.monitor_name,
        checkpoint_freq=args.checkpoint_freq,
        coordination_mode=args.coordination_mode,
        run_tag=args.run_tag,
    )


if __name__ == "__main__":
    main()

"""§16 curriculum stage definitions — Phase 6 (PPO training, Stages 1-4).

Stages are CUMULATIVE ScenarioConfig deltas, not independent configs — each
stage's config is the previous stage's plus one new randomization axis, per
§16's "Adds" column. Stage 1 is the locked 4/3/2 corridor held fixed
(`randomize_lane_counts=False`), per the Phase 3 BUILD_LOG decision
("`reset()` is curriculum-parameterized... defaulting to §16 Stage 1 (fixed
4/3/2, fixed density, no emergencies)") — see the note under §16's stage
table in the master plan, added when that row's stale "4-way, 4-lane"
wording was corrected to match this decision.

Stage 5 (MARL) and Stage 6 (Y-merge, deprioritized per CLAUDE.md §2) are out
of scope here — this module only covers single-agent Stages 1-4.

train.py runs ONE stage per invocation and stops; there is no auto-advance
between stages. Moving from stage N to N+1 is a separate --stage invocation,
gated on that stage's §16 checkpoint passing — a human read of the actual
reward curve / evaluation numbers, not a script decision (§16: "stop and
debug on any red flag, don't let a bad run continue unattended").
"""

from __future__ import annotations

from env.psychoflow_env import ScenarioConfig

# §16 states 50k-100k as a STARTING POINT per stage, not a fixed value. This
# is each stage's full budget once its early checkpoint (below) has been
# reviewed and passed — not a number to run blind in one uninterrupted call.
STAGE_TARGET_TIMESTEPS: dict[int, int] = {
    1: 50_000,
    2: 50_000,
    3: 50_000,
    4: 50_000,
}

# §16: "After Stage 1, ~10k steps: reward trending up (plot it)". Every
# stage's FIRST invocation runs only to this point, stops, and is reviewed
# before any further budget for that stage is spent.
EARLY_CHECKPOINT_TIMESTEPS = 10_000

STAGES: dict[int, ScenarioConfig] = {
    1: ScenarioConfig(
        lane_counts=(4, 3, 2),
        randomize_lane_counts=False,
        randomize_density=False,
        spawn_emergencies=False,
    ),
    2: ScenarioConfig(
        lane_counts=(4, 3, 2),
        randomize_lane_counts=True,
        randomize_density=False,
        spawn_emergencies=False,
    ),
    3: ScenarioConfig(
        lane_counts=(4, 3, 2),
        randomize_lane_counts=True,
        randomize_density=True,
        spawn_emergencies=False,
    ),
    4: ScenarioConfig(
        lane_counts=(4, 3, 2),
        randomize_lane_counts=True,
        randomize_density=True,
        spawn_emergencies=True,
    ),
}

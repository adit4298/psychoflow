"""Phase 0 — emergency-priority INSTRUMENTATION ONLY (measurement, no changes).

Reads checkpoints and runs episodes. Touches NO repo module: reward.py,
curriculum.py and psychoflow_env.py are all imported read-only and unmodified.
The proposed action is captured in this script's own picker, before env.step(),
so nothing inside the env needs to change to observe it.

Answers two questions:

PART 1 — ambulance-visible steps per episode at the CURRENT n_emergencies=1
frequency. This is the number that determines what fraction of episode reward
a per-visible-step penalty would represent, and therefore what
`w_emergency_proposal` could safely be. Not proposing a weight here.

PART 2 — THE DISCRIMINATING TEST. Tracks whether the policy's PROPOSED action
(pre-validator) serves an ambulance, across the whole training trajectory via
the saved checkpoint series. Two theories predict different shapes:
  * sparsity theory        -> proposal quality FLAT / near-zero-signal
  * wrong-way-gradient     -> proposal quality DEGRADES with training steps
If neither shape is clear the honest answer is "ambiguous" and this script's
report says so rather than letting the more interesting theory win.

FAIRNESS OF THE METRIC (§0.3 — a metric must not be trivially satisfiable, and
must not trivially FAIL either). A junction-step where an ambulance is present
is classified three ways, using the action mask the policy actually faced:
  served              proposed slot would green an ambulance lane
  blocked_avoidable   it would not, but SOME mask-valid slot would have
  blocked_unavoidable NO mask-valid slot serves it (min-green lock or a
                      committed mid-yellow) — the policy had no choice, so
                      counting this against it would measure the mask, not
                      the policy.
Proposal quality = served / (served + blocked_avoidable).
"""

from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

REPO_ROOT = Path(r"C:\Users\aditp\OneDrive\Documents\GitHub\Test")
assert (REPO_ROOT / "env" / "psychoflow_env.py").is_file(), f"bad root {REPO_ROOT}"
sys.path.insert(0, str(REPO_ROOT))

from sb3_contrib import MaskablePPO  # noqa: E402

from env.obs_action_spec import MAX_PHASES, build_observation  # noqa: E402
from env.psychoflow_env import PsychoFlowEnv  # noqa: E402
from prediction.spillover import SpilloverPredictor  # noqa: E402
from training.curriculum import STAGES  # noqa: E402
from twin.digital_twin import CORRIDOR_JUNCTIONS  # noqa: E402

CKPT_DIR = REPO_ROOT / "training" / "checkpoints" / "stage5_graph_attention"
OUT = REPO_ROOT / "training" / "checkpoints" / "_sweeps" / "phase0_emergency.json"

# Spread across the whole trajectory; trained from scratch with
# spawn_emergencies=True from step 0, so this is a complete history.
TRAJECTORY = [5000, 10240, 20240, 30240, 40240, 51624,
              61624, 71624, 81624, 91624, 102824, 110824]
TRAJ_SEEDS = [1, 7, 42]          # fixed across checkpoints -> paired comparison
PART1_SEEDS = list(range(1, 13))  # 12 distinct scenarios for the distribution


def ckpt_path(step: int) -> Path:
    for name in (f"psychoflow_stage5_{step}_steps_final.zip",
                 f"psychoflow_stage5_{step}_steps.zip"):
        p = CKPT_DIR / name
        if p.is_file():
            return p
    raise FileNotFoundError(step)


def run_episode(env, model, seed):
    """One episode. Returns per-episode emergency instrumentation."""
    env.reset(seed=seed)
    served_map = env.phase_served_lanes()

    steps = 0
    amb_steps = 0            # steps with an ambulance visible ANYWHERE
    jstep_served = 0
    jstep_blocked_avoidable = 0
    jstep_blocked_unavoidable = 0
    amb_junction_steps = 0
    first_contact = None     # (served?) on the FIRST step an ambulance appears
    overrides = 0

    while True:
        snap = env.twin.snapshot
        runtime = env._runtime()
        masks = env.action_masks()
        obs = build_observation(snap, runtime, env._spillover())
        action, _ = model.predict(obs, action_masks=masks, deterministic=True)
        action = [int(a) for a in action]

        seen_this_step = False
        for j, jid in enumerate(CORRIDOR_JUNCTIONS):
            lanes = snap["junctions"][jid]["lanes"]
            amb_lanes = {lid for lid, r in lanes.items()
                         if r["type_composition"].get("ambulance", 0) > 0}
            if not amb_lanes:
                continue
            seen_this_step = True
            amb_junction_steps += 1

            valid = [s for s in range(MAX_PHASES) if masks[j * MAX_PHASES + s]]
            jserved = served_map.get(jid, {})
            proposed_ok = bool(amb_lanes & set(jserved.get(action[j], frozenset())))
            any_valid_ok = any(
                amb_lanes & set(jserved.get(s, frozenset())) for s in valid)

            if proposed_ok:
                jstep_served += 1
            elif any_valid_ok:
                jstep_blocked_avoidable += 1
            else:
                jstep_blocked_unavoidable += 1

            if first_contact is None:
                first_contact = bool(proposed_ok)

        if seen_this_step:
            amb_steps += 1

        _, _, term, trunc, info = env.step(action)
        steps += 1
        overrides += len(info["safety_overrides"])
        if term or trunc:
            break

    decidable = jstep_served + jstep_blocked_avoidable
    return {
        "seed": seed,
        "steps": steps,
        "amb_visible_steps": amb_steps,
        "amb_visible_pct": 100.0 * amb_steps / max(1, steps),
        "amb_junction_steps": amb_junction_steps,
        "served": jstep_served,
        "blocked_avoidable": jstep_blocked_avoidable,
        "blocked_unavoidable": jstep_blocked_unavoidable,
        "proposal_quality": (jstep_served / decidable) if decidable else None,
        "decidable_junction_steps": decidable,
        "first_contact_served": first_contact,
        "overrides": overrides,
    }


def main():
    cfg = STAGES[4]  # spawn_emergencies=True, n_emergencies=1 — realistic freq
    print(f"config: STAGES[4]  spawn_emergencies={cfg.spawn_emergencies} "
          f"n_emergencies={cfg.n_emergencies} window={cfg.emergency_window_s}")
    print("MEASUREMENT ONLY — no repo module modified\n", flush=True)

    results = {"part1": [], "part2": {}}

    # ---------------- PART 2 first (the discriminating test) --------------
    print("=== PART 2: proposal quality across training trajectory ===",
          flush=True)
    print(f"{'steps':>8} {'served':>7} {'avoid':>7} {'unavoid':>8} "
          f"{'quality':>8} {'ambJstep':>9} {'ovr':>5}", flush=True)
    for step in TRAJECTORY:
        try:
            p = ckpt_path(step)
        except FileNotFoundError:
            print(f"{step:>8}  (missing, skipped)", flush=True)
            continue
        model = MaskablePPO.load(str(p))
        env = PsychoFlowEnv(scenario_config=cfg,
                            spillover_predictor=SpilloverPredictor(), seed=7)
        rows = []
        try:
            for sd in TRAJ_SEEDS:
                rows.append(run_episode(env, model, sd))
        finally:
            env.close()
        results["part2"][str(step)] = rows
        s = sum(r["served"] for r in rows)
        a = sum(r["blocked_avoidable"] for r in rows)
        u = sum(r["blocked_unavoidable"] for r in rows)
        q = s / (s + a) if (s + a) else float("nan")
        print(f"{step:>8} {s:>7} {a:>7} {u:>8} {q:>8.3f} "
              f"{sum(r['amb_junction_steps'] for r in rows):>9} "
              f"{sum(r['overrides'] for r in rows):>5}", flush=True)

    # ---------------- PART 1: visible-step distribution -------------------
    print("\n=== PART 1: ambulance-visible steps/episode, n_emergencies=1 ===",
          flush=True)
    latest = ckpt_path(TRAJECTORY[-1])
    print(f"checkpoint: {latest.name}", flush=True)
    model = MaskablePPO.load(str(latest))
    env = PsychoFlowEnv(scenario_config=cfg,
                        spillover_predictor=SpilloverPredictor(), seed=7)
    try:
        for sd in PART1_SEEDS:
            r = run_episode(env, model, sd)
            results["part1"].append(r)
            print(f"  seed {sd:>3}: visible_steps={r['amb_visible_steps']:>4} "
                  f"({r['amb_visible_pct']:>5.2f}% of {r['steps']}) "
                  f"amb_junction_steps={r['amb_junction_steps']:>4} "
                  f"served={r['served']:>3} avoid={r['blocked_avoidable']:>3} "
                  f"unavoid={r['blocked_unavoidable']:>3}", flush=True)
    finally:
        env.close()

    v = [r["amb_visible_steps"] for r in results["part1"]]
    jt = [r["amb_junction_steps"] for r in results["part1"]]
    print(f"\n  visible_steps/episode: mean={st.fmean(v):.2f} "
          f"median={st.median(v)} min={min(v)} max={max(v)} "
          f"stdev={st.stdev(v) if len(v) > 1 else 0:.2f}")
    print(f"  amb_junction_steps/ep: mean={st.fmean(jt):.2f} "
          f"min={min(jt)} max={max(jt)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()

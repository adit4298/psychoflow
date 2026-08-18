"""Phase 0 baselines — generalizes phase0_emergency.py's proposal-quality metric
to ARBITRARY checkpoints and to a random control. MEASUREMENT ONLY.

phase0_emergency.py could only target the stage5_graph_attention checkpoint
series (hardcoded dir + `psychoflow_stage5_{step}_steps` name pattern) and had
no control condition, so its headline "proposal quality ~0.80" had nothing to
be compared against. Per master plan §0.3, a metric with no reference point
cannot be read as good or bad. This module supplies both.

The classification rules are COPIED EXACTLY from phase0_emergency.py, not
reinterpreted. `--selfcheck` re-runs a recorded row from
_sweeps/phase0_emergency.json through this code and asserts an exact match,
because a rewritten harness that silently disagrees with the original would
invalidate every comparison drawn against the recorded matrix.

  served              proposed slot would green an ambulance lane
  blocked_avoidable   it would not, but SOME mask-valid slot would have
  blocked_unavoidable NO mask-valid slot serves it (min-green lock or a
                      committed mid-yellow) — the policy had no choice
  proposal quality = served / (served + blocked_avoidable)

TWO ADDITIONS BEYOND THE ORIGINAL, both flagged as such:

1. ANALYTIC CHANCE BASELINE (`chance_quality`). At every decidable ambulance
   junction-step, the probability a uniform-random pick over the mask-valid
   slots would have served the ambulance is exactly
   `n_valid_slots_that_serve / n_valid_slots`. Averaging that over the same
   decidable steps gives the EXPECTED proposal quality of a random picker at
   the exact states the policy actually visited. This is a strictly tighter
   control than a separate random rollout: a random rollout diverges into a
   different trajectory (different congestion, different ambulance exposure),
   so it answers "how does a random agent do in its own world", whereas this
   answers "how much credit does this policy deserve at the decisions it
   actually faced". Both are reported; they measure different things.

2. OVERRIDE SPLIT BY RULE. The original counted `len(info["safety_overrides"])`
   which lumps §10's emergency_override together with starvation_ceiling. For
   an emergency-priority question only the emergency ones are on topic, and
   they are typically a small minority. Split here via `record.rule`.

Touches no repo module: env/reward/validator are all imported read-only.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics as st
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
assert (REPO_ROOT / "env" / "psychoflow_env.py").is_file(), f"bad root {REPO_ROOT}"
sys.path.insert(0, str(REPO_ROOT))

from sb3_contrib import MaskablePPO  # noqa: E402

from env.obs_action_spec import MAX_PHASES, build_observation  # noqa: E402
from env.psychoflow_env import PsychoFlowEnv  # noqa: E402
from prediction.spillover import SpilloverPredictor  # noqa: E402
from safety.validator import RULE_EMERGENCY, RULE_STARVATION  # noqa: E402
from training.curriculum import STAGES  # noqa: E402
from twin.digital_twin import CORRIDOR_JUNCTIONS  # noqa: E402

SWEEPS = REPO_ROOT / "training" / "checkpoints" / "_sweeps"
OUT = SWEEPS / "phase0_baselines.json"
SEEDS = [1, 7, 42]  # identical to phase0_emergency.py's TRAJ_SEEDS


# --------------------------------------------------------------------------
# pickers
# --------------------------------------------------------------------------
def ppo_picker(model):
    def pick(obs, masks):
        action, _ = model.predict(obs, action_masks=masks, deterministic=True)
        return [int(a) for a in action]
    return pick


def random_picker(rng):
    """Uniform over MASK-VALID slots per junction (an invalid pick would raise)."""
    def pick(obs, masks):
        out = []
        for j in range(len(CORRIDOR_JUNCTIONS)):
            valid = [s for s in range(MAX_PHASES) if masks[j * MAX_PHASES + s]]
            out.append(rng.choice(valid))
        return out
    return pick


# --------------------------------------------------------------------------
# episode
# --------------------------------------------------------------------------
def run_episode(env, pick, seed):
    env.reset(seed=seed)
    served_map = env.phase_served_lanes()

    steps = amb_steps = amb_junction_steps = 0
    jserved = javoid = junavoid = 0
    chance_num, chance_den = 0.0, 0
    first_contact = None
    ovr_emergency = ovr_starvation = ovr_total = 0

    while True:
        snap = env.twin.snapshot
        runtime = env._runtime()
        masks = env.action_masks()
        obs = build_observation(snap, runtime, env._spillover())
        action = pick(obs, masks)

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
            jmap = served_map.get(jid, {})
            serving_valid = [s for s in valid
                             if amb_lanes & set(jmap.get(s, frozenset()))]
            proposed_ok = bool(amb_lanes & set(jmap.get(action[j], frozenset())))
            any_valid_ok = bool(serving_valid)

            if proposed_ok:
                jserved += 1
            elif any_valid_ok:
                javoid += 1
            else:
                junavoid += 1

            # ADDITION 1: exact expected hit-rate of a uniform-random pick at
            # THIS state. Only over decidable steps, matching the metric's own
            # denominator.
            if any_valid_ok and valid:
                chance_num += len(serving_valid) / len(valid)
                chance_den += 1

            if first_contact is None:
                first_contact = bool(proposed_ok)

        if seen_this_step:
            amb_steps += 1

        _, _, term, trunc, info = env.step(action)
        steps += 1
        # ADDITION 2: split overrides by rule instead of one lumped count.
        # info carries OverrideRecord.to_dict(), not the dataclass itself.
        for rec in info["safety_overrides"]:
            ovr_total += 1
            if rec["rule"] == RULE_EMERGENCY:
                ovr_emergency += 1
            elif rec["rule"] == RULE_STARVATION:
                ovr_starvation += 1
        if term or trunc:
            break

    decidable = jserved + javoid
    return {
        "seed": seed,
        "steps": steps,
        "amb_visible_steps": amb_steps,
        "amb_visible_pct": 100.0 * amb_steps / max(1, steps),
        "amb_junction_steps": amb_junction_steps,
        "served": jserved,
        "blocked_avoidable": javoid,
        "blocked_unavoidable": junavoid,
        "proposal_quality": (jserved / decidable) if decidable else None,
        "chance_quality": (chance_num / chance_den) if chance_den else None,
        "decidable_junction_steps": decidable,
        "first_contact_served": first_contact,
        "overrides": ovr_total,
        "overrides_emergency": ovr_emergency,
        "overrides_starvation": ovr_starvation,
        "terminated_or_truncated_steps": steps,
    }


def run_condition(label, pick_factory, seeds=SEEDS):
    cfg = STAGES[4]
    rows = []
    print(f"\n--- {label} ---", flush=True)
    print(f"{'seed':>5} {'steps':>6} {'ambJ':>5} {'srv':>4} {'avd':>4} "
          f"{'unav':>5} {'quality':>8} {'chance':>7} {'ovrE':>5} {'ovrS':>5}",
          flush=True)
    for sd in seeds:
        env = PsychoFlowEnv(scenario_config=cfg,
                            spillover_predictor=SpilloverPredictor(), seed=7)
        try:
            r = run_episode(env, pick_factory(), sd)
        finally:
            env.close()
        rows.append(r)
        q = "  n/a" if r["proposal_quality"] is None else f"{r['proposal_quality']:>8.3f}"
        c = "  n/a" if r["chance_quality"] is None else f"{r['chance_quality']:>7.3f}"
        print(f"{sd:>5} {r['steps']:>6} {r['amb_junction_steps']:>5} "
              f"{r['served']:>4} {r['blocked_avoidable']:>4} "
              f"{r['blocked_unavoidable']:>5} {q} {c} "
              f"{r['overrides_emergency']:>5} {r['overrides_starvation']:>5}",
              flush=True)

    s = sum(r["served"] for r in rows)
    a = sum(r["blocked_avoidable"] for r in rows)
    pooled = s / (s + a) if (s + a) else float("nan")
    qs = [r["proposal_quality"] for r in rows if r["proposal_quality"] is not None]
    cs = [r["chance_quality"] for r in rows if r["chance_quality"] is not None]
    print(f"  POOLED quality={pooled:.3f}  mean={st.fmean(qs):.3f} "
          f"(std {st.stdev(qs) if len(qs) > 1 else 0:.3f})  "
          f"CHANCE mean={st.fmean(cs):.3f}  decidable={s + a}  "
          f"ovrE={sum(r['overrides_emergency'] for r in rows)}  "
          f"ovrS={sum(r['overrides_starvation'] for r in rows)}", flush=True)
    return {"label": label, "rows": rows, "pooled": pooled,
            "mean_quality": st.fmean(qs), "mean_chance": st.fmean(cs)}


# --------------------------------------------------------------------------
# self-check — the harness must reproduce the recorded matrix exactly
# --------------------------------------------------------------------------
def selfcheck():
    """Re-run one recorded (checkpoint, seed) and assert an exact match.

    CLAUDE.md §8: 'a verification run that passes while proving nothing is the
    failure mode to watch in this repo'. A rewritten harness agreeing only
    approximately with the original would make every comparison below
    meaningless while still printing plausible numbers.
    """
    rec = json.loads((SWEEPS / "phase0_emergency.json").read_text())
    ck = REPO_ROOT / "training/checkpoints/stage5_graph_attention"
    step, seed = "110824", 1
    want = next(r for r in rec["part2"][step] if r["seed"] == seed)
    model = MaskablePPO.load(str(ck / f"psychoflow_stage5_{step}_steps.zip"))
    env = PsychoFlowEnv(scenario_config=STAGES[4],
                        spillover_predictor=SpilloverPredictor(), seed=7)
    try:
        got = run_episode(env, ppo_picker(model), seed)
    finally:
        env.close()
    keys = ["steps", "amb_visible_steps", "amb_junction_steps", "served",
            "blocked_avoidable", "blocked_unavoidable", "proposal_quality",
            "overrides"]
    print(f"SELFCHECK vs recorded phase0_emergency.json[part2][{step}] seed={seed}")
    ok = True
    for k in keys:
        match = want[k] == got[k]
        ok &= match
        print(f"  {k:<24} recorded={want[k]!r:<22} got={got[k]!r:<22} "
              f"{'OK' if match else 'MISMATCH'}")
    print("SELFCHECK:", "PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--checkpoint", action="append", default=[],
                    metavar="LABEL=PATH", help="repeatable")
    ap.add_argument("--random", action="store_true",
                    help="add uniform-random-over-mask-valid control")
    ap.add_argument("--random-reps", type=int, default=3,
                    help="independent random reps per seed (default 3)")
    args = ap.parse_args()

    if args.selfcheck:
        sys.exit(0 if selfcheck() else 1)

    results = []
    for spec in args.checkpoint:
        label, _, path = spec.partition("=")
        model = MaskablePPO.load(path)
        results.append(run_condition(label, lambda m=model: ppo_picker(m)))

    if args.random:
        # Independent reps: a single random rollout is one draw from a wide
        # distribution, so reporting n=1 would be indistinguishable from noise.
        for rep in range(args.random_reps):
            seed_rng = random.Random(1000 + rep)
            results.append(run_condition(
                f"RANDOM (mask-valid uniform) rep{rep + 1}",
                lambda r=seed_rng: random_picker(r)))

    SWEEPS.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()

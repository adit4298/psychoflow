"""Per-term reward decomposition across a checkpoint window. MEASUREMENT ONLY.

Replays the EXACT 12 (combo, seed) pairs that `evaluate_stage.py --j1-recheck`
uses, capturing `info["reward_breakdown"]` every step and summing each §9.4
term separately. Aggregate reward hides which term moved; this shows it.

Separability note: these are pinned deterministic episodes on the flagged
bottleneck combos ((3,2,3)/(3,2,4) + j1=4 controls), density pinned at nominal,
no emergency spawn — the same construction the j1 numbers come from. So the
decomposition is attributable to those scenarios specifically, NOT averaged in
with general randomized training episodes. No approximation is involved.

Touches no repo module; env/reward/validator imported read-only.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
assert (REPO_ROOT / "env" / "psychoflow_env.py").is_file(), f"bad root {REPO_ROOT}"
sys.path.insert(0, str(REPO_ROOT))

from sb3_contrib import MaskablePPO  # noqa: E402

from env.obs_action_spec import build_observation  # noqa: E402
from env.psychoflow_env import PsychoFlowEnv, ScenarioConfig  # noqa: E402
from prediction.spillover import SpilloverPredictor  # noqa: E402
from safety.validator import RULE_EMERGENCY, RULE_STARVATION  # noqa: E402

SWEEPS = REPO_ROOT / "training" / "checkpoints" / "_sweeps"
CKPT_DIR = REPO_ROOT / "training" / "checkpoints" / "stage5_graph_attention"

# identical to evaluate_stage.py's J1_RECHECK_* plan
PLAN = ([((3, 2, 3), s) for s in (1, 3, 7, 42)]
        + [((3, 2, 4), s) for s in (1, 3, 7, 42)]
        + [((4, 2, 3), s) for s in (1, 3)]
        + [((4, 2, 4), s) for s in (1, 3)])


def ckpt_path(step: int) -> Path:
    for name in (f"psychoflow_stage5_{step}_steps_final.zip",
                 f"psychoflow_stage5_{step}_steps.zip"):
        p = CKPT_DIR / name
        if p.is_file():
            return p
    raise FileNotFoundError(step)


def replay(model, combo, seed):
    env = PsychoFlowEnv(
        scenario_config=ScenarioConfig(
            lane_counts=combo, randomize_lane_counts=False,
            randomize_density=False, spawn_emergencies=False),
        spillover_predictor=SpilloverPredictor(), seed=seed)
    env.reset(seed=seed)
    tot = {"total": 0.0, "starvation_penalty": 0.0, "throughput_bonus": 0.0,
           "emergency_penalty": 0.0, "switch_penalty": 0.0}
    steps = switches = arrived = starved_steps = 0
    ovr_s = ovr_e = 0
    worst = 0.0
    try:
        while True:
            snap = env.twin.snapshot
            masks = env.action_masks()
            obs = build_observation(snap, env._runtime(), env._spillover())
            action, _ = model.predict(obs, action_masks=masks, deterministic=True)
            _, _, term, trunc, info = env.step([int(a) for a in action])
            b = info["reward_breakdown"]
            for k in tot:
                tot[k] += b[k]
            steps += 1
            switches += len(b["switched_junctions"])
            arrived += b["arrived_this_interval"]
            w = b["worst_lane"]
            wv = w.get("wait_time_max_single_vehicle", 0.0) if isinstance(w, dict) else 0.0
            worst = max(worst, wv)
            if wv > 90.0:
                starved_steps += 1
            for rec in info["safety_overrides"]:
                if rec["rule"] == RULE_EMERGENCY:
                    ovr_e += 1
                elif rec["rule"] == RULE_STARVATION:
                    ovr_s += 1
            if term or trunc:
                break
    finally:
        env.close()
    per = {f"{k}_per_step": v / max(1, steps) for k, v in tot.items()}
    return {"combo": list(combo), "seed": seed, "steps": steps,
            "switches": switches, "switches_per_step": switches / max(1, steps),
            "arrived": arrived, "starved_steps": starved_steps,
            "starved_pct": 100.0 * starved_steps / max(1, steps),
            "worst_wait": worst, "ovr_starvation": ovr_s, "ovr_emergency": ovr_e,
            **{f"sum_{k}": v for k, v in tot.items()}, **per}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("steps", type=int, nargs="+", help="checkpoint num_timesteps")
    ap.add_argument("--out", default=str(SWEEPS / "reward_term_replay.json"))
    args = ap.parse_args()

    results = {}
    for step in args.steps:
        p = ckpt_path(step)
        print(f"\n{'='*100}\nCKPT {step}  ({p.name})\n{'='*100}", flush=True)
        model = MaskablePPO.load(str(p))
        print(f"{'combo':>10} {'seed':>4} {'steps':>6} {'rew/stp':>8} {'starv/stp':>10} "
              f"{'thru/stp':>9} {'swtch/stp':>10} {'sw/step':>8} {'starv%':>7} "
              f"{'wait':>7} {'ovrS':>5}", flush=True)
        rows = []
        for combo, seed in PLAN:
            r = replay(model, combo, seed)
            rows.append(r)
            print(f"{str(combo):>10} {seed:>4} {r['steps']:>6} "
                  f"{r['total_per_step']:>8.4f} {r['starvation_penalty_per_step']:>10.4f} "
                  f"{r['throughput_bonus_per_step']:>9.4f} "
                  f"{r['switch_penalty_per_step']:>10.4f} "
                  f"{r['switches_per_step']:>8.4f} {r['starved_pct']:>7.2f} "
                  f"{r['worst_wait']:>7.1f} {r['ovr_starvation']:>5}", flush=True)
        results[str(step)] = rows
        f = lambda k: st.fmean([r[k] for r in rows])
        print(f"  MEAN  rew/step={f('total_per_step'):+.4f}  "
              f"starv/step={f('starvation_penalty_per_step'):.4f}  "
              f"thru/step={f('throughput_bonus_per_step'):.4f}  "
              f"switchpen/step={f('switch_penalty_per_step'):.4f}  "
              f"switches/step={f('switches_per_step'):.4f}  "
              f"starved%={f('starved_pct'):.2f}  arrived={f('arrived'):.0f}", flush=True)

    Path(args.out).write_text(json.dumps(results, indent=2))
    print(f"\nwrote {args.out}")

    if len(args.steps) >= 2:
        a, b = str(args.steps[0]), str(args.steps[-1])
        print(f"\n{'='*100}\nDELTA {a} -> {b}  (mean over the same 12 pinned pairs)\n{'='*100}")
        print(f"{'term':<34} {a:>12} {b:>12} {'delta':>12}")
        for k in ["total_per_step", "starvation_penalty_per_step",
                  "throughput_bonus_per_step", "switch_penalty_per_step",
                  "switches_per_step", "starved_pct", "worst_wait", "arrived",
                  "steps", "ovr_starvation"]:
            va = st.fmean([r[k] for r in results[a]])
            vb = st.fmean([r[k] for r in results[b]])
            print(f"{k:<34} {va:>12.4f} {vb:>12.4f} {vb - va:>+12.4f}")


if __name__ == "__main__":
    main()

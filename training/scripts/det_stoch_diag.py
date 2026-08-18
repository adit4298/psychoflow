"""Diagnostic: deterministic-vs-stochastic reward/fairness disagreement on
graph_attention's Stage 5 final checkpoint (51,624 steps).

DIAGNOSTIC ONLY — reads checkpoints, writes JSON. Modifies nothing in the repo.

Recorded finding being tested (CLAUDE.md §8 / BUILD_LOG 2026-08-16):
    deterministic        = 1.285947   worst_wait 121.0s / 1.09% starved
    stochastic n=10 mean = 1.027080   worst_wait mostly 75-103s / 0.00% starved
i.e. reward and fairness DISAGREE about which policy is better.

Two methodology points this script pins down that the original n=10 did not:

1. MODEL LOADED ONCE, LOOPED. A fresh MaskablePPO.load() calls
   set_random_seed(self.seed) restoring the checkpoint's training seed, so
   "stochastic" episodes drawn from separate loads are silently IDENTICAL.
   (Recorded trap, BUILD_LOG Stage 1.) One load, N episodes, RNG advances.

2. SCENARIO PINNED PER SEED. env.reset(seed=s) re-seeds the env's own
   scenario RNG, so every episode at seed s faces a bit-for-bit identical
   scenario. The ONLY thing varying across the stochastic sample is the
   policy's action sampling. Without this the sample mixes policy noise with
   scenario-draw noise and no per-term attribution is possible.

Per episode it accumulates the full §9.4 term decomposition from
info["reward_breakdown"], so "does the greedy action buy throughput at the
cost of one lane's tail" is answerable directly rather than by inference.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

# NOT derived by walking up from __file__ — this script lives in the
# scratchpad, whose path contains no segment literally named "Test", so a
# name-matching walk spins at the filesystem root forever (Path("C:/").parent
# is itself). Take the repo root explicitly and assert it, so a wrong path
# fails loudly on line 1 instead of hanging with no output.
REPO_ROOT = Path(r"C:\Users\aditp\OneDrive\Documents\GitHub\Test")
assert (REPO_ROOT / "env" / "psychoflow_env.py").is_file(), (
    f"repo root wrong: {REPO_ROOT}")
sys.path.insert(0, str(REPO_ROOT))

from sb3_contrib import MaskablePPO  # noqa: E402

from env.obs_action_spec import build_observation  # noqa: E402
from env.psychoflow_env import PsychoFlowEnv, ScenarioConfig  # noqa: E402
from prediction.spillover import SpilloverPredictor  # noqa: E402

CKPT = (REPO_ROOT / "training" / "checkpoints" / "stage5_graph_attention"
        / "psychoflow_stage5_51624_steps_final.zip")
OUT = REPO_ROOT / "training" / "checkpoints" / "_sweeps" / "det_stoch_diag.json"

PRIMARY_SEED = 7
PRIMARY_N = 40
ROBUST_SEEDS = [1, 3, 42]
ROBUST_N = 10


def run_one(env, model, seed, deterministic):
    """One episode on the scenario pinned by `seed`, with full term capture."""
    env.reset(seed=seed)
    served = env.phase_served_lanes()

    steps = 0
    total_reward = 0.0
    worst_wait = 0.0
    starved_steps = 0
    terms = {"starvation_penalty": 0.0, "throughput_bonus": 0.0,
             "emergency_penalty": 0.0, "switch_penalty": 0.0}
    per_junction = Counter()
    worst_lane_hits = Counter()
    worst_junction_hits = Counter()
    switch_events = 0
    per_step_worst = []
    overrides = []
    arrived = 0
    sim_time = 0.0
    terminated = truncated = False

    while True:
        snapshot = env.twin.snapshot
        runtime = env._runtime()
        masks = env.action_masks()
        obs = build_observation(snapshot, runtime, env._spillover())
        action, _ = model.predict(obs, action_masks=masks,
                                  deterministic=deterministic)

        obs_, reward, terminated, truncated, info = env.step(action)

        steps += 1
        total_reward += reward
        arrived = info["arrived_total"]
        sim_time = info["sim_time"]

        bd = info["reward_breakdown"]
        for k in terms:
            terms[k] += bd[k]
        for jid, v in bd["per_junction_starvation"].items():
            per_junction[jid] += v

        w = bd["worst_lane"]
        worst = w["wait_s"]
        per_step_worst.append(worst)
        worst_wait = max(worst_wait, worst)
        if worst > 90.0:
            starved_steps += 1
        if w["lane_id"] is not None:
            worst_lane_hits[w["lane_id"]] += 1
            worst_junction_hits[w["junction_id"]] += 1

        switch_events += len(bd["switched_junctions"])
        overrides.extend(info["safety_overrides"])

        if terminated or truncated:
            break

    per_step_worst.sort()

    def q(p):
        if not per_step_worst:
            return 0.0
        i = min(len(per_step_worst) - 1, int(p * len(per_step_worst)))
        return per_step_worst[i]

    ov = Counter(f"{o['rule']}/{o['outcome']}" for o in overrides)

    return {
        "seed": seed,
        "deterministic": deterministic,
        "steps": steps,
        "mean_reward": total_reward / max(1, steps),
        "total_reward": total_reward,
        "worst_wait": worst_wait,
        "starved_steps": starved_steps,
        "starved_pct": 100.0 * starved_steps / max(1, steps),
        "arrived": arrived,
        "sim_time": sim_time,
        "terminated": terminated,
        "truncated": truncated,
        # per-step means, so episodes of differing length compare directly
        "starvation_per_step": terms["starvation_penalty"] / max(1, steps),
        "throughput_per_step": terms["throughput_bonus"] / max(1, steps),
        "emergency_per_step": terms["emergency_penalty"] / max(1, steps),
        "switch_per_step": terms["switch_penalty"] / max(1, steps),
        "sum_starvation": terms["starvation_penalty"],
        "sum_throughput": terms["throughput_bonus"],
        "sum_emergency": terms["emergency_penalty"],
        "sum_switch": terms["switch_penalty"],
        "switch_events": switch_events,
        "switches_per_step": switch_events / max(1, steps),
        "per_junction_starvation": dict(per_junction),
        "worst_wait_p50": q(0.50),
        "worst_wait_p90": q(0.90),
        "worst_wait_p99": q(0.99),
        "worst_junction_hits": dict(worst_junction_hits),
        "worst_lane_top": worst_lane_hits.most_common(3),
        "overrides_total": len(overrides),
        "overrides_by_rule": dict(ov),
    }


def summarize(label, rows):
    if not rows:
        return {}
    keys = ["mean_reward", "worst_wait", "starved_pct", "arrived",
            "starvation_per_step", "throughput_per_step", "emergency_per_step",
            "switch_per_step", "switches_per_step", "worst_wait_p50",
            "worst_wait_p90", "worst_wait_p99", "overrides_total", "steps"]
    out = {"label": label, "n": len(rows)}
    for k in keys:
        vals = [r[k] for r in rows]
        out[k] = {
            "mean": statistics.fmean(vals),
            "stdev": statistics.stdev(vals) if len(vals) > 1 else 0.0,
            "min": min(vals), "max": max(vals),
        }
    return out


def main():
    print(f"checkpoint: {CKPT.name}")
    print(f"config: ScenarioConfig(lane_counts=(4,3,2)) — matches the recorded "
          f"det-vs-stoch methodology (Stage 1 config, fixed density, no emergencies)")
    print("model loaded ONCE; scenario pinned per seed via reset(seed=s)\n")

    model = MaskablePPO.load(str(CKPT))
    env = PsychoFlowEnv(
        scenario_config=ScenarioConfig(lane_counts=(4, 3, 2)),
        spillover_predictor=SpilloverPredictor(),
        seed=PRIMARY_SEED,
    )

    results = {"checkpoint": CKPT.name, "det": {}, "stoch": {}}

    try:
        for seed, n in [(PRIMARY_SEED, PRIMARY_N)] + [(s, ROBUST_N) for s in ROBUST_SEEDS]:
            print(f"=== seed {seed} ===", flush=True)

            # Deterministic twice on the first seed: confirms the scenario pin
            # and the greedy policy really are reproducible before any
            # stochastic number is compared against them.
            det_rows = []
            reps = 2 if seed == PRIMARY_SEED else 1
            for i in range(reps):
                r = run_one(env, model, seed, deterministic=True)
                det_rows.append(r)
                print(f"  DET  rep{i}  mean_reward={r['mean_reward']:.6f}  "
                      f"worst={r['worst_wait']:.1f}s  starved={r['starved_pct']:.2f}%  "
                      f"steps={r['steps']}  arrived={r['arrived']}", flush=True)
            results["det"][str(seed)] = det_rows

            stoch_rows = []
            for i in range(n):
                r = run_one(env, model, seed, deterministic=False)
                stoch_rows.append(r)
                print(f"  STO  {i:>2}    mean_reward={r['mean_reward']:.6f}  "
                      f"worst={r['worst_wait']:.1f}s  starved={r['starved_pct']:.2f}%  "
                      f"steps={r['steps']}  arrived={r['arrived']}", flush=True)
            results["stoch"][str(seed)] = stoch_rows

            d0 = det_rows[0]
            ss = summarize(f"stoch seed {seed}", stoch_rows)
            print(f"\n  -- seed {seed} summary --")
            print(f"     deterministic mean_reward = {d0['mean_reward']:.6f}")
            print(f"     stochastic    mean_reward = {ss['mean_reward']['mean']:.6f} "
                  f"(stdev {ss['mean_reward']['stdev']:.6f}, n={ss['n']})")
            print(f"     GAP (det - stoch)         = "
                  f"{d0['mean_reward'] - ss['mean_reward']['mean']:+.6f}")
            print(f"     det worst_wait {d0['worst_wait']:.1f}s / starved {d0['starved_pct']:.2f}%"
                  f"   vs stoch {ss['worst_wait']['mean']:.1f}s / {ss['starved_pct']['mean']:.2f}%")
            for term in ["throughput_per_step", "starvation_per_step",
                         "switch_per_step", "emergency_per_step",
                         "switches_per_step"]:
                print(f"     {term:<22} det={d0[term]:+.4f}   "
                      f"stoch={ss[term]['mean']:+.4f}   "
                      f"delta={d0[term] - ss[term]['mean']:+.4f}")
            print(flush=True)
    finally:
        env.close()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()

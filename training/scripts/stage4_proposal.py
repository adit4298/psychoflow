"""Point 2 — widen Stage 4's emergency proposal-quality measurement.

The recorded 0.885 pooled quality rests on **26 decidable junction-steps across
3 seeds**, and the original entry already flagged that pooled and mean-of-seeds
disagree violently (0.885 vs 0.636) because seed 42 drew only 2 decidable steps
and missed both. This widens the seed set.

TWO THINGS THE ORIGINAL DID NOT DO:

1. CONTAMINATION SCREEN. `phase0_baselines.py` runs `STAGES[4]` — the full
   TRAINING config — so an eval seed whose episode-1 draw matches a training
   draw is scoring the policy on a memorised scenario. Verified separately:
   eval seed 7 reproduces Stage 4 training episode 1 exactly, and it supplies
   11 of the 26 decidable steps behind 0.885. Every seed used here is screened
   against the committed `stage4/monitor*.csv` BEFORE it is run, and any hit is
   reported separately rather than pooled (master plan §15.4's "assert
   disjointness programmatically, not by inspection").

2. DENOMINATOR ENDOGENEITY, reported rather than hidden. `decidable` counts
   ambulance-junction-steps where some mask-valid slot serves the ambulance —
   so a policy that clears an ambulance FAST collects FEWER samples than one
   that lets it linger. Stage 4 drew 2-14 decidable steps per episode; the
   random control drew 19-28 in the same scenarios. Pooled quality therefore
   weights episodes by how badly the policy handled them. Mean-of-seeds is the
   unweighted read; both are printed, and disagreement between them is a
   sample-size symptom, not a tiebreak to pick from.

Classification code is IMPORTED from phase0_baselines (`run_episode`,
`ppo_picker`, `random_picker`) — not reimplemented — so results are directly
comparable to the recorded matrix.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics as st
import sys
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
assert (REPO_ROOT / "env" / "psychoflow_env.py").is_file(), f"bad root {REPO_ROOT}"
sys.path.insert(0, str(REPO_ROOT))

warnings.filterwarnings("ignore")

from sb3_contrib import MaskablePPO  # noqa: E402

from env.psychoflow_env import PsychoFlowEnv  # noqa: E402
from prediction.spillover import SpilloverPredictor  # noqa: E402
from training.curriculum import STAGES  # noqa: E402
from training.scripts.phase0_baselines import (  # noqa: E402
    ppo_picker, random_picker, run_episode,
)

CKPT = REPO_ROOT / "training/checkpoints/stage4/psychoflow_stage4_153600_steps_final.zip"
STAGE4_DIR = REPO_ROOT / "training" / "checkpoints" / "stage4"
OUT = REPO_ROOT / "training/checkpoints/_sweeps/stage4_proposal.json"
TOL = 1e-9


# -------------------------------------------------------------------------
# contamination screen
# -------------------------------------------------------------------------
def training_key_set(monitor_dir: Path | None = None) -> set[str]:
    """Every scenario the checkpoint under test actually trained on.

    Reads EVERY monitor*.csv in the directory, not a hardcoded pair — a
    resumed run writes one per burst (graph_attention has five), and missing
    one would under-report contamination, which fails in the dangerous
    direction.
    """
    keys = set()
    for p in sorted((monitor_dir or STAGE4_DIR).glob("monitor*.csv*")):
        lines = [ln for ln in p.open() if not ln.startswith("#")]
        for row in csv.DictReader(lines):
            if not row.get("emergency_route"):
                continue  # pre-Stage-4 logging: no emergency columns
            keys.add(f"{row['lane_counts']}|{float(row['density_mult_corridor']):.9f}"
                     f"|{float(row['density_mult_cross']):.9f}|{row['emergency_route']}"
                     f"|{float(row['emergency_depart_s']):.6f}")
    return keys


def eval_key(seed: int) -> str:
    """Episode-1 draw for reset(seed=seed), without starting SUMO."""
    env = PsychoFlowEnv(scenario_config=STAGES[4], seed=seed)
    env._rng = random.Random(seed)
    env._draw_scenario()
    return (f"{tuple(env._lane_counts)}|{env._density_mult['corridor_mean']:.9f}"
            f"|{env._density_mult['cross_mean']:.9f}|{env._emergency_route}"
            f"|{env._emergency_depart_s:.6f}")


def screen(seeds: list[int], monitor_dir: Path | None = None) -> dict[int, bool]:
    train = training_key_set(monitor_dir)
    return {s: (eval_key(s) in train) for s in seeds}


# -------------------------------------------------------------------------
def run_condition(label, pick_factory, seeds):
    rows = []
    print(f"\n--- {label} ---", flush=True)
    print(f"{'seed':>5} {'ambJ':>5} {'srv':>4} {'avd':>4} {'unav':>5} "
          f"{'decid':>6} {'quality':>8} {'chance':>7} {'ovrE':>5} {'ovrS':>6}", flush=True)
    for sd in seeds:
        env = PsychoFlowEnv(scenario_config=STAGES[4],
                            spillover_predictor=SpilloverPredictor(), seed=7)
        try:
            r = run_episode(env, pick_factory(), sd)
        finally:
            env.close()
        rows.append(r)
        q = "     n/a" if r["proposal_quality"] is None else f"{r['proposal_quality']:>8.3f}"
        c = "    n/a" if r["chance_quality"] is None else f"{r['chance_quality']:>7.3f}"
        print(f"{sd:>5} {r['amb_junction_steps']:>5} {r['served']:>4} "
              f"{r['blocked_avoidable']:>4} {r['blocked_unavoidable']:>5} "
              f"{r['decidable_junction_steps']:>6} {q} {c} "
              f"{r['overrides_emergency']:>5} {r['overrides_starvation']:>6}", flush=True)
    return rows


def summarize(label, rows, tag=""):
    s = sum(r["served"] for r in rows)
    a = sum(r["blocked_avoidable"] for r in rows)
    dec = s + a
    pooled = s / dec if dec else float("nan")
    qs = [r["proposal_quality"] for r in rows if r["proposal_quality"] is not None]
    cs = [r["chance_quality"] for r in rows if r["chance_quality"] is not None]
    # Pooled analytic chance, weighted by each episode's decidable count — the
    # correct floor for the pooled quality figure.
    cw = sum(r["chance_quality"] * r["decidable_junction_steps"]
             for r in rows if r["chance_quality"] is not None)
    cd = sum(r["decidable_junction_steps"]
             for r in rows if r["chance_quality"] is not None)
    pooled_chance = cw / cd if cd else float("nan")
    out = {
        "label": label + tag, "n_seeds": len(rows), "decidable": dec,
        "served": s, "avoidable": a,
        "pooled_quality": pooled, "pooled_chance": pooled_chance,
        "pooled_lift": pooled - pooled_chance,
        "mean_quality": st.fmean(qs) if qs else float("nan"),
        "std_quality": st.stdev(qs) if len(qs) > 1 else 0.0,
        "mean_chance": st.fmean(cs) if cs else float("nan"),
    }
    print(f"  {label + tag}")
    print(f"    n_seeds={out['n_seeds']}  decidable={dec}  served={s}  avoidable={a}")
    print(f"    POOLED quality={pooled:.4f}  chance={pooled_chance:.4f}  "
          f"LIFT={out['pooled_lift']:+.4f}")
    print(f"    MEAN-OF-SEEDS quality={out['mean_quality']:.4f} "
          f"(std {out['std_quality']:.4f})  chance={out['mean_chance']:.4f}")
    return out


def ztest(s1, n1, s2, n2):
    """Two-proportion z of condition 1 against control 2."""
    if not n1 or not n2:
        return float("nan"), float("nan")
    p1, p2 = s1 / n1, s2 / n2
    p = (s1 + s2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return float("nan"), float("nan")
    z = (p1 - p2) / se
    return z, math.erfc(abs(z) / math.sqrt(2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="1,2,3,5,7,10,13,21,42,99,123,777")
    ap.add_argument("--random-reps", type=int, default=2)
    ap.add_argument("--checkpoint", default=str(CKPT))
    ap.add_argument("--monitor-dir", default=str(STAGE4_DIR),
                    help="the checkpoint's OWN training record, for the screen")
    ap.add_argument("--label", default="STAGE4 153600")
    ap.add_argument("--skip-random", action="store_true",
                    help="reuse an already-committed control on the same seeds")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]

    print("=" * 92)
    print("CONTAMINATION SCREEN (§15.4: assert disjointness programmatically)")
    print("=" * 92)
    flags = screen(seeds, Path(args.monitor_dir))
    for sd, hit in flags.items():
        print(f"  seed {sd:>4}: {'*** TRAINING SCENARIO — will be reported apart ***' if hit else 'held-out'}")
    clean = [s for s in seeds if not flags[s]]
    dirty = [s for s in seeds if flags[s]]
    print(f"\n  held-out seeds: {clean}")
    print(f"  contaminated  : {dirty}")

    print(f"\n  screened against: {args.monitor_dir}")
    print(f"  checkpoint under test: {args.checkpoint}")

    model = MaskablePPO.load(args.checkpoint)
    results = {}
    lab = args.label

    rows_all = run_condition(lab, lambda: ppo_picker(model), seeds)
    by_seed = {r["seed"]: r for r in rows_all}
    results["stage4_all"] = summarize(lab, rows_all, " [ALL SEEDS]")
    results["stage4_heldout"] = summarize(
        lab, [by_seed[s] for s in clean], " [HELD-OUT ONLY]")
    if dirty:
        results["stage4_contaminated"] = summarize(
            lab, [by_seed[s] for s in dirty], " [CONTAMINATED ONLY]")

    rnd_rows = []
    for rep in range(0 if args.skip_random else args.random_reps):
        rng = random.Random(5000 + rep)
        rnd_rows += run_condition(f"RANDOM control rep{rep + 1}",
                                  lambda r=rng: random_picker(r), seeds)
    rnd_by = {}
    for r in rnd_rows:
        rnd_by.setdefault(r["seed"], []).append(r)
    if args.skip_random:
        prev = json.loads(Path(str(OUT)).read_text())
        rnd_rows = prev["random_rows"]
        rnd_by = {}
        for r in rnd_rows:
            rnd_by.setdefault(r["seed"], []).append(r)
        print("\n--- RANDOM control: REUSED from stage4_proposal.json "
              "(same 12 seeds, same STAGES[4] config) ---")
    results["random_all"] = summarize("RANDOM", rnd_rows, " [ALL SEEDS]")
    results["random_heldout"] = summarize(
        "RANDOM", [r for s in clean for r in rnd_by.get(s, [])], " [HELD-OUT ONLY]")

    print("\n" + "=" * 92)
    print(f"SIGNIFICANCE — {args.label} vs the random control, held-out seeds only")
    print("=" * 92)
    a, b = results["stage4_heldout"], results["random_heldout"]
    z, p = ztest(a["served"], a["decidable"], b["served"], b["decidable"])
    print(f"  {args.label} {a['served']}/{a['decidable']} = {a['pooled_quality']:.4f}   "
          f"random {b['served']}/{b['decidable']} = {b['pooled_quality']:.4f}")
    print(f"  two-proportion z = {z:+.3f}   p = {p:.4f}   "
          f"{'SIGNIFICANT at 0.05' if p < 0.05 else 'NOT significant at 0.05'}")
    print(f"\n  vs its OWN analytic chance floor: lift {a['pooled_lift']:+.4f}")
    results["ztest_heldout"] = {"z": z, "p": p}

    print("\n  RECORDED FOR COMPARISON: pooled 0.885 on 26 decidable, 3 seeds,")
    print("  11 of which came from the contaminated seed 7.")

    Path(args.out).write_text(json.dumps(
        {"summary": results, "stage4_rows": rows_all, "random_rows": rnd_rows,
         "contaminated_seeds": dirty}, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()

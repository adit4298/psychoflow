"""Adversarial scrutiny of the DEPLOYED checkpoint (Stage 4 @ 153,600).

`graph_attention` was diagnosed hard all night; Stage 4 became the deployed
default (4a bake-off) on a 4-combo, density-pinned, no-emergency grid and has
never received the same treatment. This harness applies it.

MEASUREMENT ONLY — env / reward / validator imported read-only. `LaneMetricProbe`
is IMPORTED from checkpoint_bakeoff rather than reimplemented, so every number
here is directly comparable to the 4a table instead of "close enough".

Sub-commands:
  --topology   all 27 lane-count combos x seeds. Point 1: is there a weak shape
               nobody looked for? Plain-episode config, which is structurally
               disjoint from Stage 4 training (verified: 0/80 training episodes
               have density 1.0, 80/80 have an ambulance).
  --tier0      Tier 0 on named combos — the "inherently hard vs policy gap"
               control, the same test that cleared (4,2,4) at Stage 2.
  --curve      Stage 4's OWN checkpoint curve. `graph_attention` collapsed after
               its kept checkpoint; nobody has checked whether 153,600 sits past
               Stage 4's own peak.
  --density    density {0.7,1.0,1.3} x combos. 4a pinned density at 1.0.
  --emergency  spawn_emergencies=True fairness check. 4a pinned it off.
  --detstoch   deterministic vs stochastic. Deployment runs deterministic; the
               gap has been measured on Stage 1 and on graph_attention, never
               on Stage 4.

MARGIN INSTRUMENTATION (added here, not in 4a): the §15.2 metrics all key off
DEFAULT_STARVATION_THRESHOLD_S = 90s. A policy whose waits sit just BELOW 90s
scores a perfect 0.00 while being one perturbation from failing — the same
class of defect as the worst_wait threshold artifact, pointing the other way.
So every row also carries p50/p90/p99 of the across-lane max wait and the
fraction of steps spent in the 70-90s approach band.

Shardable (`--shard i/n`) so the long grids run in parallel; each shard writes
its own JSON and `--merge` combines them.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
assert (REPO_ROOT / "env" / "psychoflow_env.py").is_file(), f"bad root {REPO_ROOT}"
sys.path.insert(0, str(REPO_ROOT))

warnings.filterwarnings("ignore")

from sb3_contrib import MaskablePPO  # noqa: E402

from agents.rule_based import Tier0Controller  # noqa: E402
from env.psychoflow_env import PsychoFlowEnv, ScenarioConfig  # noqa: E402
from prediction.spillover import SpilloverPredictor  # noqa: E402
from safety.validator import RULE_EMERGENCY, RULE_STARVATION  # noqa: E402
from sim.networks.generate_corridor import VALID_LANE_COUNTS  # noqa: E402
from sim.run_tier0_episode import run_episode  # noqa: E402
from training.evaluate_stage import ppo_picker  # noqa: E402
from training.scripts.checkpoint_bakeoff import LaneMetricProbe  # noqa: E402

CKPT_ROOT = REPO_ROOT / "training" / "checkpoints"
OUT_DIR = CKPT_ROOT / "_sweeps"
STAGE4 = CKPT_ROOT / "stage4" / "psychoflow_stage4_153600_steps_final.zip"

ALL_COMBOS = [(a, b, c) for a in VALID_LANE_COUNTS
              for b in VALID_LANE_COUNTS for c in VALID_LANE_COUNTS]
SEEDS = [1, 7, 42]

# Stage 4's own curve. Its Stage-4-specific budget is 153,600-102,400 = 51,200
# steps, so these span the whole emergency-config segment of the lineage.
CURVE_CKPTS = [107400, 117640, 127640, 137640, 147640, 153600]
CURVE_PAIRS = [((4, 3, 2), 7), ((3, 2, 3), 1), ((2, 4, 2), 7), ((4, 4, 4), 42)]

DENSITY_COMBOS = [(4, 3, 2), (2, 4, 2), (3, 2, 3)]
DENSITY_LEVELS = [0.7, 1.0, 1.3]


class RichProbe(LaneMetricProbe):
    """LaneMetricProbe + the distribution of the across-lane max wait.

    The parent's `starvation_events_count` / `starved_pct` are threshold
    counters at 90s; these percentiles say how much headroom produced that
    count, which is what distinguishes a genuinely safe policy from one
    sitting just under the line.
    """

    @staticmethod
    def _pct(vals, p):
        if not vals:
            return 0.0
        s = sorted(vals)
        return s[min(len(s) - 1, int(p * len(s)))]

    def summary(self) -> dict:
        out = super().summary()
        m = self._step_maxes
        n = max(1, len(m))
        out.update({
            "wait_p50": self._pct(m, 0.50),
            "wait_p90": self._pct(m, 0.90),
            "wait_p99": self._pct(m, 0.99),
            # steps spent in the approach band and over the line
            "pct_steps_70_90": 100.0 * sum(1 for w in m if 70.0 <= w < 90.0) / n,
            "pct_steps_over_90": 100.0 * sum(1 for w in m if w >= 90.0) / n,
        })
        return out


def make_env(combo, seed, *, density=None, emergencies=False):
    kw = dict(lane_counts=combo, randomize_lane_counts=False,
              randomize_density=False, spawn_emergencies=emergencies)
    cfg = ScenarioConfig(**kw)
    if density is not None and density != 1.0:
        # Scale the base flow rates rather than enabling randomize_density,
        # so the level is EXACT and reproducible, not a draw. Matches how
        # Stage 3's density sweep varied load.
        cfg.corridor_veh_per_hour *= density
        cfg.cross_veh_per_hour *= density
    return PsychoFlowEnv(scenario_config=cfg,
                         spillover_predictor=SpilloverPredictor(), seed=seed)


def run_one(label, model, combo, seed, *, density=None, emergencies=False,
            deterministic=True) -> dict:
    env = make_env(combo, seed, density=density, emergencies=emergencies)
    probe = RichProbe()
    if model is None:
        t0c = Tier0Controller()
        pick = lambda s, r, m, sv: t0c.act(s, r, m, sv)  # noqa: E731
    else:
        pick = ppo_picker(env, model, deterministic=deterministic)
    stats = run_episode(env, pick, on_step=lambda e, i: probe.observe(e))
    env.close()

    ovr = stats["overrides"]
    row = {
        "label": label, "combo": str(combo), "seed": seed,
        "density": density if density is not None else 1.0,
        "emergencies": emergencies, "deterministic": deterministic,
        "steps": stats["steps"], "arrived": stats["arrived"],
        "mean_reward": stats["mean_reward"],
        "starved_pct": 100 * stats["starved_steps"] / max(1, stats["steps"]),
        "worst_wait": stats["worst_wait"],
        "ovrE": sum(1 for r in ovr if r["rule"] == RULE_EMERGENCY),
        "ovrS": sum(1 for r in ovr if r["rule"] == RULE_STARVATION),
        "terminated": stats["terminated"], "truncated": stats["truncated"],
    }
    row.update(probe.summary())
    return row


def emit(row, t):
    print(f"  {row['label']:>14} {row['combo']:>10} seed={row['seed']:>2} "
          f"d={row['density']:.2f} "
          f"starv_ev={row['starvation_events_count']:>4} "
          f"starved={row['starved_pct']:>6.2f}% "
          f"p90={row['wait_p90']:>5.1f}s p99={row['wait_p99']:>6.1f}s "
          f"band70_90={row['pct_steps_70_90']:>5.1f}% "
          f"rew={row['mean_reward']:>7.4f} ovrS={row['ovrS']:>3} "
          f"term={str(row['terminated'])[0]} ({t:.0f}s)", flush=True)


def save(rows, name):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / name
    p.write_text(json.dumps(rows, indent=2))
    print(f"\nwrote {p}  ({len(rows)} rows)", flush=True)


def shard(items, spec):
    if not spec:
        return items
    i, n = (int(x) for x in spec.split("/"))
    return [x for k, x in enumerate(items) if k % n == i]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topology", action="store_true")
    ap.add_argument("--tier0", metavar="COMBOS",
                    help="semicolon list e.g. '4,2,2;3,2,2'")
    ap.add_argument("--curve", action="store_true")
    ap.add_argument("--density", action="store_true")
    ap.add_argument("--emergency", action="store_true")
    ap.add_argument("--detstoch", action="store_true")
    ap.add_argument("--shard", default="")
    ap.add_argument("--seeds", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--ckpt", default="",
                    help="override the checkpoint under test (topology only)")
    ap.add_argument("--tag", default="stage4", help="row label for --ckpt runs")
    args = ap.parse_args()

    seeds = [int(s) for s in args.seeds.split(",")] if args.seeds else SEEDS
    rows, t0 = [], time.time()

    if args.topology:
        model = MaskablePPO.load(args.ckpt or str(STAGE4))
        plan = shard([(c, s) for c in ALL_COMBOS for s in seeds], args.shard)
        print(f"TOPOLOGY: {len(plan)} episodes (shard {args.shard or 'all'})", flush=True)
        for combo, seed in plan:
            t = time.time(); r = run_one(args.tag, model, combo, seed)
            rows.append(r); emit(r, time.time() - t)
        save(rows, args.out or "stage4_topology.json")

    elif args.tier0:
        combos = [tuple(int(x) for x in c.split(",")) for c in args.tier0.split(";")]
        plan = shard([(c, s) for c in combos for s in seeds], args.shard)
        print(f"TIER0 CONTROL: {len(plan)} episodes", flush=True)
        for combo, seed in plan:
            t = time.time(); r = run_one("tier0", None, combo, seed)
            rows.append(r); emit(r, time.time() - t)
        save(rows, args.out or "stage4_tier0_control.json")

    elif args.curve:
        plan = shard([(ck, c, s) for ck in CURVE_CKPTS for c, s in CURVE_PAIRS],
                     args.shard)
        print(f"CURVE: {len(plan)} episodes", flush=True)
        cache = {}
        for ck, combo, seed in plan:
            if ck not in cache:
                nm = (f"psychoflow_stage4_{ck}_steps_final.zip" if ck in (153600, 112640)
                      else f"psychoflow_stage4_{ck}_steps.zip")
                cache[ck] = MaskablePPO.load(str(CKPT_ROOT / "stage4" / nm))
            t = time.time(); r = run_one(f"s4@{ck}", cache[ck], combo, seed)
            rows.append(r); emit(r, time.time() - t)
        save(rows, args.out or "stage4_curve.json")

    elif args.density:
        model = MaskablePPO.load(str(STAGE4))
        plan = shard([(c, s, d) for c in DENSITY_COMBOS for s in seeds
                      for d in DENSITY_LEVELS], args.shard)
        print(f"DENSITY: {len(plan)} episodes", flush=True)
        for combo, seed, d in plan:
            t = time.time(); r = run_one("stage4", model, combo, seed, density=d)
            rows.append(r); emit(r, time.time() - t)
        save(rows, args.out or "stage4_density.json")

    elif args.emergency:
        model = MaskablePPO.load(str(STAGE4))
        plan = shard([(c, s) for c in DENSITY_COMBOS + [(4, 4, 4)] for s in seeds],
                     args.shard)
        print(f"EMERGENCY-ON: {len(plan)} episodes", flush=True)
        for combo, seed in plan:
            t = time.time(); r = run_one("stage4", model, combo, seed, emergencies=True)
            rows.append(r); emit(r, time.time() - t)
        save(rows, args.out or "stage4_emergency.json")

    elif args.detstoch:
        # Model loaded ONCE and looped: a fresh .load() reseeds torch's RNG and
        # silently makes "stochastic" runs identical (CLAUDE.md methodology).
        model = MaskablePPO.load(str(STAGE4))
        combo = (4, 3, 2)
        print("DET vs STOCH on (4,3,2)", flush=True)
        for seed in seeds:
            t = time.time()
            r = run_one("det", model, combo, seed, deterministic=True)
            rows.append(r); emit(r, time.time() - t)
        for rep in range(8):
            t = time.time()
            r = run_one("stoch", model, combo, seeds[0], deterministic=False)
            r["rep"] = rep
            rows.append(r); emit(r, time.time() - t)
        save(rows, args.out or "stage4_detstoch.json")

    else:
        ap.error("pick a sub-command")

    print(f"wall clock: {(time.time() - t0) / 60:.1f} min", flush=True)


if __name__ == "__main__":
    # Tier 1 SUMO beacon (sim/sumo_activity.py): refuse to launch
    # concurrent SUMO while a training run or the backend is live.
    from sim.sumo_activity import require_free
    require_free('stage 4 scrutiny sweep')
    main()

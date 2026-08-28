"""4a — CHECKPOINT BAKE-OFF: which controller gets deployed as the backend default.

Decides §20's "know which mode you're actually demoing" and Session 3's
auto-mode checkpoint path, on evidence rather than on §9.5's architecture
A/B (which answered a different question: attention vs shared_policy, not
learned vs single-agent vs rule).

FOUR CONTROLLERS, identical scenarios:
    tier0          agents.rule_based.Tier0Controller (§9.1) — the demo floor
    stage4_153600  single-agent, full Stage 1->4 curriculum lineage
    ga_51624       graph_attention, §9.5's kept checkpoint
    ga_154024      graph_attention trained to Stage 4's budget (Burst D)

METRICS — §15.2's, deliberately NOT worst_wait (4b). Definitions stated
here because §15.2 names the metrics without defining them:

  starvation_events_count  RISING-EDGE count, per lane, of
                           wait_time_max_single_vehicle crossing
                           DEFAULT_STARVATION_THRESHOLD_S (90s) from below.
                           A lane already starved does not re-count until it
                           drops back under. This is an EVENT count, which is
                           what §15.2 asks for — distinct from starved_pct
                           (fraction of STEPS with any lane over the line).

  wait_time_variance_      Population variance ACROSS LANES of
  across_lanes             wait_time_max_single_vehicle at each step, then
                           averaged over steps. Units s^2. Uses the per-lane
                           max-single-vehicle wait, not wait_time_current:
                           the latter is a SUM over vehicles on the lane
                           (see agents/rule_based.py's "§9.1's UNITS" note),
                           so its variance tracks occupancy and lane count
                           rather than fairness. Comparable BETWEEN
                           CONTROLLERS on one (combo, seed) row; NOT
                           comparable across topologies with different lane
                           counts.

  mean_wait_max            Mean over steps of the across-lane max wait. The
                           honest continuous replacement for worst_wait: it
                           reads the BODY of the wait distribution, which is
                           what §9.4's reward integrates (see CLAUDE.md's
                           det-vs-stochastic entry, point 3).

  worst_wait               REPORTED BUT NOT DECISIVE. §10's ceiling fires at
                           STARVATION_CEILING_S=120, so this saturates at
                           ~121-142s the moment the ceiling engages even
                           once, and cannot separate a 1.6%-starved policy
                           from an 80%-starved one. Kept in the table only
                           to demonstrate that saturation.

  ovrE / ovrS              §10 overrides SPLIT by rule, per CLAUDE.md's
                           Phase 0 correction — never the combined count.

Config is pinned (no emergency, density 1.0, fixed lane counts) so the
fairness comparison is not confounded by the other randomisation axes,
matching training/evaluate_stage.py::_run_plain_episode exactly. Emergency
behaviour is a separate measurement (--emergency-recheck / phase0_baselines).

Usage:
    python -m training.scripts.checkpoint_bakeoff            # full 48-run grid
    python -m training.scripts.checkpoint_bakeoff --timing   # 1 episode, timed
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402
from sb3_contrib import MaskablePPO  # noqa: E402

from agents.rule_based import Tier0Controller  # noqa: E402
from env.psychoflow_env import PsychoFlowEnv, ScenarioConfig  # noqa: E402
from perception.lane_sensor import DEFAULT_STARVATION_THRESHOLD_S  # noqa: E402
from prediction.spillover import SpilloverPredictor  # noqa: E402
from safety.validator import RULE_EMERGENCY, RULE_STARVATION  # noqa: E402
from sim.run_tier0_episode import run_episode  # noqa: E402
from training.evaluate_stage import ppo_picker  # noqa: E402

CKPT_ROOT = REPO_ROOT / "training" / "checkpoints"
OUT_DIR = CKPT_ROOT / "_sweeps"

CONTROLLERS = {
    "tier0": None,
    "stage4_153600": CKPT_ROOT / "stage4" / "psychoflow_stage4_153600_steps_final.zip",
    "ga_51624": CKPT_ROOT / "stage5_graph_attention" / "psychoflow_stage5_51624_steps_final.zip",
    "ga_154024": CKPT_ROOT / "stage5_graph_attention" / "psychoflow_stage5_154024_steps_final.zip",
}

# (4,3,2) is the locked demo corridor (§0.1) and carries every recorded
# baseline. The other three are what set_topology (§13.1) would realistically
# swap to on stage: a symmetric-narrow, a symmetric-wide, and (3,2,3) — the
# one combo CONFIRMED REPEATABLE as hard at Stage 4 (2/8 seeds), included
# specifically to check the deployed choice has no landmine under it.
COMBOS = [(4, 3, 2), (2, 2, 2), (4, 4, 4), (3, 2, 3)]
SEEDS = [1, 7, 42]


class LaneMetricProbe:
    """Accumulates the §15.2 per-lane metrics across one episode."""

    def __init__(self):
        self.starv_events = 0
        self._was_starved: dict[str, bool] = {}
        self._step_vars: list[float] = []
        self._step_maxes: list[float] = []

    def observe(self, env) -> None:
        waits: list[float] = []
        for jdata in env.twin.snapshot["junctions"].values():
            for lane_id, reading in jdata["lanes"].items():
                w = float(reading["wait_time_max_single_vehicle"])
                waits.append(w)
                starved_now = w > DEFAULT_STARVATION_THRESHOLD_S
                if starved_now and not self._was_starved.get(lane_id, False):
                    self.starv_events += 1
                self._was_starved[lane_id] = starved_now
        if waits:
            self._step_maxes.append(max(waits))
            self._step_vars.append(statistics.pvariance(waits) if len(waits) > 1 else 0.0)

    def summary(self) -> dict:
        return {
            "starvation_events_count": self.starv_events,
            "wait_var": (sum(self._step_vars) / len(self._step_vars)) if self._step_vars else 0.0,
            "mean_wait_max": (sum(self._step_maxes) / len(self._step_maxes)) if self._step_maxes else 0.0,
        }


def make_env(combo, seed):
    return PsychoFlowEnv(
        scenario_config=ScenarioConfig(
            lane_counts=combo, randomize_lane_counts=False,
            randomize_density=False, spawn_emergencies=False,
        ),
        spillover_predictor=SpilloverPredictor(),
        seed=seed,
    )


def run_one(name, model, combo, seed) -> dict:
    env = make_env(combo, seed)
    probe = LaneMetricProbe()
    if model is None:
        tier0 = Tier0Controller()

        def pick(snapshot, runtime, masks, served):
            return tier0.act(snapshot, runtime, masks, served)
    else:
        pick = ppo_picker(env, model, deterministic=True)

    stats = run_episode(env, pick, on_step=lambda e, info: probe.observe(e))
    env.close()

    ovr = stats["overrides"]
    row = {
        "controller": name, "combo": str(combo), "seed": seed,
        "steps": stats["steps"], "arrived": stats["arrived"],
        "mean_reward": stats["mean_reward"],
        "starved_pct": 100 * stats["starved_steps"] / max(1, stats["steps"]),
        "worst_wait": stats["worst_wait"],
        "ovrE": sum(1 for r in ovr if r["rule"] == RULE_EMERGENCY),
        "ovrS": sum(1 for r in ovr if r["rule"] == RULE_STARVATION),
        "terminated": stats["terminated"],
    }
    row.update(probe.summary())
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timing", action="store_true", help="one episode, timed, then exit")
    args = ap.parse_args()

    for name, path in CONTROLLERS.items():
        if path is not None and not path.exists():
            raise SystemExit(f"missing checkpoint for {name}: {path}")

    if args.timing:
        t0 = time.time()
        row = run_one("tier0", None, (4, 3, 2), 7)
        print(f"tier0 (4,3,2) seed 7 in {time.time() - t0:.1f}s -> {row}")
        return

    rows = []
    grand_t0 = time.time()
    for name, path in CONTROLLERS.items():
        # Loaded ONCE per controller and looped — a fresh .load() calls
        # set_random_seed(self.seed) (CLAUDE.md's det/stoch methodology note).
        model = None if path is None else MaskablePPO.load(str(path))
        for combo in COMBOS:
            for seed in SEEDS:
                t0 = time.time()
                row = run_one(name, model, combo, seed)
                rows.append(row)
                print(f"  {name:>14} {str(combo):>10} seed={seed:>2}  "
                      f"starv_ev={row['starvation_events_count']:>3}  "
                      f"wait_var={row['wait_var']:>8.1f}  "
                      f"mean_wait_max={row['mean_wait_max']:>6.1f}s  "
                      f"starved={row['starved_pct']:>5.2f}%  "
                      f"worst={row['worst_wait']:>6.1f}s  "
                      f"rew={row['mean_reward']:>7.4f}  "
                      f"ovrS={row['ovrS']:>3}  ({time.time() - t0:.0f}s)",
                      flush=True)

    df = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "checkpoint_bakeoff.json").write_text(json.dumps(rows, indent=2))

    print("\n" + "=" * 100)
    print("PER-CONTROLLER MEANS (all 4 combos x 3 seeds)")
    print("=" * 100)
    print(f"{'controller':>15} {'starv_ev':>9} {'wait_var':>10} {'mean_wait_max':>14} "
          f"{'starved%':>9} {'worst_wait':>11} {'reward':>9} {'ovrS':>6} {'ovrE':>6} {'arrived':>8}")
    for name in CONTROLLERS:
        g = df[df["controller"] == name]
        print(f"{name:>15} {g['starvation_events_count'].mean():>9.2f} {g['wait_var'].mean():>10.1f} "
              f"{g['mean_wait_max'].mean():>14.1f} {g['starved_pct'].mean():>9.2f} "
              f"{g['worst_wait'].mean():>11.1f} {g['mean_reward'].mean():>9.4f} "
              f"{g['ovrS'].mean():>6.2f} {g['ovrE'].mean():>6.2f} {g['arrived'].mean():>8.0f}")

    print("\n" + "=" * 100)
    print("PER-COMBO BREAKDOWN — starvation_events_count / starved_pct (mean over 3 seeds)")
    print("=" * 100)
    print(f"{'combo':>10} " + " ".join(f"{n:>22}" for n in CONTROLLERS))
    for combo in COMBOS:
        cells = []
        for name in CONTROLLERS:
            g = df[(df["controller"] == name) & (df["combo"] == str(combo))]
            cells.append(f"{g['starvation_events_count'].mean():>8.1f} /{g['starved_pct'].mean():>6.2f}%   ")
        print(f"{str(combo):>10} " + " ".join(cells))

    print(f"\ntotal wall clock: {(time.time() - grand_t0) / 60:.1f} min")
    print(f"wrote {OUT_DIR / 'checkpoint_bakeoff.json'}")


if __name__ == "__main__":
    # Tier 1 SUMO beacon (sim/sumo_activity.py): refuse to launch
    # concurrent SUMO while a training run or the backend is live.
    from sim.sumo_activity import require_free
    require_free('checkpoint bake-off (48 SUMO episodes)')
    main()

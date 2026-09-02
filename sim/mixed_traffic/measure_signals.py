"""Is the sign-off watch's congestion caused by DEMAND or by the SIGNAL PLAN?

The density sweep already refuted demand: halted% sat at 34-35% at every level
from full training density down to 35% of it, and cutting demand only removed
bikes from the screen. This isolates the other candidate by holding demand fixed
and swapping the controller:

    'fixedtimer' - a dumb cyclic timer, rotating slots on a fixed period. The
                   in-env analogue of netconvert's static TLS, which is what the
                   sign-off watch runs today.
    'tier0'      - the Tier 0 fairness-first controller (§9.1), i.e. what the
                   demo actually runs in manual mode.

Same corridor, same seed, same demo driving model, same demand.
If halted% collapses under Tier 0, the congestion the user is seeing is an
artifact of the WATCH HARNESS, not a property of the driving model.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import traci  # noqa: E402

from agents.rule_based import Tier0Controller  # noqa: E402
from env.psychoflow_env import PsychoFlowEnv, ScenarioConfig  # noqa: E402
from prediction.spillover import SpilloverPredictor  # noqa: E402
from twin.digital_twin import CORRIDOR_JUNCTIONS  # noqa: E402

STEPS = 180                # 180 x 5s = 900s, matching the density sweep
FIXED_PERIOD_STEPS = 5     # 25s per slot — a plausible fixed-timer cycle

DEMO_VT = REPO_ROOT / "sim" / "networks" / "vehicle_types_demo.add.xml"


def sample() -> tuple[float, float, int, int]:
    vids = traci.vehicle.getIDList()
    if not vids:
        return 0.0, 0.0, 0, 0
    n_halt = sum(1 for v in vids if traci.vehicle.getSpeed(v) < 0.1)
    spd = sum(traci.vehicle.getSpeed(v) for v in vids) / len(vids)
    nb = sum(1 for v in vids if traci.vehicle.getTypeID(v).split(".")[0] == "bike")
    return n_halt / len(vids), spd, len(vids), nb


def run(arm: str) -> dict:
    env = PsychoFlowEnv(
        scenario_config=ScenarioConfig(lane_counts=(4, 3, 2)),
        spillover_predictor=SpilloverPredictor(),
        seed=7,
        label=f"sig_{arm}",
        vtype_file=DEMO_VT,
        lateral_resolution=0.4,
    )
    env.reset()
    ctrl = Tier0Controller() if arm == "tier0" else None
    served = env.phase_served_lanes()

    halted, speeds, onroad, bikes = [], [], [], []
    for i in range(STEPS):
        snapshot = env.twin.snapshot
        runtime = env._runtime()
        masks = env.action_masks()

        if ctrl is not None:
            action, _ = ctrl.act(snapshot, runtime, masks, served)
        else:
            # Dumb cyclic timer: advance one valid slot every FIXED_PERIOD_STEPS.
            cyc = i // FIXED_PERIOD_STEPS
            action = []
            m = np.asarray(masks).reshape(len(CORRIDOR_JUNCTIONS), -1)
            for j in range(len(CORRIDOR_JUNCTIONS)):
                valid = [s for s in range(m.shape[1]) if m[j, s]]
                action.append(valid[cyc % len(valid)] if valid else 0)
            action = tuple(action)

        env.step(action)
        h, s, n, nb = sample()
        halted.append(h); speeds.append(s); onroad.append(n); bikes.append(nb)
    env.close()

    m_ = lambda x: sum(x) / len(x) if x else 0.0
    return {
        "arm": arm,
        "mean_halted_pct": 100 * m_(halted),
        "peak_halted_pct": 100 * max(halted or [0]),
        "mean_speed_kmh": 3.6 * m_(speeds),
        "mean_on_road": m_(onroad),
        "mean_bikes": m_(bikes),
        "steps": len(halted),
    }


if __name__ == "__main__":
    from sim.sumo_activity import require_free
    require_free("measure_signals.py (fixed-timer vs Tier 0 congestion isolation)")

    rows = []
    for arm in ("fixedtimer", "tier0"):
        try:
            rows.append(run(arm))
        except Exception as e:
            import traceback
            print(f"{arm}: FAILED {type(e).__name__}: {e}")
            traceback.print_exc()

    print("\n" + "=" * 92)
    print(f"{'arm':>12} {'halted%':>9} {'peak%':>8} {'speed km/h':>12} "
          f"{'on road':>9} {'bikes':>7} {'steps':>7}")
    print("-" * 92)
    for r in rows:
        print(f"{r['arm']:>12} {r['mean_halted_pct']:>9.1f} {r['peak_halted_pct']:>8.1f} "
              f"{r['mean_speed_kmh']:>12.1f} {r['mean_on_road']:>9.0f} "
              f"{r['mean_bikes']:>7.1f} {r['steps']:>7}")
    print("=" * 92)

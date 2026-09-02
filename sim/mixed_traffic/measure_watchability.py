"""How congested is the sign-off watch actually, and what would fix it?

The user reports the corridor 'looks very congested' and bikes are hard to see.
Two candidate causes, and they need separating before anything is changed:

  (A) DEMAND      - the watch runs the training density (2x1000 + 6x600 veh/h).
  (B) SIGNAL PLAN - the watch runs netconvert's STATIC fixed-timer TLS, which is
                    the dumbest controller in the project. Tier 0 and the trained
                    policy are both far better. Congestion caused by (B) is an
                    artifact of the watch harness, NOT a property of the driving
                    model the user is trying to judge.

Sweeps density and reports flow quality per arm. Standalone raw SUMO, no env,
no checkpoint.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import sumolib  # noqa: E402
import traci  # noqa: E402

from sim.scenario_generator import write_route_file  # noqa: E402

NET = REPO_ROOT / "sim/networks/generated/corridor_432.net.xml"
DEMO = REPO_ROOT / "sim/networks/vehicle_types_demo.add.xml"
HERE = Path(__file__).resolve().parent

END = 900.0


def run(label: str, corridor_vph: float, cross_vph: float) -> dict:
    routes = HERE / f"_wat_{label}.rou.xml"
    write_route_file(
        routes, random.Random(7),
        corridor_veh_per_hour=corridor_vph,
        cross_veh_per_hour=cross_vph,
        flows_end_s=END,
        randomize_density=False,
    )
    cmd = [
        sumolib.checkBinary("sumo"),
        "-n", str(NET), "-a", str(DEMO), "-r", str(routes),
        "--step-length", "1.0", "--seed", "7", "--lateral-resolution", "0.4",
        "--no-step-log", "--duration-log.disable", "--no-warnings",
        "--waiting-time-memory", "1000", "--time-to-teleport", "600",
        "--collision.action", "warn", "--collision.mingap-factor", "0",
    ]
    traci.start(cmd, label=label)
    traci.switch(label)

    lanes = [l for l in traci.lane.getIDList() if not l.startswith(":")]
    halted_frac, speeds, running = [], [], []
    bikes_visible = []
    arrived = 0
    t = 0.0
    while t < END:
        traci.simulationStep()
        t = traci.simulation.getTime()
        arrived += traci.simulation.getArrivedNumber()
        if t % 10 == 0:
            vids = traci.vehicle.getIDList()
            if vids:
                n_halt = sum(1 for v in vids if traci.vehicle.getSpeed(v) < 0.1)
                halted_frac.append(n_halt / len(vids))
                speeds.append(sum(traci.vehicle.getSpeed(v) for v in vids) / len(vids))
                running.append(len(vids))
                nb = sum(1 for v in vids
                         if traci.vehicle.getTypeID(v).split(".")[0] == "bike")
                bikes_visible.append(nb)
    pending = traci.simulation.getMinExpectedNumber()
    traci.close()

    m = lambda x: sum(x) / len(x) if x else 0.0
    return {
        "label": label,
        "corridor_vph": corridor_vph, "cross_vph": cross_vph,
        "mean_halted_pct": 100 * m(halted_frac),
        "peak_halted_pct": 100 * max(halted_frac or [0]),
        "mean_speed_kmh": 3.6 * m(speeds),
        "mean_on_road": m(running),
        "peak_on_road": max(running or [0]),
        "mean_bikes_on_road": m(bikes_visible),
        "arrived": arrived,
        "pending_at_end": pending,
    }


if __name__ == "__main__":
    from sim.sumo_activity import require_free
    require_free("measure_watchability.py (sign-off watch density sweep)")

    arms = [
        ("full", 1000.0, 600.0),      # what the watch runs today = training density
        ("d070", 700.0, 420.0),
        ("d050", 500.0, 300.0),
        ("d035", 350.0, 210.0),
    ]
    rows = [run(*a) for a in arms]

    print("\n" + "=" * 104)
    print(f"{'arm':>6} {'corr/cross vph':>15} {'halted%':>9} {'peak%':>7} "
          f"{'speed km/h':>11} {'on road':>8} {'peak':>6} {'bikes':>7} "
          f"{'arrived':>8} {'pending':>8}")
    print("-" * 104)
    for r in rows:
        print(f"{r['label']:>6} {r['corridor_vph']:>7.0f}/{r['cross_vph']:<7.0f} "
              f"{r['mean_halted_pct']:>9.1f} {r['peak_halted_pct']:>7.1f} "
              f"{r['mean_speed_kmh']:>11.1f} {r['mean_on_road']:>8.0f} "
              f"{r['peak_on_road']:>6.0f} {r['mean_bikes_on_road']:>7.1f} "
              f"{r['arrived']:>8} {r['pending_at_end']:>8}")
    print("=" * 104)
    print("\nhalted% = share of on-road vehicles at a standstill, sampled every 10s.")
    print("bikes   = mean number of bikes on the whole 900m corridor at once.")

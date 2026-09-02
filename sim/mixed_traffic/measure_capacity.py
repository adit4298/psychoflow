"""At what demand does the corridor genuinely saturate?

Watch 2 scaled traffic to 3x in the sumo-gui "Scale Traffic" slider and the
corridor packed solid. The question that has to be answered before touching
anything is whether that is a DEFECT or simply demand past capacity.

Runs the watch's own controller at increasing demand and reports throughput.
The signature of genuine saturation is that ARRIVALS STOP RISING while INSERTED
keeps rising — i.e. the corridor is discharging as fast as it physically can and
the surplus just queues. A defect would instead show throughput COLLAPSING, or
vehicles failing to be inserted at all.

Standalone raw SUMO, demo driving model, no env, no checkpoint.
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
MIN_GREEN_S = 10.0


def run(scale: float) -> dict:
    label = f"cap{int(scale*100)}"
    routes = HERE / f"_cap_{int(scale*100)}.rou.xml"
    write_route_file(
        routes, random.Random(7),
        corridor_veh_per_hour=1000.0 * scale,
        cross_veh_per_hour=600.0 * scale,
        flows_end_s=END, randomize_density=False,
    )
    cmd = [
        sumolib.checkBinary("sumo"),
        "-n", str(NET), "-a", str(DEMO), "-r", str(routes),
        "--step-length", "1.0", "--seed", "7", "--lateral-resolution", "0.4",
        "--collision.action", "warn", "--collision.mingap-factor", "0",
        "--waiting-time-memory", "1000", "--time-to-teleport", "600",
        "--no-step-log", "--duration-log.disable", "--no-warnings",
    ]
    traci.start(cmd, label=label)
    traci.switch(label)

    tls = list(traci.trafficlight.getIDList())
    last_switch = {t: -1e9 for t in tls}
    arrived = 0
    halted, onroad = [], []
    t = 0.0
    while t < END:
        traci.simulationStep()
        t = traci.simulation.getTime()
        arrived += traci.simulation.getArrivedNumber()

        for tl in tls:
            if t - last_switch[tl] < MIN_GREEN_S:
                continue
            logic = traci.trafficlight.getAllProgramLogics(tl)[0]
            phases = logic.phases
            cur = traci.trafficlight.getPhase(tl)
            if "y" in phases[cur].state.lower():
                continue
            best, best_score = cur, -1.0
            for idx, ph in enumerate(phases):
                st = ph.state
                if "y" in st.lower():
                    continue
                served = {
                    ln for i, ln in enumerate(traci.trafficlight.getControlledLanes(tl))
                    if i < len(st) and st[i] in "Gg"
                }
                score = sum(traci.lane.getLastStepHaltingNumber(ln) for ln in served)
                score += 0.4 * sum(traci.lane.getWaitingTime(ln) for ln in served) / 60.0
                if score > best_score:
                    best, best_score = idx, score
            if best != cur:
                traci.trafficlight.setPhase(tl, best)
                last_switch[tl] = t

        if t % 10 == 0:
            vids = traci.vehicle.getIDList()
            if vids:
                halted.append(sum(1 for v in vids
                                  if traci.vehicle.getSpeed(v) < 0.1) / len(vids))
                onroad.append(len(vids))

    loaded = traci.simulation.getLoadedNumber()
    pending = traci.simulation.getMinExpectedNumber()
    traci.close()
    routes.unlink(missing_ok=True)

    m = lambda x: sum(x) / len(x) if x else 0.0
    return {
        "scale": scale,
        "arrived": arrived,
        "on_road": m(onroad),
        "peak_on_road": max(onroad or [0]),
        "halted_pct": 100 * m(halted),
        "still_pending": pending,
    }


if __name__ == "__main__":
    from sim.sumo_activity import require_free
    require_free("measure_capacity.py (corridor saturation sweep)")

    rows = [run(s) for s in (1.0, 1.5, 2.0, 3.0)]

    print("\n" + "=" * 92)
    print(f"{'scale':>7} {'arrived/900s':>14} {'on road':>9} {'peak':>7} "
          f"{'halted%':>9} {'still queued':>14}")
    print("-" * 92)
    base = rows[0]["arrived"]
    for r in rows:
        print(f"{r['scale']:>6.1f}x {r['arrived']:>14} {r['on_road']:>9.0f} "
              f"{r['peak_on_road']:>7.0f} {r['halted_pct']:>9.1f} "
              f"{r['still_pending']:>14}")
    print("=" * 92)
    print(f"\nthroughput at 3x vs 1x: {rows[-1]['arrived'] / base:.2f}x "
          f"(demand is 3.00x)")
    print("If arrivals plateau while demand triples, the corridor is at capacity —")
    print("that is real saturation, not a simulation defect.")

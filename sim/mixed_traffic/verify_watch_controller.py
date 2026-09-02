"""Does run_demo_gui.py's OWN inline controller actually decongest?

run_controlled() does not import Tier0Controller — it drives the GUI's own SUMO
process directly through TraCI with a queue-pressure phase rotator. That is a
DIFFERENT controller from the one measured earlier, so its effect has to be
measured too rather than inherited from Tier 0's numbers.

Runs the identical control loop against HEADLESS sumo on the same route file the
watch uses, and reports the same statistics the earlier isolation test did:

    fixed timer  29.1% halted / peak 60.4% / 21.1 km/h   (measured earlier)
    Tier 0        8.0% halted / peak 15.5% / 29.2 km/h   (measured earlier)
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import sumolib  # noqa: E402
import traci  # noqa: E402

NET = REPO_ROOT / "sim/networks/generated/corridor_432.net.xml"
DEMO = REPO_ROOT / "sim/networks/vehicle_types_demo.add.xml"
ROUTES = REPO_ROOT / "sim/routes/demo_gui_432_seed7.rou.xml"
END = 900.0
MIN_GREEN_S = 10.0


def run(controlled: bool) -> dict:
    label = "wc_on" if controlled else "wc_off"
    cmd = [
        sumolib.checkBinary("sumo"),
        "-n", str(NET), "-a", str(DEMO), "-r", str(ROUTES),
        "--step-length", "1.0", "--seed", "7", "--lateral-resolution", "0.4",
        "--collision.action", "warn", "--collision.mingap-factor", "0",
        "--waiting-time-memory", "1000", "--time-to-teleport", "600",
        "--no-step-log", "--duration-log.disable", "--no-warnings",
    ]
    traci.start(cmd, label=label)
    traci.switch(label)

    tls = list(traci.trafficlight.getIDList())
    last_switch = {t: -1e9 for t in tls}
    halted, speeds, bikes, switches = [], [], [], 0
    t = 0.0
    while t < END and traci.simulation.getMinExpectedNumber() > 0:
        traci.simulationStep()
        t = traci.simulation.getTime()

        if controlled:
            # ---- verbatim copy of run_demo_gui.run_controlled's loop body ----
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
                    switches += 1
            # ------------------------------------------------------------------

        if t % 10 == 0:
            vids = traci.vehicle.getIDList()
            if vids:
                halted.append(sum(1 for v in vids if traci.vehicle.getSpeed(v) < 0.1) / len(vids))
                speeds.append(sum(traci.vehicle.getSpeed(v) for v in vids) / len(vids))
                bikes.append(sum(1 for v in vids
                                 if traci.vehicle.getTypeID(v).split(".")[0] == "bike"))
    traci.close()

    m = lambda x: sum(x) / len(x) if x else 0.0
    return {
        "arm": "watch controller" if controlled else "static TLS (old watch)",
        "halted": 100 * m(halted),
        "peak": 100 * max(halted or [0]),
        "kmh": 3.6 * m(speeds),
        "bikes": m(bikes),
        "switches": switches,
    }


if __name__ == "__main__":
    from sim.sumo_activity import require_free
    require_free("verify_watch_controller.py")

    rows = [run(False), run(True)]
    print("\n" + "=" * 88)
    print(f"{'arm':>24} {'halted%':>9} {'peak%':>8} {'km/h':>8} {'bikes':>7} {'switches':>10}")
    print("-" * 88)
    for r in rows:
        print(f"{r['arm']:>24} {r['halted']:>9.1f} {r['peak']:>8.1f} "
              f"{r['kmh']:>8.1f} {r['bikes']:>7.1f} {r['switches']:>10}")
    print("=" * 88)

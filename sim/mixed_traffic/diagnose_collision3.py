"""Trace f_r_ew.28's lateral offset from the moment of its own insertion
(departure) through the collision, to distinguish an insertion-time artifact
from a drift caused by interaction with queued neighbors (e.g. a filtering
bike/auto against a standing truck).
"""
from __future__ import annotations
import sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parents[2]
# ^ Derived, not hardcoded. This harness ran from a scratchpad during the
#   mixed-traffic work and carried an absolute path to one machine's checkout.
#   parents[2] is the repo root from sim/mixed_traffic/. See README.md here.
sys.path.insert(0, str(REPO_ROOT))
HERE = Path(__file__).resolve().parent
import sumolib
import traci

NET = REPO_ROOT / "sim/networks/generated/corridor_432.net.xml"
TIERED = REPO_ROOT / "sim/networks/vehicle_types_demo.add.xml"
ROUTES = HERE / "measure.rou.xml"

TARGET = "f_r_ew.28"

def main():
    cmd = [
        sumolib.checkBinary("sumo"),
        "-n", str(NET), "-a", str(TIERED), "-r", str(ROUTES),
        "--step-length", "1.0", "--seed", "7", "--lateral-resolution", "0.4",
        "--no-step-log", "--duration-log.disable",
        "--waiting-time-memory", "1000", "--time-to-teleport", "600",
        "--collision.action", "warn", "--collision.mingap-factor", "0",
    ]
    traci.start(cmd, label="diag3")
    traci.switch("diag3")

    seen = False
    t = 0.0
    while t < 250:
        traci.simulationStep()
        t = traci.simulation.getTime()
        if TARGET in traci.vehicle.getIDList():
            if not seen:
                seen = True
                print(f"[INSERTED at t={t}]")
            lane = traci.vehicle.getLaneID(TARGET)
            pos = traci.vehicle.getLanePosition(TARGET)
            latoff = traci.vehicle.getLateralLanePosition(TARGET)
            speed = traci.vehicle.getSpeed(TARGET)
            neighbors = []
            for other in traci.vehicle.getIDList():
                if other == TARGET:
                    continue
                try:
                    olane = traci.vehicle.getLaneID(other)
                except Exception:
                    continue
                if olane == lane:
                    opos = traci.vehicle.getLanePosition(other)
                    if abs(opos - pos) < 15.0:
                        neighbors.append((other, round(opos - pos, 2)))
            print(f"t={t:6.1f} lane={lane:<10} pos={pos:7.2f} latOff={latoff:+.3f} "
                  f"speed={speed:5.2f} sameLaneNear={neighbors}")

    traci.close()

if __name__ == "__main__":
    from sim.sumo_activity import require_free
    require_free("diagnose_collision3.py (insertion-to-collision lateral trace)")
    main()

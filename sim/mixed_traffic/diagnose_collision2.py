"""Follow-up pass: lateral offset (getLateralLanePosition), vClass/vType params,
and vehicle length/width for both trucks, across the window immediately before
the collision. Lane centers at J2_J1_0/1 are 3.2m apart with 3.2m-wide lanes,
i.e. lane edges touch with ZERO nominal margin -- so any positive lateral drift
off-center by either vehicle removes the safety gap entirely. This checks
whether that is what happened.
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

TARGETS = {"f_r_ew.36", "f_r_ew.28"}

def main():
    cmd = [
        sumolib.checkBinary("sumo"),
        "-n", str(NET), "-a", str(TIERED), "-r", str(ROUTES),
        "--step-length", "1.0", "--seed", "7", "--lateral-resolution", "0.4",
        "--no-step-log", "--duration-log.disable",
        "--waiting-time-memory", "1000", "--time-to-teleport", "600",
        "--collision.action", "warn", "--collision.mingap-factor", "0",
    ]
    traci.start(cmd, label="diag2")
    traci.switch("diag2")

    printed_params = set()
    t = 0.0
    while t < 250:
        traci.simulationStep()
        t = traci.simulation.getTime()
        if t < 220:
            continue
        for vid in TARGETS:
            if vid not in traci.vehicle.getIDList():
                continue
            if vid not in printed_params:
                printed_params.add(vid)
                vtype = traci.vehicle.getTypeID(vid)
                length = traci.vehicle.getLength(vid)
                width = traci.vehicle.getWidth(vid)
                minGapLat = traci.vehicle.getParameter(vid, "laneChangeModel.minGapLat")
                latAlign = traci.vehicle.getLateralAlignment(vid)
                print(f"[PARAMS] {vid}  type={vtype}  length={length}  width={width}  "
                      f"minGapLat={minGapLat}  latAlignment={latAlign}")
        line = []
        for vid in sorted(TARGETS):
            if vid not in traci.vehicle.getIDList():
                line.append(f"{vid}: gone")
                continue
            lane = traci.vehicle.getLaneID(vid)
            pos = traci.vehicle.getLanePosition(vid)
            latpos = traci.vehicle.getLateralLanePosition(vid)
            width = traci.vehicle.getWidth(vid)
            y = traci.vehicle.getPosition(vid)[1]
            line.append(f"{vid}[{lane}] pos={pos:6.2f} latOff={latpos:+.3f} w={width:.2f} y={y:7.3f}")
        print(f"t={t:6.1f}  " + " | ".join(line))

    traci.close()

if __name__ == "__main__":
    from sim.sumo_activity import require_free
    require_free("diagnose_collision2.py (lateral offset trace)")
    main()

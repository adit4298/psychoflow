"""Probe: does traci.vehicletype.* accept a vTypeDistribution id?

Condition 1's repo-wide getTypeID() audit turned up a SECOND affected site the
first report missed: perception/weather.py addresses vTypes BY ID --
traci.vehicletype.getTau("bike") / setTau("bike", ...) for every entry of
VEHICLE_TYPES -- inside WeatherModel.attach(), which env/psychoflow_env.py
calls on the demo path (psychoflow_env.py:492 -> twin.attach() ->
weather.attach()).

If `bike` becomes a DISTRIBUTION id rather than a vType id, this either raises
(demo backend crashes at reset) or silently no-ops (weather stops working for
the tiered types). Either way it has to be known before building, not after.

Standalone raw SUMO. No PsychoFlowEnv, no checkpoint.
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

import sumolib  # noqa: E402
import traci  # noqa: E402

NET = REPO_ROOT / "sim" / "networks" / "generated" / "corridor_432.net.xml"


def main() -> None:
    add_p = HERE / "_probe_types.add.xml"     # written by probe_dist.py
    rou_p = HERE / "_probe_routes.rou.xml"
    if not add_p.exists():
        raise SystemExit("run probe_dist.py first (it writes the probe files)")

    cmd = [
        sumolib.checkBinary("sumo"),
        "-n", str(NET), "-a", str(add_p), "-r", str(rou_p),
        "--step-length", "1.0", "--seed", "7", "--lateral-resolution", "0.4",
        "--no-step-log", "--duration-log.disable", "--no-warnings",
        "--waiting-time-memory", "1000", "--time-to-teleport", "600",
    ]
    traci.start(cmd, label="vtd")
    traci.switch("vtd")
    traci.simulationStep()

    print("=" * 70)
    print("traci.vehicletype.getIDList():")
    ids = sorted(traci.vehicletype.getIDList())
    print(f"  {ids}")
    print(f"  'bike' present as a vehicletype id? {'bike' in ids}")
    print("=" * 70)

    # Exactly what WeatherModel.attach() does, per type.
    from perception.lane_sensor import VEHICLE_TYPES
    print("\nWeatherModel.attach() equivalent, per VEHICLE_TYPES entry:")
    for vt in VEHICLE_TYPES:
        try:
            tau = traci.vehicletype.getTau(vt)
            ms = traci.vehicletype.getMaxSpeed(vt)
            sg = traci.vehicletype.getImperfection(vt)
            print(f"  {vt:<12} OK    tau={tau:.3f} maxSpeed={ms:.2f} sigma={sg:.2f}")
        except traci.TraCIException as exc:
            print(f"  {vt:<12} RAISE {type(exc).__name__}: {exc}")

    # And the write half, which is what actually changes behaviour.
    print("\nWeatherModel.set_state() equivalent (setTau):")
    for vt in VEHICLE_TYPES:
        try:
            traci.vehicletype.setTau(vt, 1.5)
            print(f"  {vt:<12} setTau OK -> readback "
                  f"{traci.vehicletype.getTau(vt):.3f}")
        except traci.TraCIException as exc:
            print(f"  {vt:<12} setTau RAISE {type(exc).__name__}: {exc}")

    # Does a write to a SUB-type reach the vehicles drawn from it?
    print("\nsub-type addressing (the tiered ids SUMO actually reports):")
    for vt in ("bike.cautious", "bike.normal", "bike.aggressive"):
        try:
            traci.vehicletype.setTau(vt, 1.7)
            print(f"  {vt:<18} setTau OK -> readback "
                  f"{traci.vehicletype.getTau(vt):.3f}")
        except traci.TraCIException as exc:
            print(f"  {vt:<18} RAISE {type(exc).__name__}: {exc}")

    traci.close()


if __name__ == "__main__":
    from sim.sumo_activity import require_free
    require_free("probe_vtypedomain.py (vehicletype-domain distribution probe)")
    main()

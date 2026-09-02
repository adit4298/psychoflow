"""Probe: can a <vTypeDistribution> reference OTHER DISTRIBUTIONS in vTypes=,
and what does traci.vehicle.getTypeID() return for a vehicle drawn from one?

This is the blocking design question for the tier refinement. The route file
scenario_generator.py writes always contains

    <vTypeDistribution id="mixed" vTypes="bike auto car truck" .../>

so if `bike` becomes a distribution, `mixed` must be able to nest it. And
perception/lane_sensor.py classifies by getTypeID().split("@")[0] against
VEHICLE_TYPES = (bike, auto, car, truck, ambulance) -- so whatever id SUMO
reports has to be resolvable back to a base type.

Standalone raw SUMO. Constructs no PsychoFlowEnv, loads no checkpoint.
"""

from __future__ import annotations

import sys
from collections import Counter
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

# A minimal demo add-file where `bike` is a DISTRIBUTION of three tiers and
# `auto`/`car`/`truck`/`ambulance` stay plain vTypes. Deliberately tiny -- this
# probes the mechanism, not the tuning.
ADD = """<?xml version="1.0" encoding="UTF-8"?>
<additional>
    <vTypeDistribution id="bike">
        <vType id="bike.cautious"   probability="0.20" vClass="bicycle" length="1.8" width="0.65" maxSpeed="11.0" tau="1.2"/>
        <vType id="bike.normal"     probability="0.45" vClass="bicycle" length="1.8" width="0.65" maxSpeed="11.0" tau="1.0"/>
        <vType id="bike.aggressive" probability="0.35" vClass="bicycle" length="1.8" width="0.65" maxSpeed="11.0" tau="0.9"/>
    </vTypeDistribution>
    <vType id="auto"  vClass="passenger" length="3.2" width="1.4" maxSpeed="9.5"/>
    <vType id="car"   vClass="passenger" length="4.5" width="1.8" maxSpeed="12.5"/>
    <vType id="truck" vClass="truck"     length="7.5" width="2.4" maxSpeed="10.0"/>
    <vType id="ambulance" vClass="emergency" length="5.5" width="2.0" maxSpeed="15.0"/>
</additional>
"""

# Mirrors exactly what sim/scenario_generator.py::write_route_file emits.
ROU = """<?xml version="1.0" encoding="UTF-8"?>
<routes>
    <vTypeDistribution id="mixed" vTypes="bike auto car truck" probabilities="0.15 0.25 0.5 0.1"/>
    <route id="r_we" edges="W1_J1 J1_J2 J2_J3 J3_E3"/>
    <route id="r_ns1" edges="N1_J1 J1_S1"/>
    <flow id="f_we" type="mixed" route="r_we" begin="0" end="600" vehsPerHour="1000" departLane="random" departSpeed="max"/>
    <flow id="f_ns1" type="mixed" route="r_ns1" begin="0" end="600" vehsPerHour="600" departLane="random" departSpeed="max"/>
</routes>
"""


def main() -> None:
    add_p = HERE / "_probe_types.add.xml"
    rou_p = HERE / "_probe_routes.rou.xml"
    add_p.write_text(ADD, encoding="utf-8")
    rou_p.write_text(ROU, encoding="utf-8")

    cmd = [
        sumolib.checkBinary("sumo"),
        "-n", str(NET), "-a", str(add_p), "-r", str(rou_p),
        "--step-length", "1.0", "--seed", "7",
        "--lateral-resolution", "0.4",
        "--no-step-log", "--duration-log.disable",
        "--waiting-time-memory", "1000", "--time-to-teleport", "600",
    ]
    print("CMD:", " ".join(cmd), "\n")
    traci.start(cmd, label="probe")
    traci.switch("probe")

    seen: Counter[str] = Counter()
    for _ in range(400):
        traci.simulationStep()
        for vid in traci.simulation.getDepartedIDList():
            seen[traci.vehicle.getTypeID(vid)] += 1
    traci.close()

    print("=" * 68)
    print("getTypeID() values observed on departure (raw, no splitting):")
    total = sum(seen.values())
    for tid, n in seen.most_common():
        print(f"   {tid:<18} {n:5d}   {100*n/total:5.1f}%")
    print(f"   {'TOTAL':<18} {total:5d}")
    print("=" * 68)

    bike_tiers = {k: v for k, v in seen.items() if k.startswith("bike")}
    bike_total = sum(bike_tiers.values())
    print(f"\nNESTED DISTRIBUTION RESOLVED: {'YES' if bike_total else 'NO'}")
    if bike_total:
        print("  observed bike-tier split (target 0.20 / 0.45 / 0.35):")
        for k in sorted(bike_tiers):
            print(f"    {k:<18} {bike_tiers[k]:4d}  {bike_tiers[k]/bike_total:.3f}")
    print(f"\nbike share of all vehicles = {bike_total/total:.3f} (mixed target 0.15)")

    # The lane_sensor question, stated as the code actually does it.
    from perception.lane_sensor import VEHICLE_TYPES
    print(f"\nlane_sensor VEHICLE_TYPES = {VEHICLE_TYPES}")
    unknown = [t for t in seen if t.split("@")[0] not in VEHICLE_TYPES]
    print(f"ids lane_sensor would classify as UNKNOWN today: {sorted(unknown)}")
    print(f"  -> would they resolve via split('.')[0]? "
          f"{all(t.split('.')[0] in VEHICLE_TYPES for t in unknown)}")


if __name__ == "__main__":
    from sim.sumo_activity import require_free
    require_free("probe_dist.py (vTypeDistribution nesting probe)")
    main()

"""CONDITION 1 evidence: raw BEFORE/AFTER type_composition and weather counts
under a LIVE TIERED scenario, plus the inertness proof on the default file.

Deliberately does NOT argue that the fixes are correct -- it runs both the old
and the new rule against the same live SUMO state and prints the counts.

Two fixes are covered, because the repo-wide getTypeID() audit found a second
affected site:

  1. perception/lane_sensor.py  -- getTypeID() returns the concrete sub-type
     ("bike.normal"), so the old VEHICLE_TYPES match zeroed type_composition
     for every tiered vehicle. AFTER uses the real shipped LaneSensor; BEFORE
     replicates the old one-line rule inline.

  2. perception/weather.py -- traci.vehicletype.{get,set}* accept a
     DISTRIBUTION id and resolve it to one randomly sampled member, so §7.4
     wrote to ~1 tier of 3. AFTER uses the real shipped WeatherModel.

Standalone raw SUMO. No PsychoFlowEnv, no checkpoint, no reward/validator.
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

from perception.lane_sensor import LaneSensor, VEHICLE_TYPES, base_vtype  # noqa: E402
from perception.weather import WeatherModel  # noqa: E402

NET = REPO_ROOT / "sim" / "networks" / "generated" / "corridor_432.net.xml"
DEMO = REPO_ROOT / "sim" / "networks" / "vehicle_types_demo.add.xml"
DEFAULT = REPO_ROOT / "sim" / "networks" / "vehicle_types.add.xml"
ROUTES = HERE / "measure.rou.xml"


def old_rule(type_id: str) -> str:
    """The rule as it stood before the fix: strip '@' only."""
    return type_id.split("@")[0]


def run(vtypes: Path, lateral: float | None, tag: str) -> None:
    cmd = [
        sumolib.checkBinary("sumo"),
        "-n", str(NET), "-a", str(vtypes), "-r", str(ROUTES),
        "--step-length", "1.0", "--seed", "7",
        "--no-step-log", "--duration-log.disable", "--no-warnings",
        "--waiting-time-memory", "1000", "--time-to-teleport", "600",
        "--collision.action", "warn", "--collision.mingap-factor", "0",
    ]
    if lateral is not None:
        cmd += ["--lateral-resolution", str(lateral)]

    traci.start(cmd, label=tag)
    traci.switch(tag)

    weather = WeatherModel()
    weather.attach()

    for _ in range(400):
        traci.simulationStep()

    lanes = [l for l in traci.lane.getIDList() if not l.startswith(":")]
    sensor = LaneSensor()

    after = Counter({t: 0 for t in VEHICLE_TYPES})
    before = Counter({t: 0 for t in VEHICLE_TYPES})
    before_unknown: Counter[str] = Counter()
    n_veh = 0

    for lane in lanes:
        # AFTER: the real shipped code path.
        reading = sensor.read_lane(lane)
        after.update(reading.type_composition)
        # BEFORE: the old rule, same live state, computed inline.
        for vid in traci.lane.getLastStepVehicleIDs(lane):
            n_veh += 1
            tid = old_rule(traci.vehicle.getTypeID(vid))
            if tid in before:
                before[tid] += 1
            else:
                before_unknown[tid] += 1

    print("=" * 74)
    print(f"{tag}   vtypes={vtypes.name}  lateral_resolution={lateral}")
    print("=" * 74)
    print(f"  vehicles on non-internal lanes at t=400: {n_veh}")
    print(f"\n  type_composition, summed over all {len(lanes)} lanes:")
    print(f"    {'type':<12} {'BEFORE (old rule)':>20} {'AFTER (shipped)':>18}")
    for t in VEHICLE_TYPES:
        flag = "   <-- ZEROED" if before[t] == 0 and after[t] > 0 else ""
        print(f"    {t:<12} {before[t]:>20} {after[t]:>18}{flag}")
    print(f"    {'TOTAL':<12} {sum(before.values()):>20} {sum(after.values()):>18}")

    print(f"\n  BEFORE unknown_types (counts lost from the schema): "
          f"{dict(before_unknown) or '{}'}")
    print(f"  AFTER  sensor.unknown_types: {sensor.unknown_types or 'set()'}")

    # ---- weather: does §7.4 reach every tier? ----
    print(f"\n  weather -- ids attach() resolved to: "
          f"{sorted(weather._baselines)}")
    weather.set_state("heavy_rain", 400.0)
    live = weather.current_vtype_params()
    print("    after set_state('heavy_rain'), live tau read back per id:")
    for vt in sorted(live):
        base = weather._baselines[vt]["tau"]
        print(f"      {vt:<18} baseline={base:.3f}  now={live[vt]['tau']:.3f}  "
              f"changed={'YES' if abs(live[vt]['tau'] - base) > 1e-9 else 'NO'}")
    unchanged = [vt for vt in live
                 if abs(live[vt]["tau"] - weather._baselines[vt]["tau"]) < 1e-9]
    print(f"    tiers NOT reached by the weather write: "
          f"{unchanged or 'none — all reached'}")

    traci.close()


if __name__ == "__main__":
    from sim.sumo_activity import require_free
    require_free("verify_perception_fixes.py (CONDITION 1 evidence)")
    run(DEMO, 0.4, "TIERED DEMO")
    print()
    run(DEFAULT, None, "DEFAULT FILE (inertness control)")

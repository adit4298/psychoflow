"""Probe: does traci.vehicletype.setTau(<distribution id>) reach ALL members?

The previous probe showed traci.vehicletype accepts a vTypeDistribution id
without raising, and that getTau("bike") returned 0.900 -- bike.aggressive's
value, not cautious's 1.2 or normal's 1.0. That is consistent with SUMO
resolving a distribution id to ONE representative member.

If that is what happens, perception/weather.py's §7.4 contract silently breaks
under tiering: attach() would snapshot one member's baseline and set_state()
would rewrite one member, leaving the other tiers on clear-weather dynamics
while the twin reports "heavy_rain". No exception. That is exactly this repo's
named failure mode.

This probe writes a UNIQUE value through the distribution id and then reads
each sub-type back INDEPENDENTLY, with no intervening writes, so propagation
is measured rather than inferred from a readback of the same representative.

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
TIERS = ("bike.cautious", "bike.normal", "bike.aggressive")


def main() -> None:
    add_p = HERE / "_probe_types.add.xml"
    rou_p = HERE / "_probe_routes.rou.xml"

    cmd = [
        sumolib.checkBinary("sumo"),
        "-n", str(NET), "-a", str(add_p), "-r", str(rou_p),
        "--step-length", "1.0", "--seed", "7", "--lateral-resolution", "0.4",
        "--no-step-log", "--duration-log.disable", "--no-warnings",
        "--waiting-time-memory", "1000", "--time-to-teleport", "600",
    ]
    traci.start(cmd, label="prop")
    traci.switch("prop")
    traci.simulationStep()

    def snap(tag: str) -> None:
        print(f"  {tag}")
        print(f"    {'bike (distribution id)':<26} tau={traci.vehicletype.getTau('bike'):.4f}")
        for t in TIERS:
            print(f"    {t:<26} tau={traci.vehicletype.getTau(t):.4f}")

    print("=" * 72)
    print("AS DECLARED IN THE ADD-FILE (cautious 1.2 / normal 1.0 / aggressive 0.9)")
    snap("before any write:")

    print("\nNOW: traci.vehicletype.setTau('bike', 9.99)  <- the weather.py path")
    traci.vehicletype.setTau("bike", 9.99)
    snap("after writing through the DISTRIBUTION id:")

    reached = [t for t in TIERS if abs(traci.vehicletype.getTau(t) - 9.99) < 1e-6]
    print("\n" + "=" * 72)
    print(f"tiers actually reached by the distribution-id write: "
          f"{len(reached)}/{len(TIERS)}  {reached}")
    if len(reached) == len(TIERS):
        print("VERDICT: write PROPAGATES to all members -> weather.py is SAFE as-is.")
    else:
        print("VERDICT: write does NOT propagate -> weather.py SILENTLY BREAKS "
              "under tiering. §7.4 would report a weather state the untouched "
              "tiers are not actually driving under.")

    # Control: writing each member directly must always work, so a failure
    # above is about the distribution id and not about setTau in general.
    print("\ncontrol -- writing each member DIRECTLY:")
    for t in TIERS:
        traci.vehicletype.setTau(t, 7.77)
        print(f"    {t:<26} -> {traci.vehicletype.getTau(t):.4f}")

    traci.close()


if __name__ == "__main__":
    from sim.sumo_activity import require_free
    require_free("probe_weather_propagation.py (weather/distribution propagation)")
    main()

"""A/B one vType table against another for collisions and throughput.

The minimal harness behind the 2026-09-01 collision fix (see
`docs/MIXED_TRAFFIC_RESEARCH.md` §6.6): run one candidate vType file over the
pinned route file and report arrived count + collision events. Point it at two
tables in turn and the difference is the parameter change's actual effect.

Usage:
    python sim/mixed_traffic/check_fix.py sim/networks/vehicle_types_demo.add.xml
    python sim/mixed_traffic/check_fix.py sim/mixed_traffic/vehicle_types_demo_step1.add.xml

Recorded results on the shipped file (reproduced 2026-09-02 from this location):
    arrived=1737  collision_events=0

NOTE on `--collision.action warn`: it re-logs an overlapping pair on EVERY step
the overlap persists, so `collision_events` counts step-events, not incidents.
The pre-fix number was 14 events spanning ~19s of one sustained truck-on-truck
sideswipe, not 14 separate crashes.
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

NET = REPO_ROOT / "sim/networks/generated/corridor_432.net.xml"
ROUTES = HERE / "measure.rou.xml"


def main(add: Path) -> None:
    cmd = [
        sumolib.checkBinary("sumo"),
        "-n", str(NET), "-a", str(add), "-r", str(ROUTES),
        "--step-length", "1.0", "--seed", "7", "--lateral-resolution", "0.4",
        "--no-step-log", "--duration-log.disable",
        "--waiting-time-memory", "1000", "--time-to-teleport", "600",
        "--collision.action", "warn", "--collision.mingap-factor", "0",
    ]
    traci.start(cmd, label="fixcheck")
    traci.switch("fixcheck")

    n_arrived = 0
    events: list[tuple] = []
    t = 0.0
    while t < 1200:
        traci.simulationStep()
        t = traci.simulation.getTime()
        n_arrived += traci.simulation.getArrivedNumber()
        for c in traci.simulation.getCollisions():
            events.append((round(t, 1), c.collider, c.victim, c.lane))
    traci.close()

    print(f"ADD={add.name}")
    print(f"arrived={n_arrived}  collision_events={len(events)}")
    for e in events:
        print("  ", e)


if __name__ == "__main__":
    # CLAUDE.md §8 standing rule: this launches SUMO, so it must check the
    # Tier 1 beacon first. Inside the __main__ guard on purpose — the check
    # must fire on invocation, never on import.
    from sim.sumo_activity import require_free

    require_free("check_fix.py (vType-table collision/throughput A/B)")

    if len(sys.argv) < 2:
        raise SystemExit(
            "usage: python sim/mixed_traffic/check_fix.py <vehicle_types.add.xml>\n"
            "  e.g. sim/networks/vehicle_types_demo.add.xml"
        )
    main(Path(sys.argv[1]))

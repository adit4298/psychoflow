"""Which vehicle types collide under the tiered model, and where?

The tiered arm regressed 0.00% -> 0.107% collisions (2 vehicles of 1868)
against STEP 1's demo table on the same route file. STEP 1's own history says
guessing at a collision cause wastes passes: two lateral-parameter guesses were
both wrong there, and only inspecting the collision stream (3557 on-lane vs 8
junction) identified car-following as the mechanism. Same discipline here.

Standalone raw SUMO. No PsychoFlowEnv, no checkpoint.
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
DEMO = REPO_ROOT / "sim" / "networks" / "vehicle_types_demo.add.xml"
ROUTES = HERE / "measure.rou.xml"


def main() -> None:
    cmd = [
        sumolib.checkBinary("sumo"),
        "-n", str(NET), "-a", str(DEMO), "-r", str(ROUTES),
        "--step-length", "1.0", "--seed", "7", "--lateral-resolution", "0.4",
        "--no-step-log", "--duration-log.disable",
        "--waiting-time-memory", "1000", "--time-to-teleport", "600",
        "--collision.action", "warn", "--collision.mingap-factor", "0",
    ]
    traci.start(cmd, label="coll")
    traci.switch("coll")

    tier: dict[str, str] = {}
    events: list[dict] = []
    seen_pairs: set[tuple] = set()
    by_type: Counter[str] = Counter()

    t = 0.0
    while t < 1200:
        traci.simulationStep()
        t = traci.simulation.getTime()
        for vid in traci.vehicle.getIDList():
            if vid not in tier:
                tier[vid] = traci.vehicle.getTypeID(vid).split("@")[0]
        for c in traci.simulation.getCollisions():
            key = (c.collider, c.victim, round(c.time if hasattr(c, "time") else t))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            events.append({
                "t": t,
                "collider": c.collider, "collider_type": tier.get(c.collider, "?"),
                "victim": c.victim, "victim_type": tier.get(c.victim, "?"),
                "lane": c.lane, "pos": round(c.pos, 2),
                "type": c.type,
            })
            by_type[tier.get(c.collider, "?")] += 1

    traci.close()

    print("=" * 74)
    print(f"collision EVENTS: {len(events)}")
    for e in events:
        internal = "JUNCTION(internal)" if e["lane"].startswith(":") else "on-lane"
        print(f"  t={e['t']:7.1f}  {e['type']:<12} {internal:<18} lane={e['lane']}")
        print(f"      collider {e['collider']:<12} ({e['collider_type']})")
        print(f"      victim   {e['victim']:<12} ({e['victim_type']})")
    print("\ncollider type counts:", dict(by_type))
    on_lane = sum(1 for e in events if not e["lane"].startswith(":"))
    print(f"on-lane: {on_lane}   junction/internal: {len(events) - on_lane}")


if __name__ == "__main__":
    from sim.sumo_activity import require_free
    require_free("probe_collisions.py (tiered-model collision detail)")
    main()

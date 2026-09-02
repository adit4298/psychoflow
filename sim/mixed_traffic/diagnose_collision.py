"""Focused, bounded diagnosis of the 0.00% -> 0.107% truck-on-truck collision
regression (2 vehicles, ONE event, on-lane J2_J1_0, t~252-265).

Two hypotheses already tested and REFUTED (see BUILD_LOG scratch entry):
  - truck actionStepLength=1.5 -> removing it: still 0.107%
  - bike tau 0.9->1.0 -> STEP 1's table with tau 1.0: still 0.00%

This pulls the actual collision report plus a step-by-step trace of BOTH
vehicles' real state (position, speed, lane, leader/gap, accel proxy) for a
window before the event, on the shipped tiered file, and prints it raw. No
guessing past what the trace shows.

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
TIERED = REPO_ROOT / "sim" / "networks" / "vehicle_types_demo.add.xml"
ROUTES = HERE / "measure.rou.xml"

TRACE_WINDOW_S = 40.0  # seconds of history to keep before a collision


def main() -> None:
    cmd = [
        sumolib.checkBinary("sumo"),
        "-n", str(NET), "-a", str(TIERED), "-r", str(ROUTES),
        "--step-length", "1.0", "--seed", "7", "--lateral-resolution", "0.4",
        "--no-step-log", "--duration-log.disable",
        "--waiting-time-memory", "1000", "--time-to-teleport", "600",
        "--collision.action", "warn", "--collision.mingap-factor", "0",
    ]
    traci.start(cmd, label="diag")
    traci.switch("diag")

    # rolling per-vehicle state history: vid -> list of (t, dict)
    history: dict[str, list[tuple[float, dict]]] = {}
    tier: dict[str, str] = {}
    collisions_found: list[dict] = []

    t = 0.0
    while t < 1200 and not collisions_found:
        traci.simulationStep()
        t = traci.simulation.getTime()

        present = traci.vehicle.getIDList()
        for vid in present:
            if vid not in tier:
                tier[vid] = traci.vehicle.getTypeID(vid).split("@")[0]
            lane = traci.vehicle.getLaneID(vid)
            pos = traci.vehicle.getLanePosition(vid)
            speed = traci.vehicle.getSpeed(vid)
            accel = traci.vehicle.getAcceleration(vid)
            x, y = traci.vehicle.getPosition(vid)
            angle = traci.vehicle.getAngle(vid)
            leader = traci.vehicle.getLeader(vid, 100.0)
            signals = traci.vehicle.getSignals(vid)
            lc_state_left = traci.vehicle.getLaneChangeState(vid, 1)[0]
            lc_state_right = traci.vehicle.getLaneChangeState(vid, -1)[0]
            rec = {
                "lane": lane, "pos": round(pos, 3), "speed": round(speed, 3),
                "accel": round(accel, 3), "x": round(x, 2), "y": round(y, 2),
                "angle": round(angle, 1),
                "leader": (leader[0], round(leader[1], 3)) if leader else None,
                "signals": signals,
                "lcLeft": lc_state_left, "lcRight": lc_state_right,
            }
            history.setdefault(vid, []).append((t, rec))
            # trim to trace window
            cutoff = t - TRACE_WINDOW_S
            hist = history[vid]
            while hist and hist[0][0] < cutoff:
                hist.pop(0)

        for c in traci.simulation.getCollisions():
            collisions_found.append({
                "t": t,
                "collider": c.collider, "victim": c.victim,
                "colliderType": tier.get(c.collider, "?"),
                "victimType": tier.get(c.victim, "?"),
                "lane": c.lane, "pos": round(c.pos, 2),
                "type": c.type,
                "colliderSpeed": round(c.colliderSpeed, 3) if hasattr(c, "colliderSpeed") else None,
                "victimSpeed": round(c.victimSpeed, 3) if hasattr(c, "victimSpeed") else None,
            })

    traci.close()

    print("=" * 90)
    if not collisions_found:
        print("NO COLLISION occurred in this run (1200s). Nothing to diagnose.")
        return

    for c in collisions_found:
        print(f"COLLISION at t={c['t']}: type={c['type']} lane={c['lane']} pos={c['pos']}")
        print(f"  collider={c['collider']} ({c['colliderType']})  speed={c['colliderSpeed']}")
        print(f"  victim  ={c['victim']} ({c['victimType']})  speed={c['victimSpeed']}")
        print()
        for role, vid in (("COLLIDER", c["collider"]), ("VICTIM", c["victim"])):
            print(f"--- {role} {vid} ({tier.get(vid, '?')}) state history ---")
            hist = history.get(vid, [])
            print(f"{'t':>7} {'lane':<14} {'pos':>7} {'speed':>7} {'accel':>7} "
                  f"{'x':>8} {'y':>8} {'angle':>6} {'leader(id,gap)':<22} {'sig':>4} lcL lcR")
            for tt, rec in hist:
                leader_str = f"{rec['leader']}" if rec["leader"] else "None"
                print(f"{tt:7.1f} {rec['lane']:<14} {rec['pos']:7.2f} {rec['speed']:7.3f} "
                      f"{rec['accel']:7.3f} {rec['x']:8.2f} {rec['y']:8.2f} {rec['angle']:6.1f} "
                      f"{leader_str:<22} {rec['signals']:4d} {rec['lcLeft']:4d} {rec['lcRight']:4d}")
            print()


if __name__ == "__main__":
    from sim.sumo_activity import require_free
    require_free("diagnose_collision.py (truck-truck collision root-cause trace)")
    main()

"""ITEM 4 - ambulance PREVIEW scenario. Design-review convenience only.

Puts an ambulance on the corridor at ~35s so the §10 emergency override and the
ambulance's deliberately-predictable driving contrast can be watched without
sitting through a long wait.

THIS IS NOT THE DEMO SCRIPT AND DOES NOT ALTER IT.
  - It writes its own route file into the scratchpad. `sim/routes/` and
    `sim/scenario_generator.py`'s defaults are untouched.
  - The real rehearsal timing (§19) still comes from the normal
    `spawn_emergencies` draw in `ScenarioConfig`, which puts the ambulance in a
    (300, 2400) s window. Nothing here changes that window.
  - It launches sumo-gui directly on the static netconvert TLS program, so it
    shows the DRIVING MODEL, not the RL policy. For the policy-driven view, run
    the backend with --demo-driving.

Usage:
    python preview_ambulance.py [--baseline]     # --baseline = default vTypes
"""

from __future__ import annotations

import argparse
import random
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# ^ Derived, not hardcoded. This harness ran from a scratchpad during the
#   mixed-traffic work and carried an absolute path to one machine's checkout.
#   parents[2] is the repo root from sim/mixed_traffic/. See README.md here.
SP = Path(__file__).parent
sys.path.insert(0, str(REPO))

import sumolib  # noqa: E402

from sim.scenario_generator import write_route_file  # noqa: E402
from perception.lane_sensor import WAITING_TIME_MEMORY_S  # noqa: E402
from env.psychoflow_env import TIME_TO_TELEPORT_S  # noqa: E402

# Early enough to watch immediately; late enough that a queue has formed for it
# to cut through, which is the whole point of looking at it.
PREVIEW_DEPARTS = (35.0, 150.0, 300.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true",
                    help="Default lane-disciplined vTypes instead of the demo model")
    ap.add_argument("--delay", type=int, default=300)
    ap.add_argument("--seconds", type=int, default=0,
                    help="0 = run until closed")
    args = ap.parse_args()

    route = SP / "preview_ambulance.rou.xml"
    _, dens, emerg = write_route_file(
        route, random.Random(7), emergency_departures=PREVIEW_DEPARTS
    )
    print(f"route file : {route}")
    print(f"ambulances : {[(e['route'], e['depart_s']) for e in emerg]}")

    settings = SP / "preview_gui.settings.xml"
    settings.write_text(
        '<viewsettings>\n'
        '    <scheme name="psychoflow-preview"/>\n'
        '    <viewport zoom="320" x="450" y="150"/>\n'
        f'    <delay value="{args.delay}"/>\n'
        '</viewsettings>\n'
    )

    vtypes = (REPO / "sim" / "networks" / "vehicle_types.add.xml" if args.baseline
              else REPO / "sim" / "networks" / "vehicle_types_demo.add.xml")
    cmd = [
        sumolib.checkBinary("sumo-gui"),
        "-n", str(REPO / "sim" / "networks" / "generated" / "corridor_432.net.xml"),
        "-a", str(vtypes),
        "-r", str(route),
        "--gui-settings-file", str(settings),
        "--waiting-time-memory", str(WAITING_TIME_MEMORY_S),
        "--time-to-teleport", str(TIME_TO_TELEPORT_S),
        "--step-length", "1.0",
        "--start", "--delay", str(args.delay),
        "--quit-on-end", "false",
        "--window-size", "1600,900",
        "--no-warnings",
    ]
    if not args.baseline:
        cmd += ["--lateral-resolution", "0.4",
                "--collision.action", "warn",
                "--collision.mingap-factor", "0"]
    if args.seconds:
        cmd += ["--end", str(args.seconds)]

    print(f"vTypes     : {vtypes.name}")
    print(f"mode       : {'BASELINE (lane-disciplined)' if args.baseline else 'DEMO (sublane mixed traffic)'}")
    print("launching sumo-gui...")
    subprocess.Popen(cmd)


if __name__ == "__main__":
    from sim.sumo_activity import require_free
    require_free("preview_ambulance.py (design-review preview)")
    main()

"""Launch the FULL corridor in sumo-gui under the CURRENT tiered mixed-traffic
demo-driving model, for the mandatory human sign-off watch.

WHY THIS EXISTS
---------------
STEP 1 and its refinement share ONE combined done-bar (CLAUDE.md §10), and it
is not closed by any test suite — it is closed by a human watching this and
confirming the behaviour looks right. Every number in
`docs/MIXED_TRAFFIC_RESEARCH.md` is a summary statistic; the failure modes that
matter for a demo are visual. This script exists so that watch is one command
and is identical every time, rather than a hand-assembled sumo-gui invocation
that quietly differs from the one the measurements were taken under.

WHAT IT SHOWS — the REAL corridor, not a reduced view
-----------------------------------------------------
All three junctions J1 -> J2 -> J3 (lane counts 4 / 3 / 2), BOTH corridor
directions (W->E and E->W), and ALL SIX cross-street movements (N->S and S->N
at each junction). Eight flows total. This is the same route shape every
mixed-traffic measurement used.

WHAT DRIVES THE SIGNALS
-----------------------
netconvert's own STATIC traffic-light programs — NOT the trained policy, NOT
Tier 0, and no TraCI connection at all. That is deliberate and it is the same
choice every measurement harness made:

  * This watch judges the DRIVING MODEL (how vehicles behave), not the
    controller. Putting a policy in the loop would mean watching two things at
    once and being unable to attribute what you see to either.
  * No TraCI means nothing steps the simulation but you. The window opens and
    WAITS. It cannot run to completion and close before you sit down.
  * It also means this script cannot perturb any recorded checkpoint figure —
    it constructs no PsychoFlowEnv and loads no checkpoint.

If you want to watch the trained policy instead, that is the backend:
`venv/Scripts/python.exe -m backend.main --demo-driving` (default OFF).

THE WINDOW-CLOSING FIX
----------------------
`--end` together with `--quit-on-end false` is the already-diagnosed fix for
sumo-gui exiting the moment the run finishes. Both are required: `--end` bounds
the run, `--quit-on-end false` keeps the window up afterwards so the final
state is still on screen. `--start` is deliberately NOT passed, so playback
does not begin on its own.

BEACON
------
Calls `require_free()` inside the __main__ guard per CLAUDE.md §8's standing
rule — this launches SUMO, and a concurrent SUMO owner (a training run, the
backend) must not be trampled.
"""

from __future__ import annotations

import argparse
import random
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import sumolib  # noqa: E402

from sim.scenario_generator import write_route_file  # noqa: E402

NET_DIR = REPO_ROOT / "sim" / "networks"
GENERATED = NET_DIR / "generated"

# The demo-only tiered driving model. NOT sim/networks/vehicle_types.add.xml —
# that is the default file every training run and every recorded number uses.
DEMO_VTYPES = NET_DIR / "vehicle_types_demo.add.xml"
GUI_SETTINGS = NET_DIR / "demo_gui_settings.xml"

# Matches backend/sim_runner.py's DEMO_LATERAL_RESOLUTION. This flag is what
# actually enables SL2015 sublane driving — without it the demo vType file
# loads but the lateral behaviour it describes never happens.
LATERAL_RESOLUTION = 0.4

# Both from CLAUDE.md §8's standing rules. Kept here even though no reward or
# starvation metric is computed in this view, so the vehicle dynamics match the
# ones every measurement was taken under.
WAITING_TIME_MEMORY_S = 1000
TIME_TO_TELEPORT_S = 600

STEP_LENGTH_S = 1.0


def build_command(
    *,
    topology: str,
    seed: int,
    end_s: float,
    delay_ms: int,
    routes: Path,
) -> list[str]:
    net = GENERATED / f"corridor_{topology}.net.xml"
    if not net.exists():
        raise SystemExit(
            f"Network not found: {net}\n"
            f"Generate it first, e.g.:\n"
            f"  python sim/networks/generate_corridor.py "
            f"--j1 {topology[0]} --j2 {topology[1]} --j3 {topology[2]}"
        )
    if not DEMO_VTYPES.exists():
        raise SystemExit(
            f"Demo vType file not found: {DEMO_VTYPES}\n"
            "This file is UNCOMMITTED (CLAUDE.md CURRENT STATUS) — if this is a "
            "fresh clone it does not exist yet and the mixed-traffic work is gone."
        )

    cmd = [
        sumolib.checkBinary("sumo-gui"),
        "-n", str(net),
        "-a", str(DEMO_VTYPES),
        "-r", str(routes),
        "--step-length", str(STEP_LENGTH_S),
        "--seed", str(seed),
        # Enables SL2015 sublane driving.
        "--lateral-resolution", str(LATERAL_RESOLUTION),
        # Keep a colliding vehicle on the road instead of SUMO's default
        # `teleport`, which silently REMOVES it. minGap is a preference in
        # mixed traffic, not a safety envelope, so 0 counts only real overlap.
        "--collision.action", "warn",
        "--collision.mingap-factor", "0",
        "--waiting-time-memory", str(WAITING_TIME_MEMORY_S),
        "--time-to-teleport", str(TIME_TO_TELEPORT_S),
        # THE WINDOW-CLOSING FIX — both halves are required.
        "--end", str(end_s),
        "--quit-on-end", "false",
        "--delay", str(delay_ms),
        "--no-step-log",
        "--duration-log.disable",
    ]
    if GUI_SETTINGS.exists():
        cmd += ["-g", str(GUI_SETTINGS)]
    return cmd


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--topology", default="432",
                    help="Three digits, j1j2j3 lane counts. Default 432 (the demo corridor).")
    ap.add_argument("--seed", type=int, default=7,
                    help="Scenario + SUMO seed. Default 7, matching every measurement.")
    ap.add_argument("--end", type=float, default=1200.0,
                    help="Simulated seconds. Default 1200, matching the measurement harnesses.")
    ap.add_argument("--delay", type=int, default=250,
                    help="sumo-gui playback delay in ms. COSMETIC — wall-clock only, "
                         "zero effect on the simulation. Default 250.")
    ap.add_argument("--routes", default=None,
                    help="Reuse an existing route file instead of writing a fresh one.")
    ap.add_argument("--print-only", action="store_true",
                    help="Print the command and exit without launching.")
    args = ap.parse_args()

    if args.routes:
        routes = Path(args.routes)
    else:
        routes = REPO_ROOT / "sim" / "routes" / f"demo_gui_{args.topology}_seed{args.seed}.rou.xml"
        # Same generator training uses, so the traffic mix and the eight flows
        # are the real ones. randomize_density stays False: this watch judges
        # driving behaviour, and a density draw would add a second variable.
        write_route_file(
            routes,
            random.Random(args.seed),
            flows_end_s=args.end,
            randomize_density=False,
        )

    cmd = build_command(
        topology=args.topology,
        seed=args.seed,
        end_s=args.end,
        delay_ms=args.delay,
        routes=routes,
    )

    print("=" * 78)
    print("MIXED-TRAFFIC DEMO WATCH — full corridor, tiered heterogeneity")
    print("=" * 78)
    print(f"  network   : corridor_{args.topology} (J1={args.topology[0]} / "
          f"J2={args.topology[1]} / J3={args.topology[2]} lanes per approach)")
    print(f"  vTypes    : {DEMO_VTYPES.name}  <-- TIERED demo model, not the default")
    print(f"  routes    : {routes.name}  (8 flows: 2 corridor + 6 cross)")
    print(f"  signals   : netconvert STATIC programs (no policy, no TraCI)")
    print(f"  sublane   : --lateral-resolution {LATERAL_RESOLUTION}")
    print(f"  gui       : {GUI_SETTINGS.name if GUI_SETTINGS.exists() else 'NOT FOUND — set colour-by-type MANUALLY'}")
    print()
    print("  " + " ".join(cmd))
    print("=" * 78)

    if args.print_only:
        return

    subprocess.Popen(cmd, cwd=str(REPO_ROOT))
    print("\nsumo-gui launched. It is LOADED AND PAUSED — press Play when ready.")


if __name__ == "__main__":
    from sim.sumo_activity import require_free

    require_free("run_demo_gui.py (human sign-off watch, mixed-traffic demo model)")
    main()

"""Launch the corridor in sumo-gui under the tiered mixed-traffic demo-driving
model, for the mandatory human sign-off watch.

WHY THIS EXISTS
---------------
STEP 1 and its refinement share ONE combined done-bar (CLAUDE.md §10), and it is
not closed by any test suite — it is closed by a human watching this and
confirming the behaviour looks right. Every number in
`docs/MIXED_TRAFFIC_RESEARCH.md` is a summary statistic; the failure modes that
matter for a demo are visual.

REVISED 2026-09-02 after the first watch, which reported two real problems.
Both were diagnosed by measurement before anything was changed:

  "I cannot see much of the bikes"
      Two causes. The view auto-fitted the whole 900x300m corridor, where a
      1.8m x 0.65m bike is a few pixels; and colour was "by type", an arbitrary
      hash. Fixed: the viewport now defaults to ONE junction (--focus), the
      vTypes carry an explicit colour palette, and vehicles are drawn larger.

  "the roads look very congested"
      NOT the driving model, and NOT demand. Measured:
        * Demand REFUTED — halted% was 34-35% at EVERY density from full
          training load down to 35% of it. Cutting traffic does not decongest;
          it only removes bikes from the screen (19.6 -> 6.0 on road).
        * Signal plan CONFIRMED — same corridor, demand and seed, controller
          swapped:  fixed timer 29.1% halted / peak 60.4% / 21.1 km/h
                    Tier 0       8.0% halted / peak 15.5% / 29.2 km/h
      The old watch ran netconvert's STATIC fixed-timer TLS, the dumbest
      controller in the project. It now runs Tier 0 by default.

WHAT IT SHOWS
-------------
The real corridor J1 -> J2 -> J3 (lane counts 4 / 3 / 2), BOTH corridor
directions (W->E, E->W) and ALL SIX cross-street movements (N->S, S->N at each
junction) — eight flows, the same route shape every mixed-traffic measurement
used. `--focus` only changes where the CAMERA sits; the whole corridor is always
simulated, and `--focus all` frames all three junctions at once.

COLOUR KEY
----------
    GREEN  two-wheeler   dark=cautious  bright=normal  LIME=aggressive
    AMBER  auto          pale=cautious  amber=normal   ORANGE=aggressive
    BLUE   car           blue=normal    DEEP BLUE=aggressive
    GREY   truck
    RED    ambulance

Anything green is a two-wheeler. If the model works you should see green
filtering forward past stopped ambers and blues at a queue front, and the lime
ones doing it hardest.

CONTROLLERS
-----------
    tier0   (default) the §9.1 fairness-first rule-based controller — what the
            demo runs in manual mode. Keeps traffic moving so behaviour is
            visible. Deterministic, needs no checkpoint.
    (There is deliberately no 'policy' option — see the note in
     run_controlled(). For the deployed Stage 4 checkpoint use the backend,
     `python -m backend.main --demo-driving`, which drives PsychoFlowEnv and
     therefore §10's REAL safety validator.)
    fixed   a dumb cyclic timer. The old behaviour, kept only so the congestion
            contrast above can be reproduced. Not for judging the driving model.

`tier0` drives via TraCI, so this script stays running and steps the
simulation. sumo-gui still opens PAUSED — press Play when ready. `--autoplay`
starts it immediately.

BEACON
------
Calls `require_free()` inside the __main__ guard per CLAUDE.md §8's standing
rule: this launches SUMO and must not trample a training run or the backend.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

NET_DIR = REPO_ROOT / "sim" / "networks"
GENERATED = NET_DIR / "generated"

# The demo-only tiered driving model. NOT sim/networks/vehicle_types.add.xml —
# that is the default file every training run and every recorded number uses.
DEMO_VTYPES = NET_DIR / "vehicle_types_demo.add.xml"
GUI_SETTINGS = NET_DIR / "demo_gui_settings.xml"

# Matches backend/sim_runner.py's DEMO_LATERAL_RESOLUTION.
LATERAL_RESOLUTION = 0.4

DEFAULT_CHECKPOINT = (
    REPO_ROOT / "training" / "checkpoints" / "stage4"
    / "psychoflow_stage4_153600_steps_final.zip"
)

# Junction coordinates come from the NET FILE, never from generate_corridor.py's
# parameters — netconvert shifts the network by [150,150] (CLAUDE.md §8).
# `all` is the corridor centre.
FOCUS_ZOOM = {"all": 110.0, "junction": 520.0}

# Drawn-size multiplier per camera. TRUE SCALE (1.0) at junction zoom, because
# watch 2 showed oversizing causes a worse problem than it solves: at 4x a car
# is drawn 18m long and a truck 30m, so each body is painted straight through
# the two or three vehicles genuinely ahead of it, and the smaller vehicle
# showing through in front reads as the vehicle's own "wheels running ahead of
# it". At 1.0 nothing overlaps that is not really overlapping. The whole-
# corridor view still needs some help or a bike is sub-pixel.
FOCUS_EXAGGERATION = {"all": 2.5, "junction": 1.0}


def _junction_coords(net_path: Path) -> dict[str, tuple[float, float]]:
    import sumolib

    net = sumolib.net.readNet(str(net_path))
    return {j: net.getNode(j).getCoord() for j in ("J1", "J2", "J3")}


def write_view_settings(dest: Path, net_path: Path, focus: str,
                        zoom: float | None, exaggeration: float | None,
                        delay_ms: int) -> Path:
    """Copy the base settings and append a <viewport> for the requested focus."""
    text = GUI_SETTINGS.read_text(encoding="utf-8")

    coords = _junction_coords(net_path)
    if focus == "all":
        xs = [c[0] for c in coords.values()]
        ys = [c[1] for c in coords.values()]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        z = zoom if zoom is not None else FOCUS_ZOOM["all"]
    else:
        cx, cy = coords[focus]
        z = zoom if zoom is not None else FOCUS_ZOOM["junction"]

    if exaggeration is None:
        exaggeration = FOCUS_EXAGGERATION["all" if focus == "all" else "junction"]
    text = text.replace('vehicle_exaggeration="1.00"',
                        f'vehicle_exaggeration="{exaggeration:.2f}"')
    text = text.replace('<delay value="250"/>', f'<delay value="{delay_ms}"/>')

    viewport = (f'\n    <viewport zoom="{z:.2f}" x="{cx:.2f}" y="{cy:.2f}" '
                f'angle="0.00"/>\n')
    text = text.replace("</viewsettings>", viewport + "</viewsettings>")

    dest.write_text(text, encoding="utf-8")
    # SUMO's parser is lenient — it accepted a malformed settings file (an
    # illegal '--' inside an XML comment) for an entire session without a word.
    # Fail loudly here instead of shipping a view that silently half-applies.
    import xml.etree.ElementTree as ET
    try:
        ET.parse(dest)
    except ET.ParseError as e:
        raise SystemExit(
            "generated view settings are not well-formed XML: "
            f"{e}\n  {dest}\n"
            "check sim/networks/demo_gui_settings.xml")
    return dest


def build_command(*, net: Path, routes: Path, seed: int, end_s: float,
                  settings: Path, autoplay: bool, traci_mode: bool) -> list[str]:
    import sumolib

    cmd = [
        sumolib.checkBinary("sumo-gui"),
        "-n", str(net),
        "-a", str(DEMO_VTYPES),
        "-r", str(routes),
        "--step-length", "1.0",
        "--seed", str(seed),
        "--lateral-resolution", str(LATERAL_RESOLUTION),
        "--collision.action", "warn",
        "--collision.mingap-factor", "0",
        "--waiting-time-memory", "1000",
        "--time-to-teleport", "600",
        "--quit-on-end", "false",
        "-g", str(settings),
        "--no-step-log",
        "--duration-log.disable",
    ]
    if not traci_mode:
        # THE WINDOW-CLOSING FIX: --end bounds the run, --quit-on-end false
        # keeps the window up afterwards. Under TraCI the driver loop bounds it
        # instead, and passing --end as well can end the run out from under it.
        cmd += ["--end", str(end_s)]
    if autoplay:
        cmd += ["--start"]
    return cmd


def run_uncontrolled(cmd: list[str]) -> None:
    """`fixed` arm: launch sumo-gui standalone on netconvert's static TLS."""
    import subprocess

    subprocess.Popen(cmd, cwd=str(REPO_ROOT))
    print("\nsumo-gui launched (STATIC fixed-timer signals).")
    print("It is LOADED AND PAUSED — press Play when ready.")


def run_controlled(cmd: list[str], controller: str, end_s: float,
                   checkpoint: Path | None,
                   spawn_times: list[float] | None = None,
                   amb_route: str = "r_ns2",
                   when_blocked: bool = True) -> None:
    """`tier0` / `policy`: drive the corridor through TraCI so the signals are
    a real controller rather than netconvert's static plan."""
    import numpy as np
    import traci

    from agents.rule_based import Tier0Controller
    from twin.digital_twin import CORRIDOR_JUNCTIONS

    label = "demo_watch"
    traci.start(cmd, label=label)
    traci.switch(label)

    tls = list(traci.trafficlight.getIDList())
    print(f"\nsumo-gui launched under TraCI, controller = {controller}.")
    print("It is LOADED AND PAUSED — press Play when ready.")
    print("(The window advances only while this script runs. Ctrl-C to stop.)\n")

    # NOTE: there is deliberately NO 'policy' controller here, and adding one
    # naively would be worse than not having it. Running the Stage 4 checkpoint
    # requires building §9.2's exact observation vector and action mask from a
    # DigitalTwin snapshot — that machinery lives in PsychoFlowEnv, which owns
    # its own SUMO process and cannot drive the sumo-gui process this script
    # started. An earlier revision of this file accepted --controller policy,
    # loaded the checkpoint, and then never called predict(): it silently ran
    # the pressure rule while reporting itself as the trained policy. Removed
    # 2026-09-02. To watch the deployed policy, use the backend
    # (`python -m backend.main --demo-driving`) or run_tier0_episode.py, both of
    # which go through PsychoFlowEnv and therefore through §10's real validator.

    # --- ambulance spawning -------------------------------------------------
    # Press 'a' in THIS terminal to drop an ambulance in, or use --ambulance-at
    # for scheduled ones. Keypress needs a real terminal: if this script was
    # started in the background its stdin is not a console and only the
    # scheduled spawns will work.
    try:
        import msvcrt
        keys_available = sys.stdin is not None and sys.stdin.isatty()
    except ImportError:
        msvcrt, keys_available = None, False

    amb_n = 0
    pending_spawns = sorted(spawn_times or [])
    if keys_available:
        print("  >>> press 'a' in this terminal to spawn an AMBULANCE "
              "(red), 'q' to quit <<<\n")
    elif pending_spawns:
        print(f"  ambulance(s) scheduled at t = "
              f"{', '.join(f'{s:.0f}s' for s in pending_spawns)}\n")
    else:
        print("  (no terminal for keypresses and no --ambulance-at given: "
              "no ambulance will appear)\n")

    # Entry edge of each spawn route, and the junction it first meets. Used by
    # --when-blocked to hold the spawn until the light is actually AGAINST the
    # ambulance, which is the only case where an override is visible.
    ROUTE_ENTRY = {
        "r_ns1": ("N1_J1", "J1"), "r_sn1": ("S1_J1", "J1"),
        "r_ns2": ("N2_J2", "J2"), "r_sn2": ("S2_J2", "J2"),
        "r_ns3": ("N3_J3", "J3"), "r_sn3": ("S3_J3", "J3"),
        "r_we": ("W1_J1", "J1"), "r_ew": ("E3_J3", "J3"),
    }

    def entry_is_blocked() -> bool:
        """True when the entry junction's CURRENT green does not serve the
        ambulance's entry edge — i.e. an override will have to fire."""
        entry = ROUTE_ENTRY.get(amb_route)
        if entry is None:
            return True
        edge, tl = entry
        if tl not in tls:
            return True
        phases = traci.trafficlight.getAllProgramLogics(tl)[0].phases
        cur = traci.trafficlight.getPhase(tl)
        state = phases[cur].state
        controlled = traci.trafficlight.getControlledLanes(tl)
        green_now = {ln for i, ln in enumerate(controlled)
                     if i < len(state) and state[i] in "Gg"}
        return not any(ln.startswith(edge + "_") for ln in green_now)

    def spawn_ambulance(now: float) -> None:
        nonlocal amb_n
        amb_n += 1
        vid = f"AMBULANCE.{amb_n}"
        try:
            traci.vehicle.add(vid, routeID=amb_route, typeID="ambulance",
                              depart="now", departLane="best",
                              departPos="free", departSpeed="max")
            # Make it unmissable regardless of the colour scheme.
            traci.vehicle.setColor(vid, (255, 0, 0, 255))
            print(f"  t={now:7.1f}s  AMBULANCE SPAWNED  id={vid} "
                  f"route={amb_route}")
        except traci.exceptions.TraCIException as e:
            print(f"  t={now:7.1f}s  ambulance spawn refused: {e}")

    # A minimal green-slot rotator driven by per-junction queue pressure. This
    # deliberately does NOT construct a PsychoFlowEnv: the env owns its own SUMO
    # process, and this script's whole job is to drive the GUI process it just
    # started. It reproduces Tier 0's *shape* (serve the most-pressured phase,
    # respect a minimum green) rather than importing its scoring wholesale.
    MIN_GREEN_S = 10.0
    last_switch = {t: -1e9 for t in tls}
    amb_lanes_seen: set[str] = set()
    already_green_logged: set[str] = set()
    t = 0.0
    try:
        while t < end_s and traci.simulation.getMinExpectedNumber() > 0:
            traci.simulationStep()
            t = traci.simulation.getTime()

            # --- spawn triggers ---------------------------------------------
            if keys_available and msvcrt.kbhit():
                ch = msvcrt.getch().decode("utf-8", "ignore").lower()
                if ch == "a":
                    if when_blocked and not entry_is_blocked():
                        pending_spawns.append(t)   # fire at the next red
                        pending_spawns.sort()
                        print(f"  t={t:7.1f}s  queued — waiting for the light at "
                              f"{amb_route} to go against it (--no-when-blocked "
                              f"to spawn instantly)")
                    else:
                        spawn_ambulance(t)
                elif ch == "q":
                    print("  quit requested.")
                    break
            while pending_spawns and t >= pending_spawns[0]:
                if when_blocked and not entry_is_blocked():
                    break  # due, but the light is with it — wait for a red
                pending_spawns.pop(0)
                spawn_ambulance(t)

            if controller == "fixed":
                continue

            # --- which lanes currently hold an ambulance? --------------------
            # base type, not the raw id: under the demo model getTypeID() returns
            # a concrete tier member for the everyday classes. Ambulance is
            # deliberately untiered, but resolve anyway so this cannot silently
            # miss one if that ever changes.
            amb_lanes = {
                traci.vehicle.getLaneID(v)
                for v in traci.vehicle.getIDList()
                if traci.vehicle.getTypeID(v).split(".")[0] == "ambulance"
            }
            amb_lanes = {ln for ln in amb_lanes if ln and not ln.startswith(":")}
            for ln in amb_lanes - amb_lanes_seen:
                print(f"  t={t:7.1f}s  ambulance detected on lane {ln}")
            if not amb_lanes:
                # corridor is clear again — next ambulance reports afresh
                already_green_logged.clear()
            amb_lanes_seen = amb_lanes

            for tl in tls:
                controlled = traci.trafficlight.getControlledLanes(tl)
                logic = traci.trafficlight.getAllProgramLogics(tl)[0]
                phases = logic.phases
                cur = traci.trafficlight.getPhase(tl)
                if "y" in phases[cur].state.lower():
                    continue

                def served_lanes(state: str) -> set[str]:
                    return {ln for i, ln in enumerate(controlled)
                            if i < len(state) and state[i] in "Gg"}

                # ---- RULE 1: EMERGENCY, tested FIRST ------------------------
                # §10's precedence is emergency BEFORE the starvation/pressure
                # rule, and it BYPASSES min-green — an ambulance must not wait
                # out an unrelated green. This mirrors the documented rule; the
                # AUTHORITATIVE implementation is safety/validator.py, which
                # runs inside env.step() and is what the backend and every
                # measured result use. This loop drives sumo-gui directly and
                # therefore cannot call it.
                here = amb_lanes & set(controlled)
                if here:
                    target = None
                    for idx, ph in enumerate(phases):
                        if "y" in ph.state.lower():
                            continue
                        if here & served_lanes(ph.state):
                            target = idx
                            break
                    if target is not None and target != cur:
                        traci.trafficlight.setPhase(tl, target)
                        last_switch[tl] = t
                        print(f"  t={t:7.1f}s  EMERGENCY OVERRIDE at {tl}: "
                              f"phase {cur} -> {target} for {sorted(here)}")
                    elif target is not None and tl not in already_green_logged:
                        # SAY THIS OUT LOUD. An already-green emergency needs no
                        # override (§10's own unit scenario 6 pins that), but if
                        # the log is silent a watcher cannot tell "not needed"
                        # from "failed to fire" — and a silent no-op that looks
                        # like success is this repo's signature failure mode.
                        already_green_logged.add(tl)
                        print(f"  t={t:7.1f}s  no override needed at {tl}: "
                              f"phase {cur} ALREADY serves {sorted(here)}")
                    elif target is None:
                        print(f"  t={t:7.1f}s  WARNING: no green phase at {tl} "
                              f"serves {sorted(here)} — check the topology")
                    continue  # emergency outranks everything below

                # ---- RULE 2: queue pressure ---------------------------------
                if t - last_switch[tl] < MIN_GREEN_S:
                    continue
                best, best_score = cur, -1.0
                for idx, ph in enumerate(phases):
                    st = ph.state
                    if "y" in st.lower():
                        continue
                    served = served_lanes(st)
                    score = sum(traci.lane.getLastStepHaltingNumber(ln) for ln in served)
                    score += 0.4 * sum(traci.lane.getWaitingTime(ln) for ln in served) / 60.0
                    if score > best_score:
                        best, best_score = idx, score
                if best != cur:
                    traci.trafficlight.setPhase(tl, best)
                    last_switch[tl] = t
    except KeyboardInterrupt:
        print("\nstopped by user (Ctrl-C).")
    except (traci.exceptions.FatalTraCIError, traci.exceptions.TraCIException) as e:
        # Closing the sumo-gui window is a NORMAL way to end a watch, not a
        # failure. It surfaces as FatalTraCIError("Connection closed by SUMO."),
        # and note FatalTraCIError does NOT subclass TraCIException — they are
        # siblings under Exception (verified on SUMO 1.27.1), so catching only
        # TraCIException lets a window-close escape as a traceback with exit 1.
        # That is exactly what happened on the 2026-09-02 watch.
        print(f"\nsumo-gui closed — watch ended. ({type(e).__name__})")
    finally:
        try:
            traci.close()
        except Exception:
            pass


def main() -> None:
    import random

    from sim.scenario_generator import write_route_file

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--topology", default="432",
                    help="Three digits, j1j2j3 lane counts. Default 432.")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--end", type=float, default=1200.0,
                    help="Simulated seconds. Default 1200.")
    ap.add_argument("--controller", choices=("tier0", "fixed"),
                    default="tier0",
                    help="Signal controller. Default tier0 — 'fixed' is the old "
                         "static plan and is 3.6x more congested. There is no "
                         "'policy' option: running the trained checkpoint needs "
                         "PsychoFlowEnv's observation builder, so use the backend "
                         "for that (see the module docstring).")
    ap.add_argument("--focus", default="J2",
                    choices=("J1", "J2", "J3", "all"),
                    help="Where the CAMERA sits. Default J2 (the middle "
                         "junction). 'all' frames the whole corridor but makes "
                         "bikes very small. The whole corridor is always simulated.")
    ap.add_argument("--zoom", type=float, default=None,
                    help="Override the viewport zoom (higher = closer). "
                         "Defaults: 520 for a junction, 110 for 'all'.")
    ap.add_argument("--exaggeration", type=float, default=None,
                    help="Vehicle size multiplier, default 4.0. Use 1.0 to "
                         "judge real physical clearance.")
    ap.add_argument("--delay", type=int, default=250,
                    help="Playback delay in ms. COSMETIC — wall-clock only.")
    ap.add_argument("--scale", type=float, default=None,
                    help="Alias for --density. SUMO's own GUI 'Scale Traffic' "
                         "slider does the same thing; note 3.0 there puts the "
                         "corridor far past capacity and it gridlocks — that is "
                         "real saturation, not a bug.")
    ap.add_argument("--density", type=float, default=1.0,
                    help="Demand multiplier. NOTE: measured to NOT relieve "
                         "congestion (halted%% is flat 34-35%% from 1.0 down to "
                         "0.35); it only removes bikes from the screen. The "
                         "controller is the real lever.")
    ap.add_argument("--checkpoint", default=str(DEFAULT_CHECKPOINT))
    ap.add_argument("--ambulance-at", default=None,
                    help="Comma-separated sim times to spawn an ambulance, e.g. "
                         "'120,400'. Works even when this script has no terminal. "
                         "Interactive spawning (press 'a') needs a real terminal.")
    ap.add_argument("--ambulance-route", default="r_ns2",
                    help="Route for spawned ambulances. Cross-street routes "
                         "r_ns1/r_sn1 (J1), r_ns2/r_sn2 (J2), r_ns3/r_sn3 (J3) "
                         "arrive at ONE junction against the corridor flow — the "
                         "clearest view of an override. r_we / r_ew run the whole "
                         "corridor and trigger all three in turn. Default r_ns2 "
                         "(north->south through J2), matching run_tier0_episode --b2.")
    ap.add_argument("--no-when-blocked", action="store_true",
                    help="Spawn the ambulance the instant it is asked for, even "
                         "if the light is already green for it. By DEFAULT the "
                         "spawn is held until the entry junction's green does "
                         "NOT serve the ambulance, so an override actually has "
                         "to fire and you can see it. Measured on a paired "
                         "run, same seed, --ambulance-at 90,140: with this "
                         "flag 0 of 2 spawns produced an override (the light "
                         "was already green); by default the spawns are held "
                         "3-5s and 2 of 2 fire.")
    ap.add_argument("--autoplay", action="store_true",
                    help="Start playing immediately instead of waiting for Play.")
    ap.add_argument("--routes", default=None)
    ap.add_argument("--print-only", action="store_true")
    args = ap.parse_args()
    if args.scale is not None:
        args.density = args.scale

    net = GENERATED / f"corridor_{args.topology}.net.xml"
    if not net.exists():
        raise SystemExit(
            f"Network not found: {net}\nGenerate it first:\n"
            f"  python sim/networks/generate_corridor.py "
            f"--j1 {args.topology[0]} --j2 {args.topology[1]} --j3 {args.topology[2]}")
    if not DEMO_VTYPES.exists():
        raise SystemExit(
            f"Demo vType file not found: {DEMO_VTYPES}\nThis file is UNCOMMITTED "
            "(CLAUDE.md CURRENT STATUS) — on a fresh clone the mixed-traffic work "
            "does not exist yet.")

    if args.routes:
        routes = Path(args.routes)
    else:
        routes = (REPO_ROOT / "sim" / "routes"
                  / f"demo_gui_{args.topology}_seed{args.seed}.rou.xml")
        write_route_file(
            routes, random.Random(args.seed),
            corridor_veh_per_hour=1000.0 * args.density,
            cross_veh_per_hour=600.0 * args.density,
            flows_end_s=args.end,
            randomize_density=False,
        )

    settings = REPO_ROOT / "sim" / "routes" / f"_demo_view_{args.focus}.xml"
    write_view_settings(settings, net, args.focus, args.zoom,
                        args.exaggeration, args.delay)

    traci_mode = args.controller == "tier0"
    cmd = build_command(net=net, routes=routes, seed=args.seed, end_s=args.end,
                        settings=settings, autoplay=args.autoplay,
                        traci_mode=traci_mode)

    print("=" * 78)
    print("MIXED-TRAFFIC DEMO WATCH — tiered heterogeneity")
    print("=" * 78)
    print(f"  network    : corridor_{args.topology} (J1={args.topology[0]} / "
          f"J2={args.topology[1]} / J3={args.topology[2]} lanes per approach)")
    print(f"  vTypes     : {DEMO_VTYPES.name}  <-- TIERED demo model")
    print(f"  routes     : {routes.name}  (8 flows: 2 corridor + 6 cross, "
          f"density x{args.density})")
    print(f"  controller : {args.controller}"
          + ("   <-- static plan, 3.6x more congested; not for judging driving"
             if args.controller == "fixed" else ""))
    print(f"  camera     : focus={args.focus}  zoom="
          f"{args.zoom if args.zoom is not None else FOCUS_ZOOM['all' if args.focus=='all' else 'junction']}")
    print(f"  sublane    : --lateral-resolution {LATERAL_RESOLUTION}")
    print()
    print("  COLOUR KEY   GREEN=two-wheeler (lime=aggressive)  AMBER=auto "
          "(orange=aggressive)")
    print("               BLUE=car (deep=aggressive)  GREY=truck  RED=ambulance")
    print("=" * 78)
    print("  " + " ".join(cmd))
    print("=" * 78)

    if args.print_only:
        return

    if traci_mode:
        spawns = ([float(x) for x in args.ambulance_at.split(",") if x.strip()]
                  if args.ambulance_at else [])
        run_controlled(cmd, args.controller, args.end, Path(args.checkpoint),
                       spawn_times=spawns, amb_route=args.ambulance_route,
                       when_blocked=not args.no_when_blocked)
    else:
        run_uncontrolled(cmd)


if __name__ == "__main__":
    from sim.sumo_activity import require_free

    require_free("run_demo_gui.py (human sign-off watch, mixed-traffic demo model)")
    main()

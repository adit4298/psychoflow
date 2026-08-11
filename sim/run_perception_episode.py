"""§18 Phase 2 verification harness — one manually-triggered episode.

Runs the Phase 1 corridor with the Phase 2 test route file, updates the
digital twin every simulation step, manually injects an incident (§7.3)
and manually changes weather (§7.4) mid-episode so both event-driven
paths are actually exercised rather than sitting at defaults, then dumps
the §7.6 twin snapshot.

Not part of §6's folder structure — this is a verification harness for
the Phase 2 done bar, not a runtime component.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import sumolib  # noqa: E402
import traci  # noqa: E402

from perception.lane_sensor import WAITING_TIME_MEMORY_S  # noqa: E402
from twin.digital_twin import DigitalTwin  # noqa: E402

NET_FILE = REPO_ROOT / "sim" / "networks" / "generated" / "corridor.net.xml"
ADD_FILE = REPO_ROOT / "sim" / "networks" / "vehicle_types.add.xml"
ROU_FILE = REPO_ROOT / "sim" / "routes" / "test_episode.rou.xml"

INCIDENT_STEP = 150
WEATHER_STEP = 200
DEFAULT_DUMP_STEP = 300
DEFAULT_TOTAL_STEPS = 320


def summarise_junctions(snapshot: dict) -> None:
    for junction_id, jdata in snapshot["junctions"].items():
        lanes = jdata["lanes"]
        total_veh = sum(l["vehicle_count"] for l in lanes.values())
        total_halted = sum(l["halted_count"] for l in lanes.values())
        max_wait = max((l["wait_time_max_single_vehicle"] for l in lanes.values()), default=0.0)
        starved = sum(1 for l in lanes.values() if l["starvation_flag"])
        comp: dict[str, int] = {}
        for l in lanes.values():
            for vtype, count in l["type_composition"].items():
                comp[vtype] = comp.get(vtype, 0) + count
        print(
            f"  {junction_id}: lane_count={jdata['lane_count']} phase={jdata['current_phase']} "
            f"lanes_sensed={len(lanes)} vehicles={total_veh} halted={total_halted} "
            f"max_single_wait={max_wait}s starved_lanes={starved}"
        )
        print(f"        type_composition={comp}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2 perception-layer verification episode")
    parser.add_argument("--steps", type=int, default=DEFAULT_TOTAL_STEPS)
    parser.add_argument("--dump-step", type=int, default=DEFAULT_DUMP_STEP)
    parser.add_argument("--v2x-print-limit", type=int, default=3)
    parser.add_argument("--lane-print-limit", type=int, default=2)
    args = parser.parse_args()

    traci.start([
        sumolib.checkBinary("sumo"),
        "-n", str(NET_FILE),
        "-a", str(ADD_FILE),
        "-r", str(ROU_FILE),
        # Critical: default is 100s, which saturates against the 90s
        # starvation threshold (§0.1) and destroys the magnitude signal
        # §9.1/§9.4 depend on.
        "--waiting-time-memory", str(WAITING_TIME_MEMORY_S),
        "--no-step-log",
        "--duration-log.disable",
    ])

    twin = DigitalTwin(NET_FILE, seed=42)
    twin.attach()

    print("=" * 78)
    print("TOPOLOGY DERIVED FROM NET FILE")
    print("=" * 78)
    for junction_id, topo in twin.topology.items():
        by_direction: dict[str, list[str]] = {}
        for lane_id, direction in topo["lane_approach_map"].items():
            by_direction.setdefault(direction, []).append(lane_id)
        print(f"  {junction_id}: lane_count={topo['lane_count']} approach_lanes={len(topo['lane_approach_map'])}")
        for direction in sorted(by_direction):
            print(f"        {direction:5s}: {sorted(by_direction[direction])}")

    print()
    print("WEATHER BASELINE vType PARAMS (before any change)")
    baseline_params = twin.weather.current_vtype_params()
    for vtype, params in baseline_params.items():
        print(f"  {vtype:10s} {params}")

    snapshot = None
    for step in range(1, args.steps + 1):
        traci.simulationStep()
        sim_time = traci.simulation.getTime()

        if step == INCIDENT_STEP:
            incident = twin.incidents.report(
                incident_type="lane_blocked",
                junction_id="J2",
                lane_id="J1_J2_1",
                severity="high",
                affected_lanes=["J1_J2_1", "J1_J2_2"],
                reported_at_sim_time=sim_time,
                estimated_duration_s=600,
            )
            print(f"\n[step {step}] INCIDENT INJECTED: {incident.incident_id} on {incident.location}")

        if step == WEATHER_STEP:
            twin.weather.set_state("heavy_rain", sim_time)
            print(f"\n[step {step}] WEATHER CHANGED -> heavy_rain")
            print("  vType params read back from SUMO after change:")
            for vtype, params in twin.weather.current_vtype_params().items():
                base = baseline_params[vtype]
                print(
                    f"    {vtype:10s} tau {base['tau']} -> {params['tau']} | "
                    f"max_speed {base['max_speed']} -> {params['max_speed']} | "
                    f"sigma {base['sigma']} -> {params['sigma']}"
                )

        snapshot = twin.update(sim_time)

        if step == args.dump_step:
            break

    assert snapshot is not None

    print()
    print("=" * 78)
    print(f"DIGITAL TWIN SNAPSHOT — sim_time={snapshot['sim_time']}")
    print("=" * 78)
    summarise_junctions(snapshot)

    print()
    print(f"  active_incidents: {len(snapshot['active_incidents'])}")
    print(f"  weather: {snapshot['weather']}")
    print(f"  v2x_messages_recent: {len(snapshot['v2x_messages_recent'])} buffered")
    print(f"  v2x emitter stats: {twin.v2x.stats()}")
    print(f"  lane_sensor unknown vTypes seen: {twin.lane_sensor.unknown_types or 'none'}")

    # Full §7.6 JSON, with the repetitive collections truncated for
    # readability. Truncation is labelled; nothing is fabricated.
    printable = json.loads(json.dumps(snapshot))
    for junction_id, jdata in printable["junctions"].items():
        full_lane_ids = list(jdata["lanes"])
        keep = full_lane_ids[: args.lane_print_limit]
        jdata["lanes"] = {k: jdata["lanes"][k] for k in keep}
        jdata["vision"] = {k: jdata["vision"][k] for k in keep}
        jdata["_truncated_note"] = (
            f"showing {len(keep)} of {len(full_lane_ids)} approach lanes"
        )
    v2x_total = len(printable["v2x_messages_recent"])
    printable["v2x_messages_recent"] = printable["v2x_messages_recent"][: args.v2x_print_limit]
    printable["_v2x_truncated_note"] = f"showing {args.v2x_print_limit} of {v2x_total} buffered messages"

    print()
    print("=" * 78)
    print("§7.6 SNAPSHOT JSON (collections truncated as labelled)")
    print("=" * 78)
    print(json.dumps(printable, indent=2))

    traci.close()


if __name__ == "__main__":
    main()

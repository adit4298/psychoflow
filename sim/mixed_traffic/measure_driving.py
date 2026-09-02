"""Driving-model measurement harness (STEP 1 amendments, items 1 / 2 / 5).

DELIBERATELY STANDALONE. It drives raw SUMO with netconvert's own static TLS
program at 1s resolution -- it does NOT construct PsychoFlowEnv, does not load
any checkpoint, and touches no reward/validator code. Two reasons:

  1. Items 1/2/5 are questions about the DRIVING MODEL, not about the
     controller. Measuring them through the RL policy would confound the two.
  2. The static program cycles on its own, so a 1200s run yields many
     red->green transitions per approach -- far more queue samples than an
     RL-driven episode of the same length, where phase changes are demand-
     driven and sparse.

Item 1 needs 1s resolution: PsychoFlowEnv's decision loop is 5s, and at 13.9
m/s a vehicle covers 69m in 5s -- longer than the whole junction. A 5s-sampled
queue snapshot would miss the ordering entirely.

Usage:
    python measure_driving.py --label baseline --vtypes <path> [--lateral-resolution 0.4]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# ^ Derived, not hardcoded. This harness ran from a scratchpad during the
#   mixed-traffic work and carried an absolute path to one machine's checkout.
#   parents[2] is the repo root from sim/mixed_traffic/. See README.md here.
sys.path.insert(0, str(REPO_ROOT))

import sumolib  # noqa: E402
import traci  # noqa: E402

from perception.lane_sensor import WAITING_TIME_MEMORY_S, base_vtype  # noqa: E402
from env.psychoflow_env import TIME_TO_TELEPORT_S  # noqa: E402

NET = REPO_ROOT / "sim" / "networks" / "generated" / "corridor_432.net.xml"
JUNCTIONS = ("J1", "J2", "J3")
VEHICLE_TYPES = ("bike", "auto", "car", "truck", "ambulance")

# A vehicle within this distance of the stop line at green onset counts as
# "in the queue". 80m at 4 lanes holds well over the observed queue lengths.
QUEUE_ZONE_M = 80.0


def approach_lane_groups(net) -> dict[tuple[str, str], list[str]]:
    """{(junction, incoming edge id): [lane ids]} -- one group per approach."""
    groups: dict[tuple[str, str], list[str]] = {}
    for jid in JUNCTIONS:
        for edge in net.getNode(jid).getIncoming():
            groups[(jid, edge.getID())] = [
                lane.getID() for lane in edge.getLanes()
            ]
    return groups


def lane_lengths(net) -> dict[str, float]:
    out = {}
    for edge in net.getEdges():
        for lane in edge.getLanes():
            out[lane.getID()] = lane.getLength()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--vtypes", required=True)
    ap.add_argument("--routes", required=True)
    ap.add_argument("--lateral-resolution", type=float, default=None)
    ap.add_argument("--seconds", type=int, default=1200)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    net = sumolib.net.readNet(str(NET))
    groups = approach_lane_groups(net)
    lane_len = lane_lengths(net)
    lane_to_group = {
        lane: key for key, lanes in groups.items() for lane in lanes
    }

    cmd = [
        sumolib.checkBinary("sumo"),
        "-n", str(NET),
        "-a", str(args.vtypes),
        "-r", str(args.routes),
        "--waiting-time-memory", str(WAITING_TIME_MEMORY_S),
        "--time-to-teleport", str(TIME_TO_TELEPORT_S),
        "--step-length", "1.0",
        "--seed", "7",
        "--no-step-log", "--duration-log.disable", "--no-warnings",
        # Aggressive driving makes collisions possible. SUMO's DEFAULT is
        # `teleport`, which silently REMOVES a vehicle -- that would corrupt
        # every count below without raising. `warn` keeps the vehicle.
        "--collision.action", "warn",
        # minGap is a PREFERENCE in mixed traffic, not a safety envelope.
        # SUMO's default flags a "collision" on any minGap violation, which
        # would report every deliberate tight gap as a crash. 0 = count only
        # actual physical overlap.
        "--collision.mingap-factor", "0",
    ]
    if args.lateral_resolution is not None:
        cmd += ["--lateral-resolution", str(args.lateral_resolution)]

    traci.start(cmd, label=args.label)
    traci.switch(args.label)

    # BASE type (bike/auto/car/...) for every metric that has a STEP 1
    # comparator, plus the concrete TIER id alongside it. Under the refinement
    # getTypeID() returns "bike.normal", so matching it raw against the base
    # names silently drops every tiered vehicle -- the same defect this
    # session fixed in perception/lane_sensor.py, which this harness hit too.
    vtype_of: dict[str, str] = {}
    tier_of: dict[str, str] = {}
    first_seen_on_group: dict[tuple[str, tuple[str, str]], float] = {}
    first_halt_on_group: dict[tuple[str, tuple[str, str]], float] = {}
    prev_green: dict[tuple[str, str], bool] = {k: False for k in groups}

    speed_samples: dict[str, list[float]] = defaultdict(list)
    departures: list[tuple[float, str, str]] = []   # (t, vid, route)
    queue_samples: list[dict] = []
    collisions = 0
    colliding_ids: set[str] = set()
    all_vehicle_ids: set[str] = set()

    t = 0.0
    while t < args.seconds:
        traci.simulationStep()
        t = traci.simulation.getTime()

        collisions += traci.simulation.getCollidingVehiclesNumber()
        colliding_ids.update(traci.simulation.getCollidingVehiclesIDList())

        for vid in traci.simulation.getDepartedIDList():
            try:
                rid = traci.vehicle.getRouteID(vid)
            except traci.TraCIException:
                rid = "?"
            departures.append((t, vid, rid))

        # --- per-approach green state, read from the live RYG string --------
        green_now: dict[tuple[str, str], bool] = {}
        for jid in JUNCTIONS:
            state = traci.trafficlight.getRedYellowGreenState(jid)
            controlled = traci.trafficlight.getControlledLanes(jid)
            per_group: dict[tuple[str, str], bool] = {}
            for i, lane in enumerate(controlled):
                key = lane_to_group.get(lane)
                if key is None or key[0] != jid or i >= len(state):
                    continue
                per_group[key] = per_group.get(key, False) or state[i] in ("G", "g")
            green_now.update(per_group)

        # --- track vehicles, sample speeds ---------------------------------
        veh_on_group: dict[tuple[str, str], list[str]] = defaultdict(list)
        for vid in traci.vehicle.getIDList():
            all_vehicle_ids.add(vid)
            if vid not in vtype_of:
                raw = traci.vehicle.getTypeID(vid)
                tier_of[vid] = raw.split("@")[0]
                vtype_of[vid] = base_vtype(raw)
            vt = vtype_of[vid]
            speed_samples[vt].append(traci.vehicle.getSpeed(vid))
            if tier_of[vid] != vt:
                speed_samples[tier_of[vid]].append(traci.vehicle.getSpeed(vid))

            lane = traci.vehicle.getLaneID(vid)
            key = lane_to_group.get(lane)
            if key is None:
                continue
            veh_on_group[key].append(vid)
            fk = (vid, key)
            if fk not in first_seen_on_group:
                first_seen_on_group[fk] = t
            # First time it actually STOPPED on this approach = the moment it
            # joined the back of the queue. Ranking by this isolates
            # "filtered forward once queued" from "was overtaken during the
            # 150m free-flow run-in", which are different questions.
            if traci.vehicle.getSpeed(vid) < 0.1 and (vid, key) not in first_halt_on_group:
                first_halt_on_group[(vid, key)] = t

        # --- at each red->green transition, snapshot the queue ordering -----
        for key, is_green in green_now.items():
            if is_green and not prev_green.get(key, False):
                jid, edge_id = key
                rows = []
                for vid in veh_on_group.get(key, []):
                    lane = traci.vehicle.getLaneID(vid)
                    pos = traci.vehicle.getLanePosition(vid)
                    dist_to_stop = lane_len[lane] - pos
                    if dist_to_stop > QUEUE_ZONE_M:
                        continue
                    rows.append({
                        "vid": vid,
                        "vtype": vtype_of[vid],
                        "tier": tier_of[vid],
                        "arrive_t": first_seen_on_group[(vid, key)],
                        "halt_t": first_halt_on_group.get((vid, key)),
                        "dist_to_stop": round(dist_to_stop, 2),
                        # halted == genuinely queued, rather than still
                        # rolling in. The halted-only view is the faithful
                        # answer to "filtered past a QUEUE"; the all-vehicles
                        # view also counts ordinary approach overtaking.
                        "halted": traci.vehicle.getSpeed(vid) < 0.1,
                    })
                if len(rows) >= 3:
                    queue_samples.append({
                        "sim_time": t, "junction": jid, "edge": edge_id,
                        "n": len(rows), "rows": rows,
                    })
            prev_green[key] = is_green

    traci.close()

    # ================= item 1: queue-front filtering =====================
    # arrival rank: 0 = arrived first. stopline rank: 0 = nearest stop line.
    # advancement = arrival_rank - stopline_rank. POSITIVE means the vehicle
    # sits further forward than its arrival order earned -> it filtered.
    def filtering_stats(halted_only: bool, key_field: str = "arrive_t",
                        group: str = "vtype") -> dict:
        adv: dict[str, list[float]] = defaultdict(list)
        inversions: dict[str, int] = defaultdict(int)
        pairs: dict[str, int] = defaultdict(int)
        n_samples = 0
        for sample in queue_samples:
            rows = [r for r in sample["rows"] if r["halted"]] if halted_only \
                else sample["rows"]
            if len(rows) < 3:
                continue
            n_samples += 1
            by_arrival = sorted(rows, key=lambda r: (r[key_field], r["vid"]))
            by_stopline = sorted(rows, key=lambda r: (r["dist_to_stop"], r["vid"]))
            arank = {r["vid"]: i for i, r in enumerate(by_arrival)}
            srank = {r["vid"]: i for i, r in enumerate(by_stopline)}
            for r in rows:
                adv[r[group]].append(arank[r["vid"]] - srank[r["vid"]])
            for a in rows:
                for b in rows:
                    if a["vid"] == b["vid"]:
                        continue
                    # a arrived LATER than b?
                    if arank[a["vid"]] <= arank[b["vid"]]:
                        continue
                    tag = f"{a[group]}_over_{b[group]}"
                    pairs[tag] += 1
                    if srank[a["vid"]] < srank[b["vid"]]:   # a is nearer the line
                        inversions[tag] += 1
        return {
            "n_queue_samples_used": n_samples,
            "mean_advancement": {
                vt: round(statistics.mean(v), 3) for vt, v in sorted(adv.items())
            },
            "n_ranked": {vt: len(v) for vt, v in sorted(adv.items())},
            "pair_inversion_rate": {
                tag: {
                    "later_arrivals": pairs[tag],
                    "overtook": inversions[tag],
                    "rate": round(inversions[tag] / pairs[tag], 4),
                }
                for tag in sorted(pairs) if pairs[tag] >= 20
            },
        }

    # ================= item 5: spawn interval structure ==================
    by_route: dict[str, list[float]] = defaultdict(list)
    for tt, _vid, rid in departures:
        by_route[rid].append(tt)
    spawn = {}
    for rid, times in sorted(by_route.items()):
        times.sort()
        gaps = [round(b - a, 3) for a, b in zip(times, times[1:])]
        if len(gaps) >= 3:
            spawn[rid] = {
                "n_departures": len(times),
                "mean_gap_s": round(statistics.mean(gaps), 3),
                "stdev_gap_s": round(statistics.pstdev(gaps), 4),
                "cv": round(statistics.pstdev(gaps) / statistics.mean(gaps), 4)
                if statistics.mean(gaps) else 0.0,
                "distinct_gaps": sorted(set(gaps))[:8],
                "n_distinct_gaps": len(set(gaps)),
            }

    # ================= item 2: observed speeds ===========================
    speeds = {}
    for vt in list(VEHICLE_TYPES) + sorted(
            k for k in speed_samples if k not in VEHICLE_TYPES):
        s = speed_samples.get(vt, [])
        if not s:
            continue
        s_sorted = sorted(s)
        moving = [x for x in s if x > 0.1]
        speeds[vt] = {
            "n_samples": len(s),
            "mean_all": round(statistics.mean(s), 2),
            "mean_moving": round(statistics.mean(moving), 2) if moving else 0.0,
            "p85": round(s_sorted[int(0.85 * (len(s_sorted) - 1))], 2),
            "max": round(max(s), 2),
        }

    result = {
        "label": args.label,
        "vtypes": str(args.vtypes),
        "lateral_resolution": args.lateral_resolution,
        "seconds": args.seconds,
        "collisions_vehicle_steps": collisions,
        "collisions_distinct_vehicles": len(colliding_ids),
        "total_vehicles_seen": len(all_vehicle_ids),
        "collision_rate_pct": round(100 * len(colliding_ids) / max(1, len(all_vehicle_ids)), 3),
        "n_queue_samples": len(queue_samples),
        "speeds": speeds,
        "spawn": spawn,
        "filtering_all": filtering_stats(halted_only=False),
        "filtering_halted": filtering_stats(halted_only=True),
        "filtering_queuejoin": filtering_stats(halted_only=True, key_field="halt_t"),
        # Per-TIER, so heterogeneity is measured rather than asserted: a
        # cautious bike and an aggressive bike must not score the same.
        "filtering_halted_by_tier": filtering_stats(halted_only=True,
                                                    group="tier"),
    }

    Path(args.out).write_text(json.dumps(result, indent=2))
    pct = 100 * len(colliding_ids) / max(1, len(all_vehicle_ids))
    print(f"[{args.label}] {args.out}")
    print(f"  collisions: {len(colliding_ids)} distinct vehicles of "
          f"{len(all_vehicle_ids)} seen = {pct:.2f}% "
          f"({collisions} vehicle-steps) | queue_samples={len(queue_samples)}")


if __name__ == "__main__":
    from sim.sumo_activity import require_free
    require_free("measure_driving.py (STEP 1 driving-model measurement)")
    main()

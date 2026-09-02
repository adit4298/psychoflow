"""Overtake-logic measurement harness (STEP 1 REFINEMENT, items 2 and 3).

DELIBERATELY STANDALONE, same category and same reasons as measure_driving.py:
raw SUMO, netconvert's own static TLS, 1s resolution, no PsychoFlowEnv, no
checkpoint, no reward/validator code. It measures the DRIVING MODEL, so it
cannot perturb Stage 4 or any recorded number.

1s resolution is required for the same reason item 1 needed it: at 13.9 m/s a
vehicle covers 69m in 5s, longer than a junction, and every passing event
aliases away.

--------------------------------------------------------------------------
ITEM 2 -- does overtake-attempt rate scale with SPEED DIFFERENTIAL to the
          leader, as lcSpeedGain is supposed to make it?
--------------------------------------------------------------------------
Two independent views, one mechanism-level and one outcome-level, because
either alone can pass while proving nothing:

  (A) MECHANISM. For every (bike|auto) follower-step that HAS a leader within
      LEADER_ZONE_M on the same edge, read the lane-change model's own wish
      via getLaneChangeState(vid, dir)[0] and test the LCA_SPEEDGAIN bit.
      Bucket by `delta = desired_speed(follower) - speed(leader)`. This is the
      model's stated reason for wanting to move, not an inferred one.

      desired_speed = min(getMaxSpeed, getAllowedSpeed); getAllowedSpeed
      already folds in the vehicle's own speedFactor draw.

  (B) OUTCOME. Actual completed passes (see item 3's tracker), bucketed by the
      SAME delta measured at the instant the following relationship opened.

A correctly-tuned lcSpeedGain gives a rate that RISES with delta and is near
zero at delta <= 0 (leader already at or above the follower's desired speed --
there is nothing to gain). A flat profile is the bug this refinement targets:
overtaking regardless of whether the leader is actually slow.

--------------------------------------------------------------------------
ITEM 3 -- of the passes two-wheelers complete, what fraction are same-lane
          "lane sharing" vs a discrete lane change? (field target ~62/38)
--------------------------------------------------------------------------
A pass is detected from the follower's own frame:

  open   : F has leader L, both on the SAME LANE of a non-internal edge E,
           F behind L
  close  : F and L both still on E and F's lanePosition > L's  -> PASS
  abort  : either leaves E, or the relationship outlives PAIR_TTL_S

Classification uses F's LANE INDEX over the life of the relationship:
  lane index never changed          -> LANE-SHARING (sublane, within lane)
  lane index changed at least once  -> LANE CHANGE (discrete)

CORRECTED AFTER THE FIRST BASELINE RUN -- the first version opened a
relationship on SAME EDGE rather than SAME LANE, and the baseline control
promptly reported 22.19% "lane sharing" under LC2013, which has no sublane and
therefore cannot produce one. The cause was that two vehicles in PARALLEL
LANES of the same edge were being opened as a following pair, so F could pass L
without ever changing lane -- not an overtake at all. Same-lane opening is the
fix. Internal (junction) edges are excluded too: a ":J1_0" lane index is a
connection index, not a road lane, so a "lane change" there means nothing.

The baseline arm is the control that proves the metric works: LC2013 with no
--lateral-resolution has no sublane, so lane-sharing there must be ~0 by
construction. A large sharing share in the baseline means the metric is wrong,
not the model -- which is exactly how the bug above was caught.

Usage:
    python measure_overtake.py --label demo --vtypes <path> --routes <path> \
        --lateral-resolution 0.4 --out demo.json
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
import traci.constants as tc  # noqa: E402

from perception.lane_sensor import WAITING_TIME_MEMORY_S  # noqa: E402
from env.psychoflow_env import TIME_TO_TELEPORT_S  # noqa: E402

NET = REPO_ROOT / "sim" / "networks" / "generated" / "corridor_432.net.xml"

# Only a leader this close is plausibly the thing you would overtake. Beyond
# it the follower is not constrained and a "no attempt" reading says nothing.
LEADER_ZONE_M = 50.0
# A following relationship older than this is stale (queued at a red for a
# whole phase, say) and its opening delta no longer describes the situation.
PAIR_TTL_S = 120.0
# Followers item 2/3 ask about, plus `car` as a CONTROL: the refinement gives
# car a small aggressive minority, so it must be measurable, and if car's
# delta-response looked identical to bike's the per-type differentiation would
# be doing nothing. Truck/ambulance stay single-type by design.
FOLLOWER_BASES = ("bike", "auto", "car")

# delta = desired_speed(follower) - speed(leader), m/s.
DELTA_BINS = [
    ("<=0",     -1e9,  0.0),
    ("0-1",      0.0,  1.0),
    ("1-2",      1.0,  2.0),
    ("2-4",      2.0,  4.0),
    ("4-6",      4.0,  6.0),
    (">6",       6.0,  1e9),
]


def bin_of(delta: float) -> str:
    for name, lo, hi in DELTA_BINS:
        if lo <= delta < hi:
            return name
    return DELTA_BINS[-1][0]


def base_of(type_id: str) -> str:
    """bike.aggressive -> bike ; car -> car ; car@veh1 -> car.

    Mirrors what perception/lane_sensor.py would have to do. Kept here so the
    harness reads the same tier ids the add-file declares.
    """
    return type_id.split("@")[0].split(".")[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--vtypes", required=True)
    ap.add_argument("--routes", required=True)
    ap.add_argument("--lateral-resolution", type=float, default=None)
    ap.add_argument("--seconds", type=int, default=1200)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

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
        # Same rationale as measure_driving.py: SUMO's default collision
        # action TELEPORTS the vehicle away, which would silently delete the
        # exact events being counted. mingap-factor 0 counts physical overlap
        # only, since minGap is a preference in mixed traffic, not an envelope.
        "--collision.action", "warn",
        "--collision.mingap-factor", "0",
    ]
    if args.lateral_resolution is not None:
        cmd += ["--lateral-resolution", str(args.lateral_resolution)]

    traci.start(cmd, label=args.label)
    traci.switch(args.label)

    tier_of: dict[str, str] = {}

    # --- item 2 (A): mechanism ------------------------------------------
    # key -> (tier, delta_bin, moving?) -> [steps, speedgain, any_wish, blocked]
    # `moving` is split out because a standing queue at a red light puts a huge
    # number of steps in the high-delta bins (the leader is stopped, so delta is
    # large by definition) where overtaking is not a decision the follower can
    # act on. Pooling them flattens the very profile item 2 asks about.
    mech: dict[tuple[str, str, bool], list[int]] = defaultdict(lambda: [0, 0, 0, 0])

    # --- item 3 / item 2 (B): pass tracking -----------------------------
    # (F, L) -> dict(edge, t0, delta0, lane0, lanes_seen)
    open_pairs: dict[tuple[str, str], dict] = {}
    passes: list[dict] = []
    aborted = 0

    collisions_ids: set[str] = set()
    all_ids: set[str] = set()

    t = 0.0
    while t < args.seconds:
        traci.simulationStep()
        t = traci.simulation.getTime()
        collisions_ids.update(traci.simulation.getCollidingVehiclesIDList())

        live = set(traci.vehicle.getIDList())
        all_ids |= live

        # one read per vehicle per step, cached
        edge: dict[str, str] = {}
        lane_id: dict[str, str] = {}
        lane_ix: dict[str, int] = {}
        pos: dict[str, float] = {}
        spd: dict[str, float] = {}
        for vid in live:
            if vid not in tier_of:
                tier_of[vid] = traci.vehicle.getTypeID(vid).split("@")[0]
            edge[vid] = traci.vehicle.getRoadID(vid)
            lane_id[vid] = traci.vehicle.getLaneID(vid)
            lane_ix[vid] = traci.vehicle.getLaneIndex(vid)
            pos[vid] = traci.vehicle.getLanePosition(vid)
            spd[vid] = traci.vehicle.getSpeed(vid)

        for vid in live:
            tier = tier_of[vid]
            base = base_of(tier)
            if base not in FOLLOWER_BASES:
                continue

            lead = traci.vehicle.getLeader(vid, LEADER_ZONE_M)
            if not lead or not lead[0]:
                continue
            lid, gap = lead
            if lid not in live or edge.get(lid) != edge[vid]:
                continue

            desired = min(traci.vehicle.getMaxSpeed(vid),
                          traci.vehicle.getAllowedSpeed(vid))
            delta = desired - spd[lid]
            b = bin_of(delta)

            # ---- (A) the model's OWN stated wish, both directions ----
            wish = 0
            for direction in (-1, 1):
                wish |= traci.vehicle.getLaneChangeState(vid, direction)[0]
            cell = mech[(tier, b, spd[vid] > 0.5)]
            cell[0] += 1
            if wish & tc.LCA_SPEEDGAIN:
                cell[1] += 1
            if wish & tc.LCA_WANTS_LANECHANGE:
                cell[2] += 1
            if wish & tc.LCA_BLOCKED:
                cell[3] += 1

            # ---- open a following relationship (F behind L, SAME LANE) ----
            # Same LANE, not merely same edge: two vehicles in parallel lanes
            # are not a following pair, and F "passing" L from an adjacent lane
            # without changing lane is not an overtake. Non-internal only --
            # a ":J1_0" lane index is a connection index, not a road lane.
            key = (vid, lid)
            if (key not in open_pairs
                    and pos[vid] < pos[lid]
                    and lane_id[vid] == lane_id[lid]
                    and not edge[vid].startswith(":")):
                open_pairs[key] = {
                    "edge": edge[vid], "t0": t, "delta0": delta,
                    "lane0": lane_ix[vid], "lanes": {lane_ix[vid]},
                    "leader_lanes": {lane_ix[lid]}, "gap0": gap,
                }

        # ---- advance / resolve open relationships ----
        for key in list(open_pairs):
            f, l = key
            rec = open_pairs[key]
            if f not in live or l not in live \
                    or edge.get(f) != rec["edge"] or edge.get(l) != rec["edge"]:
                del open_pairs[key]
                aborted += 1
                continue
            if t - rec["t0"] > PAIR_TTL_S:
                del open_pairs[key]
                aborted += 1
                continue
            rec["lanes"].add(lane_ix[f])
            rec["leader_lanes"].add(lane_ix[l])
            # Delta AT THE PASS INSTANT, as well as at open. The open-time
            # delta is what the follower's decision to overtake was formed
            # against, but a relationship can run for tens of seconds and the
            # leader's speed moves in that time -- so bucketing an outcome by
            # the OPEN delta silently mixes situations. Recording both is what
            # separates "the model overtakes when there is nothing to gain"
            # from "the metric attributed a later pass to an earlier state".
            rec["last_delta"] = (min(traci.vehicle.getMaxSpeed(f),
                                     traci.vehicle.getAllowedSpeed(f))
                                 - spd[l])
            if pos[f] > pos[l]:
                changed = len(rec["lanes"]) > 1
                leader_moved = len(rec["leader_lanes"]) > 1
                passes.append({
                    "follower_tier": tier_of[f],
                    "follower_base": base_of(tier_of[f]),
                    "leader_tier": tier_of[l],
                    "leader_base": base_of(tier_of[l]),
                    "delta0": round(rec["delta0"], 3),
                    "delta_bin": bin_of(rec["delta0"]),
                    "delta_at_pass": round(rec["last_delta"], 3),
                    "delta_bin_at_pass": bin_of(rec["last_delta"]),
                    "duration_s": round(t - rec["t0"], 1),
                    "lane_changed": changed,
                    "leader_changed_lane": leader_moved,
                    "mode": "lane_change" if changed else "lane_sharing",
                    # STRICT: neither vehicle left the shared lane. This is the
                    # unambiguous "passed inside one lane" case; the headline
                    # `mode` is follower-only, which is what the field studies
                    # describe (the two-wheeler did not take a lane).
                    "mode_strict": "lane_change" if (changed or leader_moved)
                                   else "lane_sharing",
                })
                del open_pairs[key]

    traci.close()

    # ================= item 2 (A) roll-up =================================
    def roll(pred, moving: bool | None) -> dict:
        out = {}
        for b, _lo, _hi in DELTA_BINS:
            n = sg = aw = bl = 0
            for (tier, bb, mv), c in mech.items():
                if bb != b or not pred(tier):
                    continue
                if moving is not None and mv is not moving:
                    continue
                n += c[0]; sg += c[1]; aw += c[2]; bl += c[3]
            if n:
                out[b] = {
                    "follower_steps": n,
                    "speedgain_wish": sg,
                    "speedgain_rate": round(sg / n, 4),
                    "any_lc_wish_rate": round(aw / n, 4),
                    "blocked_rate": round(bl / n, 4),
                }
        return out

    tiers = sorted({tier for tier, _, _ in mech})
    item2_mechanism = {
        "MOVING_ALL": roll(lambda _t: True, True),
        "STOPPED_ALL": roll(lambda _t: True, False),
        "POOLED_ALL": roll(lambda _t: True, None),
        **{f"MOVING_base:{b}": roll(lambda x, b=b: base_of(x) == b, True)
           for b in FOLLOWER_BASES},
        **{f"MOVING_tier:{tr}": roll(lambda x, tr=tr: x == tr, True)
           for tr in tiers},
    }

    # ================= item 2 (B) outcome roll-up ========================
    # Denominator is MOVING follower-steps -- a stopped follower in a queue is
    # not making an overtaking decision, and including those steps would make
    # the high-delta bins look artificially inert.
    by_bin_open: dict[str, int] = defaultdict(int)
    for (tier, bb, mv), c in mech.items():
        if mv:
            by_bin_open[bb] += c[0]
    by_bin_pass: dict[str, int] = defaultdict(int)
    by_bin_pass_at: dict[str, int] = defaultdict(int)
    dur_by_bin: dict[str, list[float]] = defaultdict(list)
    for p in passes:
        by_bin_pass[p["delta_bin"]] += 1
        by_bin_pass_at[p["delta_bin_at_pass"]] += 1
        dur_by_bin[p["delta_bin"]].append(p["duration_s"])
    item2_outcome = {
        b: {
            "passes_bucketed_at_OPEN": by_bin_pass[b],
            "passes_bucketed_AT_PASS": by_bin_pass_at[b],
            "moving_follower_steps": by_bin_open[b],
            "passes_per_1k_moving_steps":
                round(1000 * by_bin_pass[b] / by_bin_open[b], 2)
                if by_bin_open[b] else None,
            "passes_per_1k_moving_steps_AT_PASS":
                round(1000 * by_bin_pass_at[b] / by_bin_open[b], 2)
                if by_bin_open[b] else None,
            "median_duration_s": round(statistics.median(dur_by_bin[b]), 1)
                if dur_by_bin[b] else None,
        }
        for b, _lo, _hi in DELTA_BINS if by_bin_open[b]
    }

    # ================= item 3 roll-up ====================================
    def share(rows: list[dict]) -> dict:
        n = len(rows)
        if not n:
            return {"n_passes": 0}
        sharing = sum(1 for r in rows if r["mode"] == "lane_sharing")
        strict = sum(1 for r in rows if r["mode_strict"] == "lane_sharing")
        return {
            "n_passes": n,
            "lane_sharing": sharing,
            "lane_change": n - sharing,
            "sharing_pct": round(100 * sharing / n, 2),
            "change_pct": round(100 * (n - sharing) / n, 2),
            "sharing_pct_strict": round(100 * strict / n, 2),
            "median_duration_s": round(statistics.median(
                [r["duration_s"] for r in rows]), 1),
        }

    item3 = {
        "ALL": share(passes),
        **{f"base:{b}": share([p for p in passes if p["follower_base"] == b])
           for b in FOLLOWER_BASES},
        **{f"tier:{tr}": share([p for p in passes if p["follower_tier"] == tr])
           for tr in tiers},
    }

    result = {
        "label": args.label,
        "vtypes": str(args.vtypes),
        "routes": str(args.routes),
        "lateral_resolution": args.lateral_resolution,
        "seconds": args.seconds,
        "total_vehicles_seen": len(all_ids),
        "collisions_distinct_vehicles": len(collisions_ids),
        "collision_rate_pct": round(
            100 * len(collisions_ids) / max(1, len(all_ids)), 3),
        "tiers_observed": tiers,
        "n_passes": len(passes),
        "n_aborted_relationships": aborted,
        "item2_mechanism_speedgain_by_delta": item2_mechanism,
        "item2_outcome_passes_by_delta": item2_outcome,
        "item3_sharing_vs_change": item3,
        "passes_sample": passes[:40],
    }
    Path(args.out).write_text(json.dumps(result, indent=2))

    print(f"[{args.label}] -> {args.out}")
    print(f"  vehicles={len(all_ids)} collisions={len(collisions_ids)} "
          f"({result['collision_rate_pct']}%)  passes={len(passes)}")
    for view in ("MOVING_ALL", "STOPPED_ALL"):
        print(f"  item2 (A) mechanism [{view}]:")
        print(f"    {'delta bin':<10} {'steps':>8} {'spdgain':>9} {'rate':>8} "
              f"{'anyLC':>8} {'blocked':>8}")
        for b, v in item2_mechanism[view].items():
            print(f"    {b:<10} {v['follower_steps']:>8} {v['speedgain_wish']:>9} "
                  f"{v['speedgain_rate']:>8.4f} {v['any_lc_wish_rate']:>8.4f} "
                  f"{v['blocked_rate']:>8.4f}")
    print("  item2 (B) outcome, passes per 1k MOVING follower-steps:")
    print(f"    {'delta bin':<10} {'@OPEN':>8} {'/1k':>8} {'@PASS':>8} "
          f"{'/1k':>8} {'medDur':>8}")
    for b, v in item2_outcome.items():
        print(f"    {b:<10} {v['passes_bucketed_at_OPEN']:>8} "
              f"{v['passes_per_1k_moving_steps']:>8} "
              f"{v['passes_bucketed_AT_PASS']:>8} "
              f"{v['passes_per_1k_moving_steps_AT_PASS']:>8} "
              f"{v['median_duration_s']:>8}")
    print("  item3 sharing vs change (headline = follower-only; strict = neither moved):")
    for k, v in item3.items():
        if v.get("n_passes"):
            print(f"    {k:<22} n={v['n_passes']:<5} sharing={v['sharing_pct']:>5}% "
                  f"change={v['change_pct']:>5}%  (strict sharing "
                  f"{v['sharing_pct_strict']}%)")


if __name__ == "__main__":
    from sim.sumo_activity import require_free
    require_free("measure_overtake.py (STEP 1 refinement, items 2/3)")
    main()

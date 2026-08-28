"""§18 Phase 9 done-bar harness — Backend (§13).

Done bar: "dashboard-less test client can call every control function and see the
WebSocket stream update."

Boots the FastAPI app in-process (TestClient runs the lifespan, so the SimRunner
thread starts), connects a WebSocket client, and runs a 7-point checklist:

  1. §13.2 frames arrive with the frozen shape (5 keys, well-formed digital twin)
  2. set_mode  manual <-> auto  visibly changes decision.reason on the stream
  3. set_lane_bias  reaches get_stats() and expires; and the Tier 0 additive
     lane_weights param it rides on actually moves the chosen phase (no SUMO)
  4. trigger_emergency  makes a §10 emergency_override appear on the stream
  5. set_topology("222")  changes every junction's lane_count on the stream
  6. get_stats()  returns the §13.1 field set
  7. set_baseline_mode  psychoflow applies; greedy reports "Phase 12" and does not

Not part of §6's folder structure — verification scaffolding, same category as
sim/run_tier0_episode.py and sim/run_prediction_episode.py.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.main import create_app  # noqa: E402
from backend.sim_runner import DEFAULT_CHECKPOINT  # noqa: E402

RULE = "=" * 78
_passed = 0
_failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    tag = "OK  " if ok else "FAIL"
    print(f"  [{tag}] {label}" + (f"  --  {detail}" if detail else ""))
    if ok:
        _passed += 1
    else:
        _failed += 1


def wait_ready(client: TestClient, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = client.get("/health").json()
        if last.get("sim_error"):
            raise RuntimeError(f"sim thread crashed:\n{last['sim_error']}")
        if last.get("sim_ready"):
            return last
        time.sleep(0.5)
    raise TimeoutError(f"sim not ready after {timeout}s (last /health = {last})")


def next_frame(ws, predicate=None, budget: int = 400):
    """Return the next frame (optionally the next matching `predicate`)."""
    last = None
    for _ in range(budget):
        frame = ws.receive_json()
        last = frame
        if predicate is None or predicate(frame):
            return frame
    return last  # budget exhausted — hand back the most recent for diagnostics


# ---------------------------------------------------------------------------
# check 3b — Tier 0 additive lane_weights (pure, no SUMO)
# ---------------------------------------------------------------------------
def check_tier0_bias_param() -> None:
    from agents.rule_based import Tier0Controller
    from safety.validator import _runtime_fixture, _served_fixture, _snapshot_fixture

    served = _served_fixture()          # slot 0 -> n/s, slot 1 -> e/w, per junction
    snap = _snapshot_fixture()          # all lanes 20s wait, 2 halted -> J1 tie -> slot 0
    runtime = _runtime_fixture()
    masks = np.array([1, 1, 0] * 3, dtype=bool)  # 2 valid slots/junction, MAX_PHASES=3

    ctrl = Tier0Controller()
    base_action, _ = ctrl.act(snap, runtime, masks, served)
    biased_action, _ = ctrl.act(
        snap, runtime, masks, served,
        lane_weights={"J1_e0": 10.0, "J1_w0": 10.0},
    )
    check("3b Tier0.act(lane_weights=...) moves the chosen phase",
          base_action[0] == 0 and biased_action[0] == 1,
          f"J1: unbiased slot {base_action[0]} -> biased slot {biased_action[0]}")
    check("3b unbiased call unchanged by the new optional param",
          base_action == ctrl.act(snap, runtime, masks, served)[0])


def main() -> None:
    print(RULE)
    print("PHASE 9 BACKEND SMOKE  —  checkpoint:",
          DEFAULT_CHECKPOINT.name if DEFAULT_CHECKPOINT.exists() else "(missing!)")
    print(RULE)

    check_tier0_bias_param()

    app = create_app(
        checkpoint=DEFAULT_CHECKPOINT,
        lane_counts=(4, 3, 2),
        randomize_density=False,
        spawn_emergencies=False,
        realtime_factor=0.05,
    )

    with TestClient(app) as client:
        health = wait_ready(client)
        check("sim thread came up", health["sim_ready"] and not health["sim_error"],
              f"has_checkpoint={health['has_checkpoint']}")
        check("auto mode has a checkpoint to run", health["has_checkpoint"] is True)

        with client.websocket_connect("/ws") as ws:
            # ---- 1: frame shape ---------------------------------------
            f = next_frame(ws)
            keys = set(f)
            check("1  frame has the §13.2 keys",
                  keys == {"sim_time", "digital_twin", "decision", "narration",
                           "metrics_snapshot"},
                  f"keys={sorted(keys)}")
            dt = f["digital_twin"]
            check("1  digital_twin is §7.6-shaped",
                  dt["corridor_adjacency"] == [["J1", "J2"], ["J2", "J3"]]
                  and set(dt["junctions"]) == {"J1", "J2", "J3"}
                  and all(dt["junctions"][j]["lanes"] for j in ("J1", "J2", "J3")),
                  f"J1 lane_count={dt['junctions']['J1']['lane_count']}")
            dec = f["decision"]
            check("1  decision is §12.1-shaped",
                  {"sim_time", "junction_id", "phase_selected", "score_breakdown",
                   "alternative_scores", "reason"} <= set(dec),
                  f"reason={dec['reason']!r} junction={dec['junction_id']}")
            check("1  metrics_snapshot is §13.2-shaped",
                  set(f["metrics_snapshot"]) ==
                  {"avg_wait", "starvation_events_total", "throughput_total"})

            # ---- 7: baseline swap (before we touch mode) --------------
            r_ok = client.post("/control/set_baseline_mode",
                               json={"baseline": "psychoflow"}).json()
            r_greedy = client.post("/control/set_baseline_mode",
                                   json={"baseline": "greedy"}).json()
            check("7  set_baseline_mode('psychoflow') applies", r_ok["applied"] is True)
            check("7  set_baseline_mode('greedy') is stubbed to Phase 12",
                  r_greedy["applied"] is False and "Phase 12" in r_greedy["reason"],
                  r_greedy["reason"])

            # ---- 2: mode swap changes decision.reason ----------------
            client.post("/control/set_mode", json={"mode": "auto"}).json()
            f_auto = next_frame(
                ws, lambda fr: fr["decision"]["reason"] == "rl_policy", budget=250)
            check("2  auto mode -> 'rl_policy' decisions on the stream",
                  f_auto["decision"]["reason"] == "rl_policy",
                  f"reason={f_auto['decision']['reason']!r}")

            client.post("/control/set_mode", json={"mode": "manual"}).json()
            f_man = next_frame(
                ws,
                lambda fr: fr["decision"]["reason"] in ("wait_time_threshold", "raw_count"),
                budget=250)
            check("2  manual mode -> Tier 0 reasons on the stream",
                  f_man["decision"]["reason"] in ("wait_time_threshold", "raw_count"),
                  f"reason={f_man['decision']['reason']!r}")

            # ---- 6: get_stats field set -----------------------------
            st = client.get("/control/get_stats").json()
            required = {"ready", "sim_time", "mode", "baseline_mode", "lanes",
                        "avg_wait", "starvation_events_total", "throughput_total",
                        "lane_bias", "forced_emergency_lanes"}
            check("6  get_stats() returns the §13.1 field set",
                  st["ready"] and required <= set(st),
                  f"missing={sorted(required - set(st))}")
            any_lane = next(iter(st["lanes"]))
            lane_keys = set(st["lanes"][any_lane])
            check("6  get_stats() per-lane has wait / counts / starvation",
                  {"wait_time_current", "halted_count", "starvation_flag",
                   "wait_time_max_single_vehicle"} <= lane_keys,
                  f"{any_lane}: {sorted(lane_keys)}")

            # ---- 3: set_lane_bias reaches get_stats() and expires ----
            busiest = max(st["lanes"], key=lambda l: st["lanes"][l]["halted_count"])
            client.post("/control/set_lane_bias",
                        json={"lane_id": busiest, "weight": 5.0, "duration_s": 30.0})
            present = False
            for _ in range(40):
                if busiest in client.get("/control/get_stats").json().get("lane_bias", {}):
                    present = True
                    break
                time.sleep(0.1)
            check("3  set_lane_bias shows up in get_stats().lane_bias", present, busiest)
            gone = False
            for _ in range(120):
                if busiest not in client.get("/control/get_stats").json().get("lane_bias", {}):
                    gone = True
                    break
                time.sleep(0.1)
            check("3  lane_bias auto-reverts after duration_s", gone,
                  "expired" if gone else "still present")

            # ---- 4: trigger_emergency -> §10 emergency_override ------
            st = client.get("/control/get_stats").json()
            target = max(st["lanes"], key=lambda l: st["lanes"][l]["halted_count"])
            r_em = client.post("/control/trigger_emergency",
                               json={"lane_id": target}).json()
            check("4  trigger_emergency accepted", r_em["applied"] is True, target)
            f_em = next_frame(
                ws, lambda fr: fr["decision"]["reason"] == "emergency_override",
                budget=200)
            ok_em = f_em["decision"]["reason"] == "emergency_override"
            check("4  emergency_override appears on the decision stream", ok_em,
                  f"junction={f_em['decision'].get('junction_id')} "
                  f"override={f_em['decision'].get('override')}")
            check("4  narration switched to the emergency template",
                  "Emergency override" in f_em["narration"] if ok_em else False,
                  f_em["narration"])

            # ---- 5: set_topology rebuilds the network ---------------
            client.post("/control/set_topology", json={"topology_id": "222"})
            f_topo = next_frame(
                ws,
                lambda fr: all(fr["digital_twin"]["junctions"][j]["lane_count"] == 2
                               for j in ("J1", "J2", "J3")),
                budget=400)
            check("5  set_topology('222') -> every junction lane_count == 2",
                  all(f_topo["digital_twin"]["junctions"][j]["lane_count"] == 2
                      for j in ("J1", "J2", "J3")),
                  {j: f_topo["digital_twin"]["junctions"][j]["lane_count"]
                   for j in ("J1", "J2", "J3")})
            check("5  stream still flowing after the rebuild",
                  next_frame(ws) is not None)

    print(RULE)
    print(f"  {_passed} passed, {_failed} failed")
    print(RULE)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()

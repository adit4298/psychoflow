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
  8. inject_incident (§13.1)  -> the incident rides digital_twin.active_incidents
     and predictions.incident_impact (§8.2); an unknown junction_id is rejected
  4f force_phase / clear_override (§13.1, 2026-08-31)  -> a valid deferred pin
     makes the junction report reason="voice_command" on the stream; a bad
     phase is rejected at the API

The 2026-08-31 backend security hardening has its own targeted harness,
sim/run_backend_security_check.py (offline, no SUMO).

Plus §8.1/§8.2 predictions on the §13.2 frame (2026-08-30):

  P1/P2/P3  SimRunner._predictions() — cold-start empty, a growing queue
            yields a §8.1-shaped `spillover` entry, an active incident
            yields a §8.2-shaped `incident_impact` entry (no SUMO)
  1g        every `predictions` object that rides a live frame is
            §8.1/§8.2-shaped (presence is scenario-dependent, not asserted)

Plus, since the Phase 8 adapter swap (2026-08-29), five checks that each pin a
SPECIFIC regression rather than a general shape:

  1d/1e/1f  _reset_counters() REPLACES the per-episode DecisionLog (no SUMO)
  2b        narration renders {lane} as an index, not a raw SUMO lane id
  4a        the forced lane is chosen OUTSIDE the junction's current green
            set, so 4b/4c actually exercise the failing condition
  4b/4c     a forced-emergency decision names the FORCED lane and its real
            compass direction — the East/West bug the old adapter had, where
            the lane came from the EXECUTED PHASE's served set instead
            (right after an override that set is tied at zero wait across
            BOTH of the phase's approaches, so the direction it printed was
            an arbitrary tie-break)
  4d        an overridden entry carries both `proposed` and `override`
  4e        a §11.2 responder message rides the frame once EMERGENCY_HOLD_S
            expires, with trigger_source="operator"
  5c        sim_time crosses a live episode boundary backwards and the sim
            thread survives it

Not part of §6's folder structure — verification scaffolding, same category as
sim/run_tier0_episode.py and sim/run_prediction_episode.py.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.control_api import EMERGENCY_HOLD_S  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.sim_runner import DEFAULT_CHECKPOINT  # noqa: E402
from twin.digital_twin import CORRIDOR_JUNCTIONS  # noqa: E402

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


# ---------------------------------------------------------------------------
# Finding 1 (b/c) — the backend's auto-mode decisions dict must carry EVERY
# junction, or Phase 8's DecisionLog silently drops a §10 override for the
# deployed (RL) policy. Pure, no SUMO — the Phase 8 harness could not see
# this because it only ever ran Tier 0.
# ---------------------------------------------------------------------------
def check_automode_decisionlog_contract() -> None:
    from backend.sim_runner import SimRunner
    from explainability.decision_log import REASON_RL_POLICY, DecisionLog
    from twin.digital_twin import CORRIDOR_JUNCTIONS

    class _StubModel:
        def predict(self, obs, action_masks=None, deterministic=True):
            return np.array([0, 1, 0]), None

    class _StubEnv:
        def action_masks(self):
            return np.array([1, 1, 0] * 3, dtype=bool)

    r = SimRunner.__new__(SimRunner)          # bypass __init__ (no thread, no state)
    r._mode, r._model, r._env, r._obs = "auto", _StubModel(), _StubEnv(), None
    r._forced_phase = {}                      # §13.1 force_phase pins (none here)
    action, decisions = r._pick_action()
    check("1b sim_runner auto-mode returns a full per-junction decisions dict",
          set(decisions) == set(CORRIDOR_JUNCTIONS)
          and all(d["reason"] == REASON_RL_POLICY for d in decisions.values()),
          f"keys={sorted(decisions)}")

    snap = {"junctions": {j: {"lanes": {f"L_{j}_0": {
        "approach": "north", "halted_count": 1,
        "wait_time_max_single_vehicle": 5.0}}} for j in CORRIDOR_JUNCTIONS}}
    served = {j: {0: frozenset({f"L_{j}_0"}), 1: frozenset()}
              for j in CORRIDOR_JUNCTIONS}
    info = {"safety_overrides": [{
        "junction_id": "J2", "rule": "emergency_override", "from_slot": 1,
        "to_slot": 0, "lane_id": "L_J2_0", "wait_s": 4.0, "outcome": "applied"}]}
    entries = DecisionLog().record_step(100.0, decisions, info, snap, served)
    j2 = next(e for e in entries if e.junction_id == "J2")
    check("1c auto-mode §10 override survives DecisionLog.record_step "
          "(proposed reason preserved as rl_policy)",
          j2.reason == "emergency_override"
          and j2.proposed == {"phase": 1, "reason": REASON_RL_POLICY}
          and j2.phase_selected == 0,
          f"J2 reason={j2.reason} proposed={j2.proposed} exec={j2.phase_selected}")


# ---------------------------------------------------------------------------
# Phase 8 swap — the per-episode DecisionLog lifecycle. env.reset() sends
# sim_time back to ~0 and DecisionLog refuses a backwards sim_time, so a log
# REUSED across a reset raises on the first post-reset step and kills the sim
# thread. _reset_counters() must REPLACE it, not clear it. Pure, no SUMO;
# the live counterpart is check 5c, which drives a real reset.
# ---------------------------------------------------------------------------
def check_predictions_field() -> None:
    """§13.2 `predictions` — §8.1 spillover + §8.2 incident impact, ADDITIVE
    and only when material. Pure, no SUMO: drives `SimRunner._predictions`
    with synthetic §7.6-shaped snapshots.
    """
    from backend.sim_runner import SimRunner, _SPILLOVER_MIN_DELTA
    from prediction.spillover import SpilloverPredictor, _synthetic_snapshot

    r = SimRunner.__new__(SimRunner)          # bypass __init__ (no thread, no env)
    r._spillover_view = SpilloverPredictor()

    # cold start -> §8.1 forces delta to 0.0 -> nothing material -> no key
    p0 = r._predictions(_synthetic_snapshot(0.0, {"J2": 2, "J3": 1}))
    check("P1 _predictions() is empty on a cold-start / quiet frame "
          "(key omitted)", p0 == {}, f"got {p0}")

    # J2 west queue 2 -> 12 over 5s -> +120 veh over the 60s horizon
    p1 = r._predictions(_synthetic_snapshot(5.0, {"J2": 12, "J3": 1}))
    sp = p1.get("spillover", [])
    check("P2 a growing queue streams a §8.1-shaped spillover entry",
          len(sp) >= 1
          and {"from_junction", "to_junction", "horizon_s",
               "predicted_queue_delta", "confidence"} <= set(sp[0])
          and all(abs(f["predicted_queue_delta"]) >= _SPILLOVER_MIN_DELTA
                  for f in sp)
          and "incident_impact" not in p1,
          f"spillover={sp}")

    incident = {
        "incident_id": "inc_0001", "type": "lane_blocked",
        "location": {"junction_id": "J1", "lane_id": "J1_west_0"},
        "severity": "high", "affected_lanes": ["J1_west_0", "J1_west_1"],
        "reported_at_sim_time": 10.0, "estimated_duration_s": 600.0,
    }
    p2 = r._predictions(
        _synthetic_snapshot(10.0, {"J2": 12, "J3": 1}, incidents=[incident]))
    ii = p2.get("incident_impact", [])
    check("P3 an active incident streams a §8.2-shaped incident_impact entry",
          len(ii) == 1
          and {"incident_id", "estimated_affected_junctions",
               "estimated_delay_increase_s", "horizon_s"} <= set(ii[0])
          and ii[0]["incident_id"] == "inc_0001"
          and ii[0]["estimated_affected_junctions"] == ["J1", "J2", "J3"],
          f"incident_impact={ii}")


def check_reset_replaces_log() -> None:
    from backend.sim_runner import SimRunner
    from coordinator.emergency_clearance import EmergencyClearanceCoordinator
    from explainability.decision_log import DecisionLog
    from explainability.query_interface import QueryInterface
    from prediction.spillover import SpilloverPredictor

    class _Twin:
        topology = {"J1": {"lane_approach_map": {"L_J1_0": "north"}}}

    class _Env:
        twin = _Twin()

    r = SimRunner.__new__(SimRunner)          # bypass __init__ (no thread)
    r._env, r._served = _Env(), {"J1": {0: frozenset({"L_J1_0"})}}
    r._spillover_view = SpilloverPredictor()  # _reset_counters() resets it
    r._reset_counters()
    log1, query1, coord1 = r._log, r._query, r._coord
    r._reset_counters()

    check("1d _reset_counters() REPLACES the per-episode DecisionLog / "
          "QueryInterface / clearance coordinator",
          isinstance(log1, DecisionLog)
          and isinstance(query1, QueryInterface)
          and isinstance(coord1, EmergencyClearanceCoordinator)
          and r._log is not log1 and r._query is not query1
          and r._coord is not coord1,
          f"log {id(log1)}->{id(r._log)}")
    check("1e the rebuilt QueryInterface is bound to the NEW log",
          r._query._log is r._log and r._query._log is not log1)

    # And the guard the lifecycle exists for actually bites: the OLD log,
    # having seen a late sim_time, rejects a post-reset one.
    snap = {"junctions": {"J1": {"lanes": {"L_J1_0": {
        "approach": "north", "halted_count": 1,
        "wait_time_max_single_vehicle": 5.0}}}}}
    d = {"J1": {"junction_id": "J1", "phase_selected": 0, "score_breakdown": {},
                "alternative_scores": {}, "reason": "raw_count"}}
    log1.record_step(3145.0, d, {"safety_overrides": []}, snap, r._served)
    reused_raises = False
    try:
        log1.record_step(15.0, d, {"safety_overrides": []}, snap, r._served)
    except ValueError:
        reused_raises = True
    r._log.record_step(15.0, d, {"safety_overrides": []}, snap, r._served)   # fresh: fine
    check("1f a REUSED log rejects the post-reset sim_time; the fresh one accepts it",
          reused_raises and len(r._log) == 1)


def main() -> None:
    print(RULE)
    print("PHASE 9 BACKEND SMOKE  —  checkpoint:",
          DEFAULT_CHECKPOINT.name if DEFAULT_CHECKPOINT.exists() else "(missing!)")
    print(RULE)

    check_tier0_bias_param()
    check_automode_decisionlog_contract()
    check_predictions_field()
    check_reset_replaces_log()

    app = create_app(
        checkpoint=DEFAULT_CHECKPOINT,
        lane_counts=(4, 3, 2),
        randomize_density=False,
        spawn_emergencies=False,
        realtime_factor=0.05,
    )

    runner = app.state.runner
    with TestClient(app) as client:
        health = wait_ready(client)
        check("sim thread came up", health["sim_ready"] and not health["sim_error"],
              f"has_checkpoint={health['has_checkpoint']}")
        check("auto mode has a checkpoint to run", health["has_checkpoint"] is True)

        with client.websocket_connect("/ws") as ws:
            # ---- 1: frame shape ---------------------------------------
            f = next_frame(ws)
            keys = set(f)
            core = {"sim_time", "digital_twin", "decision", "narration",
                    "metrics_snapshot"}
            # `shadow_advisor` (§13.2, read-only advisory) is the third
            # additive key. Its own S1-S6 checks live in
            # sim/run_shadow_advisor_check.py; here it only has to not
            # break the frozen five-key core contract.
            additive = {"responder_messages", "predictions", "shadow_advisor"}
            check("1  frame has the §13.2 five-key core (only additive keys "
                  "beyond it)",
                  core <= keys <= core | additive,
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
            ms = f["metrics_snapshot"]
            check("1  metrics_snapshot is §13.2/§15.2-shaped (no avg_wait)",
                  set(ms) == {"wait_time_variance_across_lanes", "mean_wait_max",
                              "starvation_events_total", "throughput_total"},
                  f"keys={sorted(ms)}")
            check("1  §15.2 metrics populated & plausible on a live frame",
                  isinstance(ms["wait_time_variance_across_lanes"], (int, float))
                  and ms["wait_time_variance_across_lanes"] >= 0.0
                  and isinstance(ms["mean_wait_max"], (int, float))
                  and ms["mean_wait_max"] >= 0.0
                  and ms["throughput_total"] >= 0
                  and ms["starvation_events_total"] >= 0,
                  f"wait_var={ms['wait_time_variance_across_lanes']} "
                  f"mean_wait_max={ms['mean_wait_max']} "
                  f"starv_ev={ms['starvation_events_total']} "
                  f"thru={ms['throughput_total']}")

            # ---- 1g: live `predictions` shape (§8.1 / §8.2) -----------
            # Scenario-dependent whether the spillover threshold is crossed
            # in any given window, so presence is not asserted — but every
            # `predictions` object that DOES ride a frame must be well-formed.
            # (spawn_emergencies=False here and no incident is injected, so
            # incident_impact is exercised by the no-SUMO P3 check above and
            # live by commit 4's inject_incident check.)
            pred_frames = 0
            for _ in range(120):
                fr = ws.receive_json()
                p = fr.get("predictions")
                if p is None:
                    continue
                pred_frames += 1
                ok_shape = (
                    isinstance(p, dict)
                    and set(p) <= {"spillover", "incident_impact"}
                    and p  # never emitted empty
                    and all(
                        {"from_junction", "to_junction", "horizon_s",
                         "predicted_queue_delta", "confidence"} <= set(e)
                        for e in p.get("spillover", [])
                    )
                    and all(
                        {"incident_id", "estimated_affected_junctions",
                         "estimated_delay_increase_s", "horizon_s"} <= set(e)
                        for e in p.get("incident_impact", [])
                    )
                )
                if not ok_shape:
                    check("1g every live `predictions` object is §8.1/§8.2-shaped",
                          False, f"malformed: {p}")
                    break
            else:
                check("1g every live `predictions` object is §8.1/§8.2-shaped",
                      True,
                      f"{pred_frames} frame(s) carried predictions, all well-formed")

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

            # ---- 2b: {lane} is an INDEX, not a raw SUMO lane id -------
            # The deleted adapter passed `_representative_lane`'s raw id
            # into the template, so §12.2's "Lane 3, North" rendered as
            # "Lane N2_J2_1, North". explainability/narrator uses
            # entry.lane_slot, the within-approach index.
            dec_man = f_man["decision"]
            m = re.search(r"\bLane (\d+),", f_man["narration"])
            check("2b narration renders {lane} as a within-approach INDEX, "
                  "not a raw SUMO lane id",
                  m is not None
                  and int(m.group(1)) == dec_man.get("lane_slot")
                  and str(dec_man.get("lane_id")) not in f_man["narration"],
                  f"narration={f_man['narration']!r} lane_slot="
                  f"{dec_man.get('lane_slot')} lane_id={dec_man.get('lane_id')}")

            # ---- 6: get_stats field set -----------------------------
            st = client.get("/control/get_stats").json()
            required = {"ready", "sim_time", "mode", "baseline_mode", "lanes",
                        "wait_time_variance_across_lanes", "mean_wait_max",
                        "starvation_events_total", "throughput_total",
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

            # ---- 8: inject_incident (§13.1) -> live §7.3 + §8.2 -------
            # The live trigger for "detects incidents": before this the
            # twin's active_incidents is always empty. After it, the
            # incident must ride digital_twin.active_incidents AND appear in
            # predictions.incident_impact (§8.2) on the stream.
            inc_lane = any_lane
            inc_j = st["lanes"][inc_lane]["junction_id"]
            r_bad = client.post("/control/inject_incident",
                                json={"junction_id": "J9",
                                      "affected_lanes": [inc_lane]}).json()
            check("8  inject_incident rejects an unknown junction_id",
                  r_bad["applied"] is False and "junction_id" in r_bad["reason"],
                  r_bad["reason"])
            r_inc = client.post("/control/inject_incident", json={
                "junction_id": inc_j, "affected_lanes": [inc_lane],
                "incident_type": "lane_blocked", "severity": "high",
                "estimated_duration_s": 600.0,
            }).json()
            check("8  inject_incident accepted",
                  r_inc["applied"] is True
                  and r_inc["incident"]["location"]["junction_id"] == inc_j,
                  f"{r_inc}")
            f_inc = next_frame(
                ws, lambda fr: fr["digital_twin"].get("active_incidents"),
                budget=200)
            active = f_inc["digital_twin"].get("active_incidents") or []
            check("8  the incident rides digital_twin.active_incidents",
                  len(active) >= 1
                  and active[0]["location"]["junction_id"] == inc_j
                  and inc_lane in active[0]["affected_lanes"],
                  f"active_incidents={active}")
            f_ii = next_frame(
                ws,
                lambda fr: fr.get("predictions", {}).get("incident_impact"),
                budget=200)
            ii = f_ii.get("predictions", {}).get("incident_impact") or []
            inc_id = active[0]["incident_id"] if active else None
            check("8  §8.2 incident_impact for it appears in predictions",
                  any(e["incident_id"] == inc_id
                      and e["estimated_affected_junctions"][0] == inc_j
                      and e["estimated_delay_increase_s"] > 0
                      for e in ii),
                  f"incident_impact={ii}")

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
            # Force a lane the junction's CURRENT green does NOT serve, and
            # whose approach differs from every lane it does. That is the
            # exact condition the deleted `_representative_lane` adapter got
            # wrong (it named the busiest lane of the EXECUTED PHASE's
            # served set, which right after an override is a set of tied
            # zero-wait lanes spanning BOTH approaches of that phase — so
            # the compass direction it printed was an arbitrary tie-break,
            # not the forced lane's). Forcing merely "the busiest lane"
            # would often be a lane already being served, where the old and
            # new rules agree by luck and the check proves nothing.
            #
            # Reads two plain Python attributes off the runner (a static
            # dict and an int). No TraCI call, so §13's single-thread
            # boundary is intact.
            st = client.get("/control/get_stats").json()
            snap0 = next_frame(ws)["digital_twin"]
            target = None
            for jid in CORRIDOR_JUNCTIONS:
                jlanes = snap0["junctions"][jid]["lanes"]
                cur = runner._env._phase_state[jid].cur_slot
                cur_served = runner._served[jid].get(cur, frozenset())
                cur_dirs = {jlanes[l]["approach"] for l in cur_served if l in jlanes}
                outside = sorted(
                    l for l in jlanes
                    if l not in cur_served and jlanes[l]["approach"] not in cur_dirs
                )
                if outside:
                    target = outside[0]
                    check(f"4a forcing a lane OUTSIDE {jid}'s current green "
                          f"set (the condition the old adapter got wrong)",
                          True,
                          f"{jid} slot {cur} serves {sorted(cur_dirs)}; forcing "
                          f"{target} ({jlanes[target]['approach']})")
                    break
            if target is None:      # no such lane this instant — do not skip
                target = max(st["lanes"], key=lambda l: st["lanes"][l]["halted_count"])
                check("4a forcing a lane OUTSIDE the current green set", False,
                      f"none available this step; fell back to busiest ({target})")
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

            # ---- 4b/4c: THE LIVE REGRESSION FOUND LAST SESSION -------
            # The deleted adapter named the lane the EXECUTED PHASE serves,
            # so forcing a lane OUTSIDE the current green set narrated the
            # wrong lane and the wrong compass direction (the East/West
            # bug). §12.1's DecisionLog takes the lane from the §10
            # OverrideRecord instead, so the entry and the narration both
            # name the lane the operator actually forced.
            dec_em = f_em["decision"]
            forced_dir = st["lanes"][target]["approach"]
            check("4b emergency entry names the FORCED lane, not the "
                  "served one",
                  dec_em.get("lane_id") == target
                  and dec_em.get("override", {}).get("lane_id") == target,
                  f"forced={target} entry.lane_id={dec_em.get('lane_id')} "
                  f"override.lane_id={dec_em.get('override', {}).get('lane_id')}")
            check("4c narration carries the FORCED lane's real direction",
                  forced_dir.capitalize() in f_em["narration"],
                  f"forced lane {target} approach={forced_dir} :: "
                  f"{f_em['narration']!r}")

            # ---- 4d: an overridden step carries BOTH sides -----------
            check("4d overridden entry carries proposed AND override",
                  "proposed" in dec_em and "override" in dec_em
                  and dec_em["proposed"]["reason"] not in
                      ("emergency_override", "starvation_ceiling")
                  and set(dec_em["override"]) >= {"rule", "lane_id", "wait_s",
                                                  "from_slot", "to_slot",
                                                  "outcome"},
                  f"proposed={dec_em.get('proposed')} "
                  f"override={dec_em.get('override')}")

            # ---- 4e: §11.2 responder message after the hold expires ---
            # trigger_emergency has no natural release, so the force is
            # dropped after EMERGENCY_HOLD_S sim-seconds; §11.1's clearance
            # episode closes then and §11.2's message rides the frame.
            f_msg = next_frame(ws, lambda fr: fr.get("responder_messages"),
                               budget=300)
            msgs = f_msg.get("responder_messages") or []
            check("4e a §11.2 responder message appears after "
                  f"EMERGENCY_HOLD_S ({EMERGENCY_HOLD_S:.0f}s) expires",
                  bool(msgs), f"frames carried {len(msgs)} message(s)")
            if msgs:
                msg = msgs[0]
                check("4e  message is §11.2-shaped, operator-triggered, "
                      "with a real clearance time",
                      msg["event"] == "emergency_clearance"
                      and msg["lane_id"] == target
                      and msg["trigger_source"] == "operator"
                      and isinstance(msg["clearance_time_s"], (int, float))
                      and msg["clearance_time_s"] >= 0.0
                      and msg["baseline_is_estimate"] is True,
                      f"junction={msg['junction_id']} lane={msg['lane_id']} "
                      f"clearance={msg['clearance_time_s']}s "
                      f"trigger_source={msg['trigger_source']!r}")
                # Printed, not asserted — clearance_time_s is scenario-
                # dependent and pinning a value would be pinning the draw.
                # Read it with `from_slot` in mind: that field is WHAT WAS
                # PROPOSED (safety/validator.py:98), not the current green.
                # So an override reading `0->1` on a frame whose message
                # says "already clear" is consistent, not contradictory —
                # the junction was already serving the forced lane when the
                # force landed (hence served_on_arrival, clearance 0.0s),
                # and the override is §10 rewriting a LATER proposal that
                # would have moved the green off it.
                print(f"         detection t={msg['sim_time']}  "
                      f"emergency-frame t={f_em['sim_time']}  "
                      f"override {dec_em['override']['from_slot']}->"
                      f"{dec_em['override']['to_slot']} "
                      f"({dec_em['override']['outcome']})")
                print(f"         summary: {msg['summary']}")

            # ---- 4f: force_phase / clear_override (§13.1, 2026-08-31) ----
            # New control endpoints. force_phase is DEFERRED and MASK-CHECKED
            # on the sim thread; when a valid pin lands, that junction's
            # decision on the stream carries reason="voice_command". A bad
            # phase is rejected at the API. clear_override cancels the pin.
            r_fp_bad = client.post("/control/force_phase",
                                   json={"junction_id": "J1", "phase": 9}).json()
            check("4f force_phase rejects a phase outside [0, MAX_PHASES)",
                  r_fp_bad["applied"] is False, r_fp_bad.get("reason", ""))
            # The valid green-slot set for J2 is topology-dependent (2 or 3),
            # and which slots the mask permits shifts with min-green — so try
            # each candidate slot and take the first that actually lands as a
            # voice_command decision on the stream. _emit_junction now
            # surfaces a force_phase ahead of an ordinary switch, so a valid
            # pin shows within a step or two.
            f_fp = None
            for slot in (0, 1, 2):
                r_fp = client.post("/control/force_phase",
                                   json={"junction_id": "J2",
                                         "phase": slot}).json()
                if not r_fp.get("applied"):
                    continue
                cand = next_frame(
                    ws,
                    lambda fr: fr["decision"]["junction_id"] == "J2"
                    and fr["decision"]["reason"] == "voice_command",
                    budget=60)
                if (cand["decision"]["junction_id"] == "J2"
                        and cand["decision"]["reason"] == "voice_command"):
                    f_fp = cand
                    break
                client.post("/control/clear_override",
                            json={"junction_id": "J2"})
            check("4f a valid force_phase surfaces J2 with "
                  "reason='voice_command' on the stream",
                  f_fp is not None,
                  "" if f_fp else "no slot in {0,1,2} landed as voice_command")
            if f_fp is not None:
                check("4f the voice_command narration reads back the operator "
                      "action",
                      "Voice command received" in f_fp["narration"],
                      f_fp["narration"])
                check("4f the forced entry carries phase_selected == the pin",
                      f_fp["decision"]["phase_selected"] == slot
                      and f_fp["decision"].get("action_taken")
                      == f"force_phase(J2, {slot})",
                      f"phase_selected={f_fp['decision'].get('phase_selected')} "
                      f"action_taken={f_fp['decision'].get('action_taken')!r}")
            r_co = client.post("/control/clear_override",
                               json={"junction_id": "J2"}).json()
            check("4f clear_override accepted", r_co["applied"] is True, str(r_co))

            # ---- 5: set_topology rebuilds the network ---------------
            pre_reset_t = next_frame(ws)["sim_time"]
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

            # ---- 5c: sim_time monotonicity across a LIVE episode
            # boundary. The rebuild above ran env.reset(), so sim_time goes
            # BACKWARDS on the wire. That is the one thing DecisionLog
            # refuses, so if _reset_counters() did not replace the log,
            # record_step would raise inside the sim thread, the thread
            # would die and /health would carry the traceback. Surviving
            # the reset with frames still arriving IS the check.
            post = [next_frame(ws) for _ in range(4)]
            post_ts = [fr["sim_time"] for fr in post]
            health = client.get("/health").json()
            check("5c sim_time went backwards across the reset (a real "
                  "episode boundary was crossed)",
                  post_ts[0] < pre_reset_t,
                  f"{pre_reset_t} -> {post_ts[0]}")
            check("5c the sim thread survived it — the per-episode "
                  "DecisionLog was replaced, not reused",
                  not health.get("sim_error") and health.get("sim_ready"),
                  (health.get("sim_error") or "no sim_error").splitlines()[-1])
            check("5c decisions are monotonic again after the boundary",
                  post_ts == sorted(post_ts)
                  and all(fr["decision"]["sim_time"] == fr["sim_time"]
                          for fr in post),
                  f"post-reset sim_times={post_ts}")

    print(RULE)
    print(f"  {_passed} passed, {_failed} failed")
    print(RULE)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    # Narration (§12.2) is UTF-8 (the voice template carries a "→"); keep the
    # harness printable when stdout is redirected to a cp1252 pipe on Windows.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # Tier 1 SUMO beacon (sim/sumo_activity.py): refuse to launch
    # concurrent SUMO while a training run or the backend is live.
    from sim.sumo_activity import require_free
    require_free('backend smoke test')
    main()

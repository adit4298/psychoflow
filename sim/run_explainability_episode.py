"""§18 Phase 8 verification harness — Coordinator + Explainability (§11, §12).

Done bar: "full decision log renders correctly for a rule-based test run
before the RL agent is even wired in."

Runs TWO rule-based segments (no trained checkpoint anywhere):

  * Tier 0, corridor 4/3/2, seed 7, spawn_emergencies=True, validator ON,
    with a deterministic manual ambulance injection on J2 north (route
    r_ns2) plus env.forced_emergency_lanes set for the transit window
    (§13.1's operator-trigger path) — guarantees the emergency_override
    reconciliation path is exercised under a rule-based controller.
  * An adversarial rule-based controller that deliberately starves J2
    north (seed 31) — the only way to exercise real starvation_ceiling
    entries on this corridor, since Tier 0 never trips the ceiling here
    (§16).

Then it feeds every step into §12.1's DecisionLog and §11.1's
EmergencyClearanceCoordinator, renders entries through §12.2's narrator,
answers §12.3 "why" queries, and builds §11.2 responder messages.

Not part of §6's folder structure — verification scaffolding, same
category as sim/run_tier0_episode.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # narration is UTF-8

import numpy as np  # noqa: E402
import traci  # noqa: E402

from agents.rule_based import Tier0Controller  # noqa: E402
from coordinator.emergency_clearance import EmergencyClearanceCoordinator  # noqa: E402
from coordinator.responder_messaging import (  # noqa: E402
    build_responder_message,
    estimate_baseline_clearance_s,
)
from env.psychoflow_env import PsychoFlowEnv, ScenarioConfig  # noqa: E402
from explainability.decision_log import (  # noqa: E402
    REASON_EMERGENCY_OVERRIDE,
    REASON_RAW_COUNT,
    REASON_RL_POLICY,
    REASON_STARVATION_CEILING,
    REASON_VOICE_COMMAND,
    REASON_WAIT_THRESHOLD,
    REASONS,
    SCORED_REASONS,
    DecisionLog,
    DecisionLogEntry,
)
from explainability.narrator import narrate  # noqa: E402
from explainability.query_interface import QueryInterface  # noqa: E402
from safety.validator import OUTCOME_DEFERRED_MIN_GREEN  # noqa: E402
from sim.run_tier0_episode import AdversarialController, victim_lanes_for  # noqa: E402
from twin.digital_twin import CORRIDOR_JUNCTIONS  # noqa: E402

RULE = "=" * 78
INJECT_AT_S = 130.0
FORCED_WINDOW_S = 100.0


# --------------------------------------------------------------------------
# Segment 1 — Tier 0 with a forced emergency
# --------------------------------------------------------------------------
def drive_tier0():
    print(RULE)
    print("SEGMENT 1 — Tier 0, corridor 4/3/2, seed 7, validator ON")
    print("            + manual ambulance on J2 north @ t>=130s (forced_emergency_lanes)")
    print(RULE)

    env = PsychoFlowEnv(
        scenario_config=ScenarioConfig(lane_counts=(4, 3, 2), spawn_emergencies=True),
        seed=7,
    )
    env.reset()
    served = env.phase_served_lanes()
    topology = env.twin.topology
    victim = victim_lanes_for(env, "J2", "north")

    controller = Tier0Controller()
    log = DecisionLog()
    coord = EmergencyClearanceCoordinator(served)

    injected = False
    inject_t: float | None = None
    n_override_records = 0
    steps = 0

    while True:
        snapshot = env.twin.snapshot
        runtime = env._runtime()
        masks = env.action_masks()
        sim_time = snapshot["sim_time"]

        if not injected and sim_time >= INJECT_AT_S:
            traci.switch(env._label)
            traci.vehicle.add(
                "amb_probe", routeID="r_ns2", typeID="ambulance",
                depart="now", departLane="best", departPos="free", departSpeed="max",
            )
            env.forced_emergency_lanes = frozenset(victim)
            injected, inject_t = True, sim_time
            print(f"  t={sim_time:6.0f}s  injected ambulance on J2 north; "
                  f"forced_emergency_lanes={sorted(victim)}")
        if injected and inject_t is not None and env.forced_emergency_lanes \
                and sim_time >= inject_t + FORCED_WINDOW_S:
            env.forced_emergency_lanes = frozenset()
            print(f"  t={sim_time:6.0f}s  cleared forced_emergency_lanes")

        action, decisions = controller.act(snapshot, runtime, masks, served)
        obs, reward, terminated, truncated, info = env.step(action)
        steps += 1
        n_override_records += len(info["safety_overrides"])

        log.record_step(info["sim_time"], decisions, info, snapshot, served)
        coord.observe(info["sim_time"], env.twin.snapshot, env._runtime(), info)

        if terminated or truncated:
            break

    coord.finalize(info["sim_time"])
    env.close()
    print(f"\n  episode: {steps} steps, {info['sim_time']:.0f}s, "
          f"terminated={terminated} truncated={truncated}")
    print(f"  §10 override records across episode: {n_override_records}")
    print(f"  clearance episodes completed: {len(coord.completed)}")
    return {
        "log": log, "coord": coord, "served": served, "topology": topology,
        "steps": steps, "n_override_records": n_override_records,
        "inject_t": inject_t,
    }


# --------------------------------------------------------------------------
# Segment 2 — adversarial starvation (for real starvation_ceiling entries)
# --------------------------------------------------------------------------
def drive_adversarial(horizon_s: float = 1200.0):
    print(RULE)
    print("SEGMENT 2 — adversarial controller starves J2 north, seed 31, validator ON")
    print(RULE)

    env = PsychoFlowEnv(scenario_config=ScenarioConfig(lane_counts=(4, 3, 2)), seed=31)
    env.reset()
    victim = victim_lanes_for(env, "J2", "north")
    env.close()

    env = PsychoFlowEnv(scenario_config=ScenarioConfig(lane_counts=(4, 3, 2)), seed=31)
    env.reset()
    served = env.phase_served_lanes()
    controller = AdversarialController("J2", victim, np.random.default_rng(31))

    log = DecisionLog()
    steps = 0
    n_override_records = 0
    while True:
        snapshot = env.twin.snapshot
        runtime = env._runtime()
        masks = env.action_masks()

        action, _ = controller.act(snapshot, runtime, masks, served)
        # The adversarial stub carries no score breakdown; represent each
        # junction's raw pick, and let record_step() overlay the §10
        # ceiling override where it fires.
        decisions = {
            jid: {
                "junction_id": jid, "phase_selected": int(action[i]),
                "score_breakdown": {}, "alternative_scores": {},
                "reason": REASON_RAW_COUNT,
            }
            for i, jid in enumerate(CORRIDOR_JUNCTIONS)
        }
        obs, reward, terminated, truncated, info = env.step(action)
        n_override_records += len(info["safety_overrides"])
        log.record_step(info["sim_time"], decisions, info, snapshot, served)
        steps += 1
        if terminated or truncated or info["sim_time"] >= horizon_s:
            break

    env.close()
    ceiling = [e for e in log.entries if e.reason == REASON_STARVATION_CEILING]
    print(f"\n  episode: {steps} steps, {info['sim_time']:.0f}s")
    print(f"  §10 override records: {n_override_records}  "
          f"({len(ceiling)} starvation_ceiling entries in the log)")
    return {"log": log, "steps": steps}


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------
def _phase_key_ok(entry: DecisionLogEntry) -> bool:
    """If an entry claims per-phase alternative scores, the executed phase
    must be among them. A producer with no scoring (the adversarial stub,
    later rl_policy) carries an empty dict and is exempt."""
    if entry.reason not in SCORED_REASONS or entry.override is not None:
        return True
    if not entry.alternative_scores:
        return True
    return f"phase_{entry.phase_selected}" in entry.alternative_scores


def _check_entries(entries, label: str) -> None:
    last_t = -1.0
    for e in entries:
        assert e.reason in REASONS, e.reason
        assert isinstance(e.phase_selected, int)
        assert isinstance(e.score_breakdown, dict)
        assert isinstance(e.alternative_scores, dict)
        assert e.sim_time >= last_t - 1e-6, f"{label}: sim_time went backwards"
        last_t = max(last_t, e.sim_time)
        assert _phase_key_ok(e), (
            f"{label}: executed phase {e.phase_selected} not in "
            f"alternative_scores {list(e.alternative_scores)} "
            f"(junction {e.junction_id}, t={e.sim_time})"
        )
        d = e.to_dict()
        for k in ("sim_time", "junction_id", "phase_selected",
                  "score_breakdown", "alternative_scores", "reason"):
            assert k in d, k
        # override reason iff an override block is attached
        has_ovr_reason = e.reason in (REASON_EMERGENCY_OVERRIDE,
                                      REASON_STARVATION_CEILING)
        assert has_ovr_reason == (e.override is not None), (
            f"{label}: reason/override mismatch at {e.junction_id} t={e.sim_time}"
        )


def verify_decision_log(tier0, adversarial) -> None:
    print(RULE)
    print("STEP 3 — decision-log structural verification")
    print(RULE)

    log: DecisionLog = tier0["log"]
    entries = list(log.entries)

    # (a) one entry per junction per decision step
    expected = tier0["steps"] * len(CORRIDOR_JUNCTIONS)
    assert len(entries) == expected, f"{len(entries)} entries != {expected}"
    print(f"  [OK] {len(entries)} entries = {tier0['steps']} steps x "
          f"{len(CORRIDOR_JUNCTIONS)} junctions")

    # (b) every entry well-formed — both segment logs
    _check_entries(entries, "tier0")
    _check_entries(list(adversarial["log"].entries), "adversarial")
    print(f"  [OK] all {len(entries)} tier0 + "
          f"{len(adversarial['log'].entries)} adversarial entries carry the "
          f"§12.1 schema; reasons in enum; sim_time monotonic; phase keys "
          f"consistent; reason<->override block agree")

    # (c) override reconciliation
    overridden = [e for e in entries if e.override is not None]
    for e in overridden:
        assert e.reason in (REASON_EMERGENCY_OVERRIDE, REASON_STARVATION_CEILING)
        assert e.reason == e.override["rule"]
        assert e.proposed is not None
        if e.override["outcome"] == OUTCOME_DEFERRED_MIN_GREEN:
            assert e.phase_selected == e.proposed["phase"], "deferred must not change phase"
        else:
            assert e.phase_selected == e.override["to_slot"], "executed != override target"
    non_override_with_ovr_reason = [
        e for e in entries
        if e.override is None
        and e.reason in (REASON_EMERGENCY_OVERRIDE, REASON_STARVATION_CEILING)
    ]
    assert not non_override_with_ovr_reason, "override reason without an override block"
    print(f"  [OK] {len(overridden)} overridden entries reconcile "
          f"(proposed vs executed vs reason vs outcome)")

    # (d) count reconciliation against the raw info stream
    assert len(overridden) == tier0["n_override_records"], (
        f"{len(overridden)} override entries != "
        f"{tier0['n_override_records']} raw §10 records"
    )
    print(f"  [OK] override-entry count == raw §10 record count "
          f"({tier0['n_override_records']})")

    # (e) the two override reasons are actually present (segments engineered to)
    n_emerg = sum(1 for e in entries if e.reason == REASON_EMERGENCY_OVERRIDE)
    n_ceil = sum(1 for e in adversarial["log"].entries
                 if e.reason == REASON_STARVATION_CEILING)
    assert n_emerg >= 1, "Tier 0 segment produced no emergency_override entry"
    assert n_ceil >= 1, "adversarial segment produced no starvation_ceiling entry"
    print(f"  [OK] emergency_override entries: {n_emerg} (Tier 0 seg); "
          f"starvation_ceiling entries: {n_ceil} (adversarial seg)")

    # jsonl dump renders
    out = REPO_ROOT / "training" / "checkpoints" / "_sweeps" / "phase8_decision_log.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    log.to_jsonl(out)
    n_lines = len(out.read_text(encoding="utf-8").strip().splitlines())
    assert n_lines == len(entries)
    print(f"  [OK] to_jsonl -> {out.relative_to(REPO_ROOT)} ({n_lines} lines)")

    # readable sample
    print("\n  --- first 2 decision steps (6 entries) ---")
    for e in entries[:6]:
        print(f"    t={e.sim_time:6.0f}  {e.junction_id}  phase={e.phase_selected}  "
              f"reason={e.reason:<20}  alts={e.alternative_scores}")
    print("  --- first 3 overridden entries ---")
    for e in overridden[:3]:
        print(f"    t={e.sim_time:6.0f}  {e.junction_id}  proposed phase "
              f"{e.proposed['phase']} ({e.proposed['reason']}) -> executed "
              f"{e.phase_selected}  [{e.reason}/{e.override['outcome']}] "
              f"lane={e.override['lane_id']} wait={e.override['wait_s']:.0f}s")


def verify_narration(tier0, adversarial) -> None:
    print("\n" + RULE)
    print("STEP 4 — §12.2 narration (one line per reason)")
    print(RULE)

    seen: dict[str, DecisionLogEntry] = {}
    for e in list(tier0["log"].entries) + list(adversarial["log"].entries):
        seen.setdefault(e.reason, e)
        if e.reason == REASON_STARVATION_CEILING and e.override:
            seen[e.reason] = e  # prefer one carrying the wait_s

    # A real voice entry via the actual API. Recorded at the log's own
    # watermark rather than a literal mid-episode time: DecisionLog now
    # refuses a backwards sim_time (one log per episode, §12.3's at-or-before
    # queries read it positionally), and this call lands AFTER the segment's
    # last decision step.
    voice_t = tier0["log"].latest().sim_time
    voice_entry = tier0["log"].record_voice(
        voice_t, "J2", "switch to manual mode", "set_mode(manual)"
    )
    seen[REASON_VOICE_COMMAND] = voice_entry

    for reason in sorted(REASONS):
        if reason in seen:
            entry, tag = seen[reason], "real"
        elif reason == REASON_RL_POLICY:
            entry = DecisionLogEntry(
                sim_time=1500.0, junction_id="J2", phase_selected=1,
                score_breakdown={}, alternative_scores={}, reason=REASON_RL_POLICY,
                lane_id="J1_J2_0", direction="west", lane_slot=0,
            )
            tag = "synthetic — producer is Phase 9 auto mode"
        else:
            raise AssertionError(f"no entry for reason {reason} and no synthetic path")
        line = narrate(entry)
        assert isinstance(line, str) and line.strip()
        print(f"  [{reason:>20}] ({tag})")
        print(f"       {line}")

    # Every enum member has a template (loop above would have raised).
    print(f"\n  [OK] all {len(REASONS)} reasons render; unknown reason raises "
          f"(see `python -m explainability.narrator`)")


def verify_queries(tier0) -> None:
    print("\n" + RULE)
    print("STEP 5 — §12.3 'why did you do that?' queries")
    print(RULE)

    log: DecisionLog = tier0["log"]
    qi = QueryInterface.from_twin_topology(log, tier0["topology"])

    # (a) mid-episode decision in force at a between-steps time
    r = qi.why(sim_time=203.0, junction_id="J1")
    assert r["sim_time"] <= 203.0
    assert r["entry"]["reason"] in REASONS
    print(f"  why(t=203, J1) -> t={r['sim_time']:.0f}, reason={r['entry']['reason']}")
    print(f"     {r['narration']}")

    # (b) latest decision for a junction
    r = qi.why(junction_id="J3")
    assert r["sim_time"] == log.latest("J3").sim_time
    print(f"  why(latest, J3) -> t={r['sim_time']:.0f}")
    print(f"     {r['narration']}")

    # (c) the emergency window — must pull the real emergency_override entry
    inject_t = tier0["inject_t"]
    r = qi.why(sim_time=inject_t + 40.0, junction_id="J2")
    assert r["entry"]["reason"] == REASON_EMERGENCY_OVERRIDE, r["entry"]["reason"]
    # returned entry is the actual logged object, not a template stand-in
    logged = [e for e in log.entries
              if e.junction_id == "J2" and e.sim_time == r["sim_time"]][-1]
    assert r["entry"] == logged.to_dict()
    print(f"  why(t={inject_t + 40:.0f}, J2) -> t={r['sim_time']:.0f}, "
          f"reason={r['entry']['reason']}  (real logged entry, not canned)")
    print(f"     {r['narration']}")

    # (d) a lane_id resolves its junction
    lane = next(iter(tier0["served"]["J2"][0]))
    r = qi.why(sim_time=inject_t + 40.0, lane_id=lane)
    assert r["junction_id"] == "J2"
    print(f"  why(lane={lane}) -> resolved junction {r['junction_id']}")

    print(f"\n  [OK] queries pull real §12.1 entries and render via §12.2")


def verify_responder(tier0) -> None:
    print("\n" + RULE)
    print("STEP 6 — §11.2 responder messages from clearance events")
    print(RULE)

    coord: EmergencyClearanceCoordinator = tier0["coord"]
    served = tier0["served"]

    resolved = [e for e in coord.completed if e.clearance_time_s is not None]
    assert resolved, "no clearance episode resolved to a green — cannot build a message"
    print(f"  {len(coord.completed)} clearance episodes, {len(resolved)} resolved\n")

    for ev in resolved:
        n_green = len(served[ev.junction_id])
        # Conservative baseline: assume the target phase is last in rotation
        # and the current phase just started (time_since_switch_s = 0).
        baseline = estimate_baseline_clearance_s(0.0, n_green)
        msg = build_responder_message(ev, baseline)

        assert msg["event"] == "emergency_clearance"
        assert 0.0 <= msg["clearance_time_s"] <= 20.0, msg["clearance_time_s"]
        recomputed = round(100.0 * (1.0 - msg["clearance_time_s"] / baseline), 1)
        assert msg["improvement_pct"] == recomputed
        assert msg["baseline_is_estimate"] is True
        print(json.dumps(msg, indent=2, sort_keys=True))
        print()

    print(f"  [OK] {len(resolved)} responder message(s); clearance_time_s real "
          f"& within the per-junction bound; improvement_pct arithmetic checks")


# --------------------------------------------------------------------------
def main() -> None:
    tier0 = drive_tier0()
    print()
    adversarial = drive_adversarial()
    print()
    verify_decision_log(tier0, adversarial)
    verify_narration(tier0, adversarial)
    verify_queries(tier0)
    verify_responder(tier0)
    print("\n" + RULE)
    print("§18 Phase 8 harness: all checks passed.")
    print(RULE)


if __name__ == "__main__":
    # Tier 1 SUMO beacon (sim/sumo_activity.py): refuse to launch
    # concurrent SUMO while a training run or the backend is live.
    from sim.sumo_activity import require_free
    require_free('explainability episode')
    main()

"""Self-test for the orchestrator blackboard (Part 4b done-bar).

    python -m orchestrator.selftest          [--w1 ... --w9]

NO SUMO. Everything runs against a synthetic §7.6 snapshot, so this is safe to
run at any time and needs no `sim.sumo_activity` beacon check — same category
as `training/scripts/stage4_contamination.py`.

The done-bar asks for three things; W1/W2 cover the first two and W4 covers
the third:

  (i)   each agent ticks                    -> W1
  (ii)  each publishes to the blackboard    -> W2
  (iii) the Supervisor veto appears in the recorded trace -> W4

W4 IS DELIBERATELY THREE CHECKS, because this repo has a documented history of
"a verification run that passes while proving nothing":
  W4a  zero overrides  -> ZERO veto rows      (kills "always emits a veto")
  W4b  two overrides   -> EXACTLY two, field-for-field, and in the TRACE
  W4c  an override naming a lane that exists NOWHERE in the snapshot is still
       reported verbatim — which only a REPORTER can do. A Supervisor that
       re-derived its own judgement could not produce that string.
"""

from __future__ import annotations

import ast
import copy
import json
import sys

from agents.incident_priority_scenarios import _lane, _synthetic_snapshot
from orchestrator.blackboard import Blackboard
from orchestrator.bus import Orchestrator
from orchestrator.types import (
    AGENT_NAMES,
    AGENT_SUPERVISOR,
    ENTRY_KEYS,
    DETAIL_MAX_KEYS,
    KIND_IDLE,
    KIND_VETO,
    PER_FRAME_BYTE_BUDGET,
    SAID_MAX_CHARS,
    AgentContext,
    AgentEntry,
    BlackboardError,
)
from orchestrator.wrappers import _Wrapper, default_wrappers

_passed = 0
_failed = 0
RULE = "=" * 74


def check(label: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  — {detail}" if detail else ""))
    if ok:
        _passed += 1
    else:
        _failed += 1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
VISION_SOURCE_TAG = "vision_mock"        # == perception.vision_mock.SOURCE_TAG
VISION_CONFIDENCE = 0.91                 # inside CONFIDENCE_RANGE (0.85, 0.98)


def _with_vision(snapshot: dict) -> dict:
    """Attach a §7.2-shaped block per lane (`_synthetic_snapshot` has none).

    Duplicates the §7.2 envelope as a literal because importing
    `perception.vision_mock` would pull in `traci`; `_check_vision_drift`
    compares against the real constants when they happen to be importable.
    """
    out = copy.deepcopy(snapshot)
    for block in out["junctions"].values():
        block["vision"] = {
            lane_id: {**reading, "confidence": VISION_CONFIDENCE,
                      "source": VISION_SOURCE_TAG}
            for lane_id, reading in block["lanes"].items()
        }
    return out


def _corridor(ambulance: bool = False, congested: bool = True) -> dict:
    j2 = [
        _lane("W1_J2_0", "west", vehicle_count=12, halted_count=8,
              wait_current=180.0, wait_max=95.0, starved=congested),
        _lane("N1_J2_0", "north", vehicle_count=7, halted_count=5,
              wait_current=140.0, wait_max=100.0, starved=congested),
        _lane("E1_J2_0", "east", vehicle_count=1, halted_count=0, wait_max=4.0,
              ambulance=1 if ambulance else 0),
    ]
    return _with_vision(_synthetic_snapshot(120.0, {
        "J1": [_lane("W1_J1_0", "west", vehicle_count=3, halted_count=1,
                     wait_max=12.0)],
        "J2": j2,
        "J3": [_lane("W1_J3_0", "west", vehicle_count=2, halted_count=1,
                     wait_max=8.0)],
    }))


def _override(junction_id: str, rule: str, lane_id: str, *, wait_s: float,
              from_slot: int, to_slot: int, outcome: str = "applied") -> dict:
    """An `OverrideRecord.to_dict()`-shaped row."""
    return {"junction_id": junction_id, "rule": rule, "lane_id": lane_id,
            "wait_s": wait_s, "from_slot": from_slot, "to_slot": to_slot,
            "outcome": outcome}


def _info(sim_time: float, executed=(0, 1, 0), overrides=()) -> dict:
    return {"sim_time": sim_time, "executed_action": list(executed),
            "safety_overrides": list(overrides), "arrived_total": 120,
            "reward_breakdown": {}}


def _decisions(reason: str = "rl_policy") -> dict:
    return {jid: {"junction_id": jid, "phase_selected": i % 2,
                  "score_breakdown": {}, "alternative_scores": {},
                  "reason": reason}
            for i, jid in enumerate(("J1", "J2", "J3"))}


def _ctx(snapshot, info, *, step=7, predictions=None, forced=frozenset(),
         mode="auto") -> AgentContext:
    return AgentContext(
        step=step, sim_time=info["sim_time"], pre_snapshot=snapshot, info=info,
        decisions=_decisions(), predictions=predictions or {},
        forced_emergency_lanes=forced, mode=mode,
    )


PREDICTIONS = {
    "spillover": [{"from_junction": "J1", "to_junction": "J2",
                   "horizon_s": 60.0, "predicted_queue_delta": 3.2,
                   "confidence": 0.85}],
    "incident_impact": [{"incident_id": "inc_0001",
                         "estimated_affected_junctions": ["J1", "J2", "J3"],
                         "estimated_delay_increase_s": 105.0,
                         "horizon_s": 300.0}],
}


# ---------------------------------------------------------------------------
def w1_six_agents_tick() -> None:
    print("\nW1  every agent ticks")
    orch = Orchestrator()
    rows = orch.observe(_ctx(_corridor(), _info(120.0), predictions=PREDICTIONS))
    seen = {r.agent for r in rows}
    check("W1 the roster is exactly the six pinned AGENT_NAMES",
          len(AGENT_NAMES) == 6 and seen == set(AGENT_NAMES),
          f"{sorted(seen)}")
    check("W1 every agent returned at least one entry",
          all(sum(1 for r in rows if r.agent == a) >= 1 for a in AGENT_NAMES))
    check("W1 no agent was disabled during a healthy round",
          orch.disabled == frozenset(), f"disabled={sorted(orch.disabled)}")


def w2_every_agent_publishes() -> None:
    print("\nW2  every agent publishes to the blackboard")
    orch = Orchestrator()
    ctx = _ctx(_corridor(), _info(120.0), predictions=PREDICTIONS)
    rows = orch.observe(ctx)
    bb = orch.blackboard
    check("W2 blackboard holds a non-empty trace for each of the six",
          all(bb.entries_for(agent=a) for a in AGENT_NAMES))
    check("W2 the returned round IS the recorded round (frame and trace can "
          "never disagree)",
          bb.round(ctx.step) == rows and len(bb) == len(rows),
          f"{len(rows)} entries")
    check("W2 blackboard.entries is a copy — a caller cannot mutate the record",
          isinstance(bb.entries, tuple) and bb.entries is not bb.entries)


def w3_wire_shape() -> None:
    print("\nW3  §13.2 wire shape and size bounds")
    orch = Orchestrator()
    rows = [r.to_dict() for r in
            orch.observe(_ctx(_corridor(True), _info(120.0), predictions=PREDICTIONS))]
    payload = json.dumps(rows)
    check("W3 the whole round is JSON-serialisable", isinstance(payload, str))
    check("W3 every entry has EXACTLY the pinned ENTRY_KEYS",
          all(set(r) == ENTRY_KEYS for r in rows))
    check("W3 Part 3's documented minimum {agent, said, at} is a subset",
          {"agent", "said", "at"} <= ENTRY_KEYS)
    check("W3 every `said` is within SAID_MAX_CHARS",
          all(len(r["said"]) <= SAID_MAX_CHARS for r in rows),
          f"max={max(len(r['said']) for r in rows)}")
    flat = all(isinstance(v, (str, int, float, bool, type(None)))
               for r in rows for v in r["detail"].values())
    check("W3 `detail` is flat scalars only, within DETAIL_MAX_KEYS",
          flat and all(len(r["detail"]) <= DETAIL_MAX_KEYS for r in rows))
    check("W3 the round fits the per-frame byte budget",
          len(payload.encode()) < PER_FRAME_BYTE_BUDGET,
          f"{len(payload.encode())} < {PER_FRAME_BYTE_BUDGET} bytes")


def w4_the_veto() -> None:
    print("\nW4  the Supervisor veto — three checks, deliberately")

    # W4a — ANTI-VACUITY. Without this, an agent that always emits a veto
    # would pass W4b.
    rows = Orchestrator().observe(_ctx(_corridor(), _info(120.0, overrides=[])))
    sup = [r for r in rows if r.agent == AGENT_SUPERVISOR]
    check("W4a zero §10 overrides -> ZERO veto rows, Supervisor goes idle",
          [r for r in rows if r.kind == KIND_VETO] == []
          and len(sup) == 1 and sup[0].kind == KIND_IDLE,
          f"said={sup[0].said!r}" if sup else "no supervisor row")

    # W4b — exactly N, field-for-field, and present in the recorded TRACE.
    injected = [
        _override("J2", "emergency_override", "E1_J2_0", wait_s=4.0,
                  from_slot=0, to_slot=1),
        _override("J1", "starvation_ceiling", "W1_J1_0", wait_s=121.0,
                  from_slot=1, to_slot=0, outcome="deferred_min_green"),
    ]
    orch = Orchestrator()
    ctx = _ctx(_corridor(True), _info(120.0, overrides=injected))
    rows = orch.observe(ctx)
    vetoes = [r for r in rows if r.kind == KIND_VETO]
    check("W4b two §10 overrides -> EXACTLY two veto rows", len(vetoes) == 2,
          f"{len(vetoes)}")
    check("W4b only the Supervisor may veto",
          {r.agent for r in vetoes} == {AGENT_SUPERVISOR})
    field_ok = True
    for veto, record in zip(sorted(vetoes, key=lambda r: r.detail["junction_id"]),
                            sorted(injected, key=lambda r: r["junction_id"])):
        for f in ("junction_id", "rule", "lane_id", "wait_s", "from_slot",
                  "to_slot", "outcome"):
            field_ok &= veto.detail[f] == record[f]
        field_ok &= record["rule"] in veto.said and record["lane_id"] in veto.said
    check("W4b each veto matches its OverrideRecord field-for-field, and "
          "names the rule and lane in its sentence", field_ok)
    trace = orch.blackboard.entries_for(agent=AGENT_SUPERVISOR,
                                        upto_at=ctx.sim_time)
    check("W4b the vetoes appear IN THE RECORDED TRACE, not just the return",
          list(trace)[-2:] == vetoes)

    # W4c — REPORTING, NOT AUTHORITY.
    ghost = "GHOST_LANE_DOES_NOT_EXIST_0"
    snapshot = _corridor(True)
    assert ghost not in {l for b in snapshot["junctions"].values()
                         for l in b["lanes"]}, "fixture bug: ghost lane exists"
    rows = Orchestrator().observe(_ctx(
        snapshot, _info(120.0, overrides=[
            _override("J2", "emergency_override", ghost, wait_s=9.0,
                      from_slot=0, to_slot=2)])))
    veto = [r for r in rows if r.kind == KIND_VETO]
    check("W4c an override naming a lane absent from the snapshot is still "
          "reported VERBATIM — only a reporter can do that",
          len(veto) == 1 and veto[0].detail["lane_id"] == ghost
          and ghost in veto[0].said)


def w5_no_mutation() -> None:
    print("\nW5  the round mutates nothing on the context")
    snapshot, info = _corridor(True), _info(120.0, overrides=[
        _override("J2", "emergency_override", "E1_J2_0", wait_s=4.0,
                  from_slot=0, to_slot=1)])
    ctx = _ctx(snapshot, info, predictions=PREDICTIONS)
    before = copy.deepcopy((snapshot, info, ctx.decisions, ctx.predictions))
    Orchestrator().observe(ctx)
    after = (snapshot, info, ctx.decisions, ctx.predictions)
    check("W5 snapshot / info / decisions / predictions are deep-equal after "
          "a full round", before == after)
    entry = AgentEntry(agent="Detection", role="r", wraps="w", kind=KIND_IDLE,
                       said="s", at=1.0, step=1)
    frozen = False
    try:
        entry.said = "tampered"          # type: ignore[misc]
    except Exception:
        frozen = True
    check("W5 AgentEntry is frozen", frozen)


def w6_failure_isolation() -> None:
    print("\nW6  one broken wrapper cannot take anything else down")

    class _BoomAgent(_Wrapper):
        name, wraps, role = "Detection", "boom", "raises on purpose"

        def __init__(self):
            self.calls = 0

        def tick(self, ctx):
            self.calls += 1
            raise RuntimeError("boom")

    boom = _BoomAgent()
    healthy = [w for w in default_wrappers() if w.name != "Detection"]
    orch = Orchestrator(wrappers=[boom, *healthy])
    ctx = _ctx(_corridor(), _info(120.0), predictions=PREDICTIONS)
    rows = orch.observe(ctx)
    check("W6 the other five agents still reported",
          {r.agent for r in rows} == set(AGENT_NAMES) - {"Detection"},
          f"{sorted({r.agent for r in rows})}")
    check("W6 the broken agent is in `disabled`", "Detection" in orch.disabled)
    orch.observe(_ctx(_corridor(), _info(125.0), predictions=PREDICTIONS))
    check("W6 it is LATCHED off, not merely caught (call count stays 1)",
          boom.calls == 1, f"calls={boom.calls}")


def w7_no_new_logic() -> None:
    print("\nW7  constraint 1 — wrappers compute nothing; no heavy imports")

    heavy = {"traci", "sumolib", "torch", "numpy", "stable_baselines3",
             "gymnasium", "random"}
    pulled = heavy & set(sys.modules)
    check("W7 importing orchestrator pulls in no SUMO / torch / numpy / random",
          not pulled, f"pulled={sorted(pulled) or 'none'}")

    src = open("orchestrator/wrappers.py", encoding="utf-8").read()
    tree = ast.parse(src)
    # The tripwire for "a wrapper started computing something": a bare number
    # in this file is almost always a threshold. 0/1 are indices and flags;
    # every selection/format number is a named import from types.py.
    bad_numbers = [n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant)
                   and isinstance(n.value, (int, float))
                   and not isinstance(n.value, bool)
                   and n.value not in (0, 1)]
    check("W7 no numeric literal outside {0, 1} in wrappers.py",
          not bad_numbers, f"found={bad_numbers}")

    banned = {"apply", "dispatch", "validate", "forecast",
              "predict_incident_impact", "ControlState"}
    imported = {a.asname or a.name
                for n in ast.walk(tree)
                if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in n.names}
    check("W7 wrappers.py imports no actuation or recompute entry point",
          not (banned & imported), f"banned={sorted(banned & imported)}")


def w8_episode_boundary() -> None:
    print("\nW8  episode boundary — the lifecycle rule is load-bearing")
    bb = Blackboard()
    good = AgentEntry(agent="Detection", role="r", wraps="w",
                      kind=KIND_IDLE, said="s", at=100.0, step=1)
    bb.publish(good)
    raised, msg = False, ""
    try:
        bb.publish(AgentEntry(agent="Detection", role="r", wraps="w",
                              kind=KIND_IDLE, said="s", at=1.0, step=2))
    except BlackboardError as exc:
        raised, msg = True, str(exc)
    check("W8 a backwards `at` RAISES (DecisionLog's rule, same reason)",
          raised and "BACKWARDS" in msg)
    check("W8 a rejected publish leaves the deque untouched", len(bb) == 1)
    check("W8 an equal `at` is legal (one round shares one timestamp)",
          bb.publish(AgentEntry(agent="Vision", role="r", wraps="w",
                                kind=KIND_IDLE, said="s", at=100.0,
                                step=1)) is not None)

    orch = Orchestrator(disabled={"Vision"})
    fresh = Orchestrator.for_episode(disabled=orch.disabled)
    check("W8 for_episode() yields a NEW blackboard and a NEW agent instance",
          fresh.blackboard is not orch.blackboard and fresh is not orch)
    check("W8 the disabled set is carried FORWARD across the boundary",
          fresh.disabled == frozenset({"Vision"}))
    check("W8 the fresh orchestrator accepts a t~0 round after a reset",
          len(fresh.observe(_ctx(_corridor(), _info(0.0)))) > 0)


def w9_advisory() -> None:
    print("\nW9  advisory — a round never touches a ControlState")

    class _Boobytrap:
        def __getattr__(self, name):
            raise AssertionError(
                f"a wrapper touched a ControlState (.{name}) — the "
                f"orchestrator must never dispatch")

    orch = Orchestrator()
    ctx = _ctx(_corridor(True), _info(120.0), predictions=PREDICTIONS)
    ctx_with_trap = AgentContext(
        step=ctx.step, sim_time=ctx.sim_time, pre_snapshot=ctx.pre_snapshot,
        info=ctx.info, decisions=ctx.decisions, predictions=ctx.predictions,
        forced_emergency_lanes=ctx.forced_emergency_lanes, mode=ctx.mode)
    _state = _Boobytrap()          # not reachable from ctx — that IS the point
    rows = orch.observe(ctx_with_trap)
    ip = [r for r in rows if r.agent == "IncidentPriority"]
    check("W9 a full round completes with no ControlState reachable at all",
          len(rows) >= len(AGENT_NAMES))
    check("W9 IncidentPriority reports `dispatched: False`",
          bool(ip) and ip[0].detail.get("dispatched") is False
          and "ADVISORY" in ip[0].said)


def _check_vision_drift() -> None:
    print("\n--- vision fixture drift guard ---")
    try:
        from perception.vision_mock import CONFIDENCE_RANGE, SOURCE_TAG
    except Exception as exc:
        print(f"  SKIPPED — perception.vision_mock needs SUMO ({type(exc).__name__})")
        return
    assert VISION_SOURCE_TAG == SOURCE_TAG, "vision source tag drifted"
    assert CONFIDENCE_RANGE[0] <= VISION_CONFIDENCE <= CONFIDENCE_RANGE[1]
    print(f"  source={SOURCE_TAG!r} confidence {VISION_CONFIDENCE} in "
          f"{CONFIDENCE_RANGE} — fixture matches §7.2")


def _transcript() -> None:
    """Hand-scored: print the six lines a human will read on the panel."""
    print("\n" + RULE)
    print("  TRANSCRIPT — ambulance at J2, J2 congested, §10 vetoed at J1")
    print(RULE)
    rows = Orchestrator().observe(_ctx(
        _corridor(ambulance=True), predictions=PREDICTIONS,
        info=_info(120.0, overrides=[
            _override("J2", "emergency_override", "E1_J2_0", wait_s=4.0,
                      from_slot=0, to_slot=1),
            _override("J1", "starvation_ceiling", "W1_J1_0", wait_s=121.0,
                      from_slot=1, to_slot=0, outcome="deferred_min_green")])))
    for row in rows:
        print(f"  {row.agent:<17} [{row.kind}]")
        print(f"    {row.said}")


CHECKS = {"w1": w1_six_agents_tick, "w2": w2_every_agent_publishes,
          "w3": w3_wire_shape, "w4": w4_the_veto, "w5": w5_no_mutation,
          "w6": w6_failure_isolation, "w7": w7_no_new_logic,
          "w8": w8_episode_boundary, "w9": w9_advisory}


def main(argv: list[str]) -> int:
    selected = [a.lstrip("-") for a in argv if a.lstrip("-") in CHECKS]
    print(RULE)
    print("  ORCHESTRATOR SELF-TEST (Part 4b) — no SUMO")
    print(f"  agents: {' -> '.join(AGENT_NAMES)}")
    print(RULE)
    for key in (selected or CHECKS):
        CHECKS[key]()
    if not selected:
        _check_vision_drift()
        _transcript()
    print("\n" + RULE)
    print(f"  {_passed} passed, {_failed} failed")
    print(RULE)
    return 1 if _failed else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))

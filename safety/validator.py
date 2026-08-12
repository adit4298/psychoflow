"""Safety & Policy Validator (§10).

The mandatory gate between an intervention proposal (§9 — Tier 0's
rule-based controller, the RL policy, or a §14 voice command) and the
actual `traci.trafficlight.setPhase()` call. It lives INSIDE
`PsychoFlowEnv.step()`, immediately before the only code path that
reaches SUMO, which is what makes §10's claim ("nothing reaches the road
without passing through here") a structural fact rather than a
convention. A Gymnasium wrapper would leave `env.step()` directly
callable and unshielded.

This module imports no traci. It is a pure function of
(proposal, twin snapshot, signal runtime, static phase->lane map), which
is what honours §7.6's "no module outside perception queries TraCI" and
what lets test_validator_scenarios() below run with no SUMO process.

Two rules, and the ORDER MATTERS:

  1. EMERGENCY OVERRIDE — an ambulance on an approach lane CLAIMS that
     junction. Not merely "fix it if broken": while an ambulance is
     present, no other rule may move the junction away from serving it.
  2. STARVATION CEILING — any lane past STARVATION_CEILING_S forces a
     phase that actually serves it.

§10's pseudocode tests starvation FIRST and returns, which would let a
starved lane deprioritize an ambulance — contradicting §10's own prose
("cannot be delayed/blocked/deprioritized by anything else") and §9.4's
weights (w_emergency=20.0 versus a starvation term that only reaches ~20
at a 250s wait). The prose and the reward agree with each other; the
pseudocode's ordering was incidental. Corrected here and in the master
plan; scenario 7 below pins it as an assertion.

What the ceiling does NOT guarantee (§10.1): it bounds what the
controller is allowed to DECIDE, not the observed wait. Detection
granularity (one decision interval), yellow clearance, min-green
deference and physical discharge sit between the trigger and the queue
moving, so a 120s ceiling yields a ~140-160s observed worst case. And a
lane starved by DOWNSTREAM gridlock will keep climbing however green it
is — the ceiling guarantees the signal has stopped being the cause, not
that the lane drains.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from perception.lane_sensor import DEFAULT_STARVATION_THRESHOLD_S
from twin.digital_twin import CORRIDOR_JUNCTIONS

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
# §10 requires a ceiling but never gives it a value. It MUST sit above
# DEFAULT_STARVATION_THRESHOLD_S (90s): if the two were equal the ceiling
# would fire the instant a lane is flagged, Tier 0's non-linear
# starvation_bonus (§9.1) would never get a band to act in, and the
# "fairness-first rule-based controller" claim would reduce to "the
# validator drives". 120s leaves a 30s band for the soft bonus to work.
#
# Three constants, three distinct roles — do not collapse them:
#   DEFAULT_STARVATION_THRESHOLD_S  90   soft line: flag, bonus, reward hinge
#   STARVATION_CEILING_S           120   hard line: validator overrides
#   MIN_GREEN_S                     10   anti-flicker (env, passed in)
STARVATION_CEILING_S = 120.0

assert STARVATION_CEILING_S > DEFAULT_STARVATION_THRESHOLD_S, (
    "the §10 ceiling must sit above §0.1's starvation threshold, or §9.1's "
    "soft bonus has no band to act in"
)

RULE_EMERGENCY = "emergency_override"
RULE_STARVATION = "starvation_ceiling"

OUTCOME_APPLIED = "applied"
OUTCOME_DEFERRED_MIN_GREEN = "deferred_min_green"
OUTCOME_RETARGETED = "retargeted_transition"


class ValidatorError(RuntimeError):
    """The validator cannot construct a safe action.

    Raised rather than silently passing the proposal through: if no green
    phase serves a lane that needs serving, the network or its TLS program
    is malformed, and continuing would mean §10's gate quietly doing
    nothing for the rest of the episode.
    """


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class OverrideRecord:
    """One junction, one rule fired. Feeds §12.1's decision log directly."""

    junction_id: str
    rule: str
    from_slot: int  # what was proposed
    to_slot: int  # what the rule wants — see `outcome` for whether it applied
    lane_id: str  # the lane that caused it
    wait_s: float
    outcome: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ValidatedAction:
    action: tuple[int, ...]
    proposed: tuple[int, ...]
    overrides: tuple[OverrideRecord, ...] = ()
    # Junctions where MIN_GREEN_S was knowingly broken. This is a
    # DECLARATION for the log and for tests, not an enforcement knob: the
    # min-green rule lives in the action mask, which is applied to the
    # PROPOSAL, so anything this module returns is applied unchecked. The
    # ceiling therefore has to defer to min-green itself (below) rather
    # than relying on a downstream check.
    bypass_min_green: frozenset[str] = frozenset()
    # Junctions whose in-flight yellow is being re-aimed. The yellow still
    # runs to completion — only its destination changes.
    retarget_transition: frozenset[str] = frozenset()

    @property
    def modified(self) -> bool:
        return self.action != self.proposed

    @classmethod
    def passthrough(cls, proposed) -> "ValidatedAction":
        p = tuple(int(x) for x in proposed)
        return cls(action=p, proposed=p)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _most_urgent(candidates: list[tuple[str, dict]]) -> tuple[str, dict]:
    """Worst wait wins; ties break on lane_id so the choice is deterministic."""
    return min(candidates, key=lambda kv: (-kv[1]["wait_time_max_single_vehicle"], kv[0]))


def _slot_serving(
    served: dict[int, frozenset[str]], lane_id: str, junction_id: str, rule: str
) -> int:
    slots = sorted(s for s, lanes in served.items() if lane_id in lanes)
    if not slots:
        raise ValidatorError(
            f"{junction_id}: no green phase serves lane {lane_id}, so rule "
            f"'{rule}' cannot be satisfied. Every controlled lane must be "
            f"green in at least one phase — the network or its TLS program "
            f"is malformed. Served map: "
            f"{ {s: sorted(l) for s, l in served.items()} }"
        )
    return slots[0]


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------
def validate(
    proposed,
    snapshot: dict,
    runtime: dict[str, dict],
    served_lanes: dict[str, dict[int, frozenset[str]]],
    min_green_s: float,
    forced_emergency_lanes: frozenset[str] = frozenset(),
    starvation_ceiling_s: float = STARVATION_CEILING_S,
) -> ValidatedAction:
    """Gate one proposed action for the whole corridor.

    `snapshot` is the twin state the proposal was made against — in the env
    that is the snapshot `build_observation()` was called on, so the
    validator judges the action against exactly the reality the policy saw.

    `served_lanes` is the STATIC per-episode map {junction: {slot: lanes}}
    of which lanes each green slot WOULD green. Deliberately not the env's
    `_green_lanes()`, which reads the live RYG state and mid-yellow returns
    the yellow phase's greens — correct for §9.4's "is it moving right
    now", wrong as a phase->lane map.

    `forced_emergency_lanes` is §13.1's `trigger_emergency(lane_id)`: an
    operator forcing the same override §10 raises automatically.
    """
    proposed_t = tuple(int(x) for x in proposed)
    if len(proposed_t) != len(CORRIDOR_JUNCTIONS):
        raise ValueError(
            f"expected {len(CORRIDOR_JUNCTIONS)} slots, got {len(proposed_t)}"
        )

    action = list(proposed_t)
    overrides: list[OverrideRecord] = []
    bypass: set[str] = set()
    retarget: set[str] = set()

    for i, junction_id in enumerate(CORRIDOR_JUNCTIONS):
        lanes = snapshot["junctions"][junction_id]["lanes"]
        jruntime = runtime[junction_id]
        served = served_lanes[junction_id]

        transition_target = jruntime.get("transition_target")
        in_transition = transition_target is not None

        # What will actually end up green if this action passes through.
        # Mid-yellow the env ignores the action and completes the committed
        # transition, so the eventual green is the transition target.
        resulting = transition_target if in_transition else proposed_t[i]
        resulting_lanes = served.get(resulting, frozenset())

        critical: tuple[str, dict] | None = None
        rule = ""

        # ---- Rule 1: EMERGENCY (first — see module docstring) -----------
        # An ambulance CLAIMS the junction. If the action already serves it
        # we `continue`, which suppresses the ceiling for this junction —
        # that suppression is the whole point of "cannot be deprioritized
        # by anything else". Without it, an ambulance on the east-west
        # phase and a 200s starved lane on north-south would let the
        # ceiling drag the green off the ambulance.
        emergency_lanes = [
            (lane_id, reading)
            for lane_id, reading in lanes.items()
            if reading["type_composition"].get("ambulance", 0) > 0
            or lane_id in forced_emergency_lanes
        ]
        if emergency_lanes:
            lane_id, reading = _most_urgent(emergency_lanes)
            if lane_id in resulting_lanes:
                continue  # already served — and the ceiling may not touch it
            critical, rule = (lane_id, reading), RULE_EMERGENCY

        # ---- Rule 2: STARVATION CEILING ---------------------------------
        else:
            starved = [
                (lane_id, reading)
                for lane_id, reading in lanes.items()
                if reading["wait_time_max_single_vehicle"] > starvation_ceiling_s
            ]
            if starved:
                lane_id, reading = _most_urgent(starved)
                if lane_id not in resulting_lanes:
                    critical, rule = (lane_id, reading), RULE_STARVATION

        if critical is None:
            continue

        lane_id, reading = critical
        target = _slot_serving(served, lane_id, junction_id, rule)

        # ---- Apply -------------------------------------------------------
        if in_transition:
            if target == transition_target:
                continue  # the yellow is already aimed at the right place
            # Re-aim the in-flight yellow instead of interrupting it.
            # Breaking a yellow releases conflicting movements before the
            # previous ones have cleared, which is the exact hazard this
            # validator exists to prevent. Costs at most the yellow's
            # remainder (~3-4s) and is the fastest SAFE response.
            action[i] = target
            retarget.add(junction_id)
            outcome = OUTCOME_RETARGETED

        elif target == jruntime["current_green_slot"]:
            # The fix is "hold" — the proposal wanted to switch away from a
            # phase that was already correct. No switch, so min-green is
            # irrelevant.
            action[i] = target
            outcome = OUTCOME_APPLIED

        elif rule == RULE_STARVATION and jruntime["time_since_switch_s"] < min_green_s:
            # The ceiling defers to min-green; the emergency override never
            # does. Letting the ceiling break min-green reintroduces exactly
            # the flicker §9.2's masking and §9.4's switch penalty suppress
            # — two mutually starved lanes would ping-pong every decision
            # step. Deferring costs at most MIN_GREEN_S against a wait
            # already past 120s. Logged either way so §12.1 stays honest.
            outcome = OUTCOME_DEFERRED_MIN_GREEN

        else:
            action[i] = target
            outcome = OUTCOME_APPLIED
            if jruntime["time_since_switch_s"] < min_green_s:
                # Emergency only, by construction of the branch above.
                bypass.add(junction_id)

        overrides.append(
            OverrideRecord(
                junction_id=junction_id,
                rule=rule,
                from_slot=proposed_t[i],
                to_slot=target,
                lane_id=lane_id,
                wait_s=reading["wait_time_max_single_vehicle"],
                outcome=outcome,
            )
        )

    return ValidatedAction(
        action=tuple(action),
        proposed=proposed_t,
        overrides=tuple(overrides),
        bypass_min_green=frozenset(bypass),
        retarget_transition=frozenset(retarget),
    )


# --------------------------------------------------------------------------
# Unit scenarios (§18 Phase 4 done bar) — no SUMO process required
# --------------------------------------------------------------------------
# Fixture: every junction has 4 lanes, one per approach, and 2 green
# phases — slot 0 serves north/south, slot 1 serves east/west. That is the
# minimum shape that can express "the lane that needs serving is on the
# OTHER phase", which is what every rule here turns on.
_APPROACHES = ("n", "e", "s", "w")


def _lane(junction_id: str, approach: str) -> str:
    return f"{junction_id}_{approach}0"


def _served_fixture() -> dict[str, dict[int, frozenset[str]]]:
    return {
        j: {
            0: frozenset({_lane(j, "n"), _lane(j, "s")}),
            1: frozenset({_lane(j, "e"), _lane(j, "w")}),
        }
        for j in CORRIDOR_JUNCTIONS
    }


def _snapshot_fixture(
    waits: dict[str, float] | None = None,
    ambulance_lanes: frozenset[str] = frozenset(),
) -> dict:
    """§7.6-shaped snapshot; `waits` overrides individual lanes, default 20s."""
    waits = waits or {}
    junctions = {}
    for junction_id in CORRIDOR_JUNCTIONS:
        lane_readings = {}
        for approach in _APPROACHES:
            lane_id = _lane(junction_id, approach)
            wait_s = waits.get(lane_id, 20.0)
            lane_readings[lane_id] = {
                "lane_id": lane_id,
                "approach": approach,
                "vehicle_count": 3,
                "halted_count": 2,
                "type_composition": {
                    "bike": 0, "auto": 0, "car": 3, "truck": 0,
                    "ambulance": 1 if lane_id in ambulance_lanes else 0,
                },
                "wait_time_current": wait_s * 2,
                "wait_time_max_single_vehicle": wait_s,
                "starvation_flag": wait_s > DEFAULT_STARVATION_THRESHOLD_S,
            }
        junctions[junction_id] = {
            "lanes": lane_readings, "vision": {}, "current_phase": 0, "lane_count": 2,
        }
    return {
        "sim_time": 500.0,
        "corridor_adjacency": [["J1", "J2"], ["J2", "J3"]],
        "junctions": junctions,
        "active_incidents": [],
        "weather": {"state": "clear", "changed_at_sim_time": 0.0},
        "v2x_messages_recent": [],
    }


def _runtime_fixture(
    j1: dict | None = None, green_age_s: float = 40.0
) -> dict[str, dict]:
    base = {
        "current_green_slot": 0,
        "n_green_phases": 2,
        "time_since_switch_s": green_age_s,
        "transition_target": None,
    }
    runtime = {j: dict(base) for j in CORRIDOR_JUNCTIONS}
    if j1:
        runtime["J1"].update(j1)
    return runtime


def test_validator_scenarios() -> None:
    """The 8 §10 scenarios, as executable assertions."""
    served = _served_fixture()
    min_green = 10.0
    passed = 0

    def check(label: str, result: ValidatedAction, **expect) -> None:
        nonlocal passed
        problems = []
        if "action" in expect and result.action != expect["action"]:
            problems.append(f"action {result.action} != {expect['action']}")
        if "n_overrides" in expect and len(result.overrides) != expect["n_overrides"]:
            problems.append(f"{len(result.overrides)} overrides != {expect['n_overrides']}")
        if "rule" in expect:
            got = result.overrides[0].rule if result.overrides else None
            if got != expect["rule"]:
                problems.append(f"rule {got!r} != {expect['rule']!r}")
        if "outcome" in expect:
            got = result.overrides[0].outcome if result.overrides else None
            if got != expect["outcome"]:
                problems.append(f"outcome {got!r} != {expect['outcome']!r}")
        if "bypass" in expect and bool(result.bypass_min_green) != expect["bypass"]:
            problems.append(f"bypass_min_green={result.bypass_min_green!r}")
        if "retarget" in expect and bool(result.retarget_transition) != expect["retarget"]:
            problems.append(f"retarget_transition={result.retarget_transition!r}")

        status = "OK" if not problems else "FAIL"
        print(f"  [{status}] {label}")
        print(f"         proposed={result.proposed} -> action={result.action}"
              f"{'  (unchanged)' if not result.modified else ''}")
        for record in result.overrides:
            print(f"         override: {record.junction_id} {record.rule} "
                  f"slot {record.from_slot}->{record.to_slot} "
                  f"lane={record.lane_id} wait={record.wait_s:.0f}s "
                  f"outcome={record.outcome}")
        if problems:
            raise AssertionError(f"{label}: " + "; ".join(problems))
        passed += 1

    print(f"§10 validator scenarios  (ceiling={STARVATION_CEILING_S:.0f}s, "
          f"threshold={DEFAULT_STARVATION_THRESHOLD_S:.0f}s, min_green={min_green:.0f}s)\n")

    # -- 1: nothing wrong -> exact pass-through ---------------------------
    check("1  clean state: action passes through untouched",
          validate((0, 0, 0), _snapshot_fixture(), _runtime_fixture(), served, min_green),
          action=(0, 0, 0), n_overrides=0)

    # -- 2a: ceiling fires ------------------------------------------------
    check("2a ceiling: J1 north at 130s, proposal serves east/west",
          validate((1, 0, 0), _snapshot_fixture({_lane("J1", "n"): 130.0}),
                   _runtime_fixture(), served, min_green),
          action=(0, 0, 0), n_overrides=1, rule=RULE_STARVATION,
          outcome=OUTCOME_APPLIED)

    # -- 2b: ceiling DEFERS to min-green (sign-off item 7) ----------------
    # J1 is on slot 1 with a 4s-old green; the ceiling wants slot 0. It must
    # wait rather than break min-green and start a ping-pong.
    check("2b ceiling defers to min-green (green_age=4s < 10s)",
          validate((1, 0, 0), _snapshot_fixture({_lane("J1", "n"): 130.0}),
                   _runtime_fixture({"current_green_slot": 1}, green_age_s=4.0),
                   served, min_green),
          action=(1, 0, 0), n_overrides=1, rule=RULE_STARVATION,
          outcome=OUTCOME_DEFERRED_MIN_GREEN, bypass=False)

    # -- 3: ceiling already satisfied by the proposal ---------------------
    check("3  ceiling satisfied: 130s lane already served by proposal",
          validate((0, 0, 0), _snapshot_fixture({_lane("J1", "n"): 130.0}),
                   _runtime_fixture(), served, min_green),
          action=(0, 0, 0), n_overrides=0)

    # -- 4: the band that belongs to Tier 0's soft bonus ------------------
    check("4  110s: over the 90s threshold, under the 120s ceiling -> no fire",
          validate((1, 0, 0), _snapshot_fixture({_lane("J1", "n"): 110.0}),
                   _runtime_fixture(), served, min_green),
          action=(1, 0, 0), n_overrides=0)

    # -- 5: emergency on a red lane, AND it breaks min-green --------------
    check("5  ambulance on J1 east (red), green_age=4s -> override + bypass",
          validate((0, 0, 0), _snapshot_fixture(ambulance_lanes=frozenset({_lane("J1", "e")})),
                   _runtime_fixture(green_age_s=4.0), served, min_green),
          action=(1, 0, 0), n_overrides=1, rule=RULE_EMERGENCY,
          outcome=OUTCOME_APPLIED, bypass=True)

    # -- 6: emergency already green -> nothing to do ----------------------
    # Must agree with §9.4's emergency term, which charges no penalty when
    # the ambulance lane is in the green set.
    check("6  ambulance on J1 north, already green -> no override",
          validate((0, 0, 0), _snapshot_fixture(ambulance_lanes=frozenset({_lane("J1", "n")})),
                   _runtime_fixture(), served, min_green),
          action=(0, 0, 0), n_overrides=0)

    # -- 7: PRECEDENCE — the contradiction in §10's pseudocode ------------
    # Ambulance on east/west (slot 1), a 200s starved lane on north/south
    # (slot 0). The two rules want opposite phases.
    conflict = _snapshot_fixture({_lane("J1", "n"): 200.0},
                                 ambulance_lanes=frozenset({_lane("J1", "e")}))

    # 7a: proposal already serves the ambulance. The ceiling must be
    # SUPPRESSED — if it fired it would drag the green off the ambulance.
    # This is the case §10's pseudocode gets wrong.
    check("7a precedence: ambulance served + 200s starved lane -> ceiling SUPPRESSED",
          validate((1, 0, 0), conflict, _runtime_fixture({"current_green_slot": 1}),
                   served, min_green),
          action=(1, 0, 0), n_overrides=0)

    # 7b: proposal serves the starved lane instead. Emergency must win.
    check("7b precedence: same state, proposal abandons ambulance -> EMERGENCY wins",
          validate((0, 0, 0), conflict, _runtime_fixture(), served, min_green),
          action=(1, 0, 0), n_overrides=1, rule=RULE_EMERGENCY)

    # -- 8: mid-yellow retarget -------------------------------------------
    # J1 is mid-yellow, committed to slot 0. An ambulance needs slot 1. The
    # yellow must NOT be broken — it is re-aimed instead.
    check("8  mid-yellow to slot 0, ambulance needs slot 1 -> RETARGET, yellow intact",
          validate((0, 0, 0), _snapshot_fixture(ambulance_lanes=frozenset({_lane("J1", "e")})),
                   _runtime_fixture({"transition_target": 0}), served, min_green),
          action=(1, 0, 0), n_overrides=1, rule=RULE_EMERGENCY,
          outcome=OUTCOME_RETARGETED, retarget=True)

    # -- structural failure must be loud, not silent ----------------------
    orphan = {j: {0: frozenset(), 1: frozenset()} for j in CORRIDOR_JUNCTIONS}
    try:
        validate((0, 0, 0), _snapshot_fixture({_lane("J1", "n"): 200.0}),
                 _runtime_fixture(), orphan, min_green)
        raise AssertionError("a lane served by NO phase must raise, not pass silently")
    except ValidatorError as exc:
        print(f"  [OK] 9  unservable lane raises instead of silently passing")
        print(f"         ValidatorError: {str(exc).splitlines()[0]}")
        passed += 1

    print(f"\nAll {passed} §10 validator scenarios passed.")


if __name__ == "__main__":
    test_validator_scenarios()

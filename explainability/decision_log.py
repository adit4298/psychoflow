"""Technical decision log (§12.1).

One structured entry per junction per decision step — §12.1's schema is
per-junction (`junction_id` singular), so a 3-junction corridor produces
three entries per decision step. §13.2's single per-step `decision` field
is a later filtering concern, not this module's shape.

Each entry is §12.1 verbatim:

    { "sim_time", "junction_id", "phase_selected", "score_breakdown",
      "alternative_scores", "reason" }

plus a reconciliation block that only appears when §10's validator
overrode that junction on that step:

    "proposed": {"phase", "reason"}      # what the controller/policy wanted
    "override": {"rule", "lane_id", "wait_s", "from_slot", "to_slot", "outcome"}

The recorder is AGENT-AGNOSTIC. `record_step()` takes the `decisions`
dict shape that `agents.rule_based.Tier0Controller.act()` already
returns; Phase 9's RL auto-mode builds the same-shaped dict with
`reason="rl_policy"` and whatever breakdown it has, and the override
reconciliation is identical regardless of who produced the proposal.
It is fed CALLER-SIDE (the Phase 8 harness, Phase 9's
`backend/sim_runner.py`) — never wired into `PsychoFlowEnv.step()`,
whose scope is frozen (CLAUDE.md §3).

ONE LOG PER EPISODE. `record_step`/`record_voice` refuse a `sim_time`
earlier than the highest already recorded: the deque is read positionally
by `entries_for`/`latest`/§12.3's `why()`, so an out-of-order entry raises
nothing and instead makes every later at-or-before query answer with the
wrong decision. `env.reset()` sends sim_time back to ~0, so a log reused
across an episode boundary would do exactly that — the guard is what makes
the per-episode lifecycle load-bearing rather than a convention.

Reason vocabulary — six values, single-sourced from the modules that own
each string so they cannot drift:

    wait_time_threshold   agents.rule_based.REASON_STARVATION   (Tier 0)
    raw_count             agents.rule_based.REASON_COUNT        (Tier 0)
    emergency_override    safety.validator.RULE_EMERGENCY       (§10)
    starvation_ceiling    safety.validator.RULE_STARVATION      (§10)
    voice_command         §12.2 — producer is Phase 9/11
    rl_policy             §12.2-adjacent — producer is Phase 9 auto mode
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

from agents.rule_based import REASON_COUNT, REASON_STARVATION
from env.obs_action_spec import _lane_index
from safety.validator import (
    OUTCOME_DEFERRED_MIN_GREEN,
    RULE_EMERGENCY,
    RULE_STARVATION,
)

# --------------------------------------------------------------------------
# Reason enum — imported, never retyped (CLAUDE.md discipline)
# --------------------------------------------------------------------------
REASON_WAIT_THRESHOLD = REASON_STARVATION       # "wait_time_threshold"
REASON_RAW_COUNT = REASON_COUNT                 # "raw_count"
REASON_EMERGENCY_OVERRIDE = RULE_EMERGENCY      # "emergency_override"
REASON_STARVATION_CEILING = RULE_STARVATION     # "starvation_ceiling"
REASON_VOICE_COMMAND = "voice_command"          # §12.2; producer Phase 9/11
REASON_RL_POLICY = "rl_policy"                  # §12.2-adjacent; producer Phase 9

# Tripwire: if an upstream constant is ever re-spelled, fail loudly here
# rather than let Session 3 / the narrator silently miss a template.
assert REASON_WAIT_THRESHOLD == "wait_time_threshold"
assert REASON_RAW_COUNT == "raw_count"
assert REASON_EMERGENCY_OVERRIDE == "emergency_override"
assert REASON_STARVATION_CEILING == "starvation_ceiling"

REASONS = frozenset({
    REASON_WAIT_THRESHOLD,
    REASON_RAW_COUNT,
    REASON_EMERGENCY_OVERRIDE,
    REASON_STARVATION_CEILING,
    REASON_VOICE_COMMAND,
    REASON_RL_POLICY,
})

# Which reasons carry a Tier-0-style score_breakdown / alternative_scores.
# The other three are produced by a layer that has no such scoring.
SCORED_REASONS = frozenset({REASON_WAIT_THRESHOLD, REASON_RAW_COUNT})


# --------------------------------------------------------------------------
# Entry
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class DecisionLogEntry:
    """One junction, one decision step. §12.1 schema plus reconciliation."""

    sim_time: float
    junction_id: str
    phase_selected: int          # what ACTUALLY ran (post-override)
    score_breakdown: dict        # {halted_count, wait_time, starvation_bonus}
    alternative_scores: dict     # {"phase_0": .., "phase_1": ..}
    reason: str

    # Reconciliation — non-None only when §10 overrode this junction here.
    proposed: dict | None = None
    override: dict | None = None

    # Context the narrator (§12.2) needs. `lane_slot` is the within-approach
    # index rendered as "Lane N"; it names the most-loaded lane the selected
    # phase serves (or, for an override, the lane that caused it).
    lane_id: str | None = None
    direction: str | None = None
    lane_slot: int | None = None

    # voice_command only.
    transcript: str | None = None
    action_taken: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _triggering_lane(
    snapshot: dict, junction_id: str, served_set, reason: str
) -> str | None:
    """The lane the narrator should name for a non-override decision.

    For `wait_time_threshold` the honest trigger is the longest-waiting
    lane the phase serves (that is the term that flipped the decision);
    otherwise the most-halted. Computable from the snapshot alone, so it
    works for `rl_policy` too.
    """
    lanes = snapshot["junctions"][junction_id]["lanes"]
    cands = [(lid, lanes[lid]) for lid in served_set if lid in lanes]
    if not cands:
        return None
    if reason == REASON_WAIT_THRESHOLD:
        key = lambda kv: (kv[1]["wait_time_max_single_vehicle"],
                          kv[1]["halted_count"], kv[0])
    else:
        key = lambda kv: (kv[1]["halted_count"],
                          kv[1]["wait_time_max_single_vehicle"], kv[0])
    return max(cands, key=key)[0]


# --------------------------------------------------------------------------
# Log
# --------------------------------------------------------------------------
class DecisionLog:
    """In-memory decision recorder. Bounded per episode; `maxlen` is a cap.

    `to_jsonl()` dumps the full log for §15 evaluation later.
    """

    def __init__(self, maxlen: int | None = None):
        self.entries: deque[DecisionLogEntry] = deque(maxlen=maxlen)
        # Highest sim_time recorded so far. Tracked as a scalar rather than
        # read off `entries[-1]`, because a bounded deque evicts.
        self._last_sim_time: float | None = None

    def __len__(self) -> int:
        return len(self.entries)

    # -- monotonicity guard --------------------------------------------------
    def _check_monotonic(self, sim_time: float, caller: str) -> float:
        """A log covers ONE episode; its sim_time may never go backwards.

        `entries_for()` / `latest()` / §12.3's `why()` all rely on the deque
        being ordered by `sim_time` — they slice it by position and take the
        LAST match, never sorting. Appending an out-of-order entry therefore
        does not raise anywhere; it silently makes every subsequent
        at-or-before query answer with the wrong decision, which is the
        failure mode this repo keeps hitting (a run that passes while
        proving nothing).

        The realistic way it happens is an episode boundary: `env.reset()`
        sends sim_time back to ~0, so a log REUSED across a reset would
        interleave two episodes into one non-monotonic sequence. The fix is
        a fresh `DecisionLog` per episode (see `backend/sim_runner.py`'s
        `_reset_counters`); this guard is what makes that lifecycle
        load-bearing instead of a convention.

        Equal timestamps are legal — a decision step records one entry per
        junction, and a §12.2 voice entry may land on the same instant.
        """
        t = float(sim_time)
        last = self._last_sim_time
        if last is not None and t < last - 1e-9:
            raise ValueError(
                f"{caller}: sim_time went BACKWARDS ({t} < {last}). A "
                f"DecisionLog covers one episode and must stay ordered — "
                f"at-or-before queries (§12.3) read it positionally and "
                f"would silently return the wrong decision. If this is an "
                f"episode boundary, replace the log rather than reusing it."
            )
        return t

    def _advance(self, sim_time: float) -> None:
        """Move the watermark. `max`, not assignment, so a step back inside
        the 1e-9 tolerance cannot walk it backwards over many calls."""
        last = self._last_sim_time
        self._last_sim_time = sim_time if last is None else max(last, sim_time)

    # -- recording -----------------------------------------------------------
    def record_step(
        self,
        sim_time: float,
        decisions: dict[str, dict],
        info: dict,
        snapshot: dict,
        served_lanes: dict[str, dict[int, frozenset[str]]],
    ) -> list[DecisionLogEntry]:
        """One `env.step()` worth of decisions -> one entry per junction.

        `decisions` is keyed by junction id, each value carrying
        `phase_selected` / `score_breakdown` / `alternative_scores` /
        `reason` (the `Tier0Controller.act()` shape). `info` is the env's
        step info — `safety_overrides` / `executed_action` are read from
        it. `snapshot` is the state the decision was scored against (the
        pre-step snapshot). `served_lanes` is the static phase->lane map.
        """
        # Both guards run BEFORE anything is appended, so a rejected call
        # leaves the log exactly as it was.
        sim_time = self._check_monotonic(sim_time, "record_step")

        overrides_by_j = {
            record["junction_id"]: record
            for record in info.get("safety_overrides", [])
        }

        # CONTRACT: every junction the controller/policy acted on this step
        # must be a key in `decisions`. An override recorded for a junction
        # that `decisions` does not mention would otherwise be dropped
        # SILENTLY by the loop below (it iterates decisions.items()), and a
        # §15 "how often did the safety gate have to fire" metric computed
        # from the resulting log would read the shield as never firing — the
        # exact §0.3 failure the corrected emergency metric exists to avoid.
        # RL auto mode must pass a per-junction dict with reason="rl_policy"
        # (see this module's docstring), not an empty / partial one.
        unscored = set(overrides_by_j) - set(decisions)
        if unscored:
            raise ValueError(
                f"record_step: §10 override(s) recorded for junction(s) "
                f"{sorted(unscored)} which are absent from `decisions` "
                f"(keys: {sorted(decisions)}). Pass a decision entry for "
                f"every junction acted on this step — for RL auto mode, one "
                f"per junction with reason={REASON_RL_POLICY!r}."
            )

        self._advance(sim_time)

        new: list[DecisionLogEntry] = []
        for junction_id, decision in decisions.items():
            reason = decision["reason"]
            phase_selected = int(decision["phase_selected"])
            proposed = override = None
            lane_id = None

            ovr = overrides_by_j.get(junction_id)
            if ovr is not None:
                proposed = {"phase": phase_selected, "reason": reason}
                override = {
                    k: ovr[k] for k in
                    ("rule", "lane_id", "wait_s", "from_slot", "to_slot", "outcome")
                }
                reason = ovr["rule"]
                # A deferred ceiling did NOT change what ran this step.
                if ovr["outcome"] != OUTCOME_DEFERRED_MIN_GREEN:
                    phase_selected = int(ovr["to_slot"])
                lane_id = ovr["lane_id"]
            else:
                served_set = served_lanes.get(junction_id, {}).get(
                    phase_selected, frozenset()
                )
                lane_id = _triggering_lane(
                    snapshot, junction_id, served_set, reason
                )

            direction = lane_slot = None
            if lane_id is not None:
                reading = snapshot["junctions"][junction_id]["lanes"].get(lane_id)
                if reading is not None:
                    direction = reading["approach"]
                lane_slot = _lane_index(lane_id)

            entry = DecisionLogEntry(
                sim_time=float(sim_time),
                junction_id=junction_id,
                phase_selected=phase_selected,
                score_breakdown=dict(decision.get("score_breakdown", {})),
                alternative_scores=dict(decision.get("alternative_scores", {})),
                reason=reason,
                proposed=proposed,
                override=override,
                lane_id=lane_id,
                direction=direction,
                lane_slot=lane_slot,
                # Carried through for a reason=voice_command row produced
                # via record_step (§13.1 force_phase). Absent for every
                # other producer -> .get() is None -> to_dict() drops it,
                # so this is transparent to existing callers.
                transcript=decision.get("transcript"),
                action_taken=decision.get("action_taken"),
            )
            self.entries.append(entry)
            new.append(entry)
        return new

    def record_voice(
        self, sim_time: float, junction_id: str, transcript: str, action_taken: str
    ) -> DecisionLogEntry:
        """§12.2: voice-triggered actions post to this same log.

        Wired by Phase 9/11; defined here so the schema and reason value
        are fixed now.
        """
        sim_time = self._check_monotonic(sim_time, "record_voice")
        self._advance(sim_time)
        entry = DecisionLogEntry(
            sim_time=float(sim_time),
            junction_id=junction_id,
            phase_selected=-1,
            score_breakdown={},
            alternative_scores={},
            reason=REASON_VOICE_COMMAND,
            transcript=transcript,
            action_taken=action_taken,
        )
        self.entries.append(entry)
        return entry

    # -- reading -----------------------------------------------------------
    def entries_for(
        self, junction_id: str | None = None, upto_sim_time: float | None = None
    ) -> list[DecisionLogEntry]:
        out = list(self.entries)
        if junction_id is not None:
            out = [e for e in out if e.junction_id == junction_id]
        if upto_sim_time is not None:
            out = [e for e in out if e.sim_time <= upto_sim_time + 1e-9]
        return out

    def latest(self, junction_id: str | None = None) -> DecisionLogEntry | None:
        found = self.entries_for(junction_id=junction_id)
        return found[-1] if found else None

    def to_jsonl(self, path: str | Path) -> Path:
        path = Path(path)
        with path.open("w", encoding="utf-8") as fh:
            for entry in self.entries:
                fh.write(json.dumps(entry.to_dict(), sort_keys=True) + "\n")
        return path


# --------------------------------------------------------------------------
# Self-test — no SUMO process
# --------------------------------------------------------------------------
def _selftest() -> None:
    print("§12.1 decision_log self-test\n")

    # Reason enum is complete and correctly spelled.
    assert len(REASONS) == 6, REASONS
    assert REASON_RL_POLICY == "rl_policy"
    print(f"  [OK] 6 reasons, rl_policy == {REASON_RL_POLICY!r}")

    # Minimal §7.6-shaped snapshot: J1 with a north lane and an east lane.
    snapshot = {
        "junctions": {
            "J1": {"lanes": {
                "N1_J1_0": {"approach": "north", "halted_count": 6,
                            "wait_time_max_single_vehicle": 30.0},
                "N1_J1_1": {"approach": "north", "halted_count": 2,
                            "wait_time_max_single_vehicle": 95.0},
                "E1_J1_0": {"approach": "east", "halted_count": 1,
                            "wait_time_max_single_vehicle": 5.0},
            }},
        },
    }
    served = {"J1": {0: frozenset({"N1_J1_0", "N1_J1_1"}), 1: frozenset({"E1_J1_0"})}}

    # -- 1: ordinary raw_count decision, no override --------------------
    decisions = {"J1": {
        "junction_id": "J1", "phase_selected": 0,
        "score_breakdown": {"halted_count": 4.8, "wait_time": 12.0,
                            "starvation_bonus": 0.0},
        "alternative_scores": {"phase_0": 16.8, "phase_1": 0.6},
        "reason": REASON_RAW_COUNT,
    }}
    log = DecisionLog()
    (e,) = log.record_step(1840.0, decisions, {"safety_overrides": []},
                           snapshot, served)
    assert e.reason == REASON_RAW_COUNT and e.proposed is None and e.override is None
    # raw_count -> names the most-halted served lane (N1_J1_0, halted 6).
    assert e.lane_id == "N1_J1_0" and e.direction == "north" and e.lane_slot == 0
    print(f"  [OK] raw_count entry: lane={e.lane_id} slot={e.lane_slot} "
          f"dir={e.direction}")

    # -- 2: same decision but a wait_time_threshold reason -------------
    decisions["J1"]["reason"] = REASON_WAIT_THRESHOLD
    log = DecisionLog()
    (e,) = log.record_step(1845.0, decisions, {"safety_overrides": []},
                           snapshot, served)
    # wait_time_threshold -> names the longest-waiting served lane (N1_J1_1, 95s).
    assert e.lane_id == "N1_J1_1" and e.lane_slot == 1, (e.lane_id, e.lane_slot)
    print(f"  [OK] wait_time_threshold entry: lane={e.lane_id} slot={e.lane_slot}")

    # -- 3: §10 emergency override reconciliation ---------------------
    info = {"safety_overrides": [{
        "junction_id": "J1", "rule": REASON_EMERGENCY_OVERRIDE,
        "from_slot": 0, "to_slot": 1, "lane_id": "E1_J1_0",
        "wait_s": 5.0, "outcome": "applied",
    }]}
    decisions["J1"]["reason"] = REASON_RAW_COUNT
    decisions["J1"]["phase_selected"] = 0
    log = DecisionLog()
    (e,) = log.record_step(1850.0, decisions, info, snapshot, served)
    assert e.reason == REASON_EMERGENCY_OVERRIDE
    assert e.phase_selected == 1, e.phase_selected           # executed, not proposed
    assert e.proposed == {"phase": 0, "reason": REASON_RAW_COUNT}
    assert e.override["rule"] == REASON_EMERGENCY_OVERRIDE
    assert e.lane_id == "E1_J1_0" and e.lane_slot == 0
    print(f"  [OK] emergency override: proposed phase {e.proposed['phase']} "
          f"-> executed phase {e.phase_selected}, reason now {e.reason!r}")

    # -- 4: a DEFERRED ceiling does not change what ran ---------------
    info = {"safety_overrides": [{
        "junction_id": "J1", "rule": REASON_STARVATION_CEILING,
        "from_slot": 1, "to_slot": 0, "lane_id": "N1_J1_1",
        "wait_s": 130.0, "outcome": OUTCOME_DEFERRED_MIN_GREEN,
    }]}
    decisions["J1"]["phase_selected"] = 1
    log = DecisionLog()
    (e,) = log.record_step(1855.0, decisions, info, snapshot, served)
    assert e.reason == REASON_STARVATION_CEILING
    assert e.phase_selected == 1, e.phase_selected           # deferred -> unchanged
    assert e.override["outcome"] == OUTCOME_DEFERRED_MIN_GREEN
    print(f"  [OK] deferred ceiling: executed phase stays {e.phase_selected} "
          f"(outcome={e.override['outcome']})")

    # -- 5: voice entry + jsonl round-trip --------------------------
    log = DecisionLog()
    log.record_step(1860.0, decisions, {"safety_overrides": []}, snapshot, served)
    log.record_voice(1861.0, "J2", "switch to manual mode", "set_mode(manual)")
    assert len(log) == 2
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "log.jsonl"
    log.to_jsonl(tmp)
    lines = tmp.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    back = [json.loads(ln) for ln in lines]
    assert back[1]["reason"] == REASON_VOICE_COMMAND
    assert back[1]["transcript"] == "switch to manual mode"
    assert "proposed" not in back[0]   # to_dict() drops None fields
    print(f"  [OK] jsonl round-trip: {len(lines)} lines, voice entry intact")

    # -- 5b: a voice_command row via record_step (§13.1 force_phase) carries
    #        transcript / action_taken through; other producers are untouched.
    fp = {"J1": {"junction_id": "J1", "phase_selected": 1, "score_breakdown": {},
                 "alternative_scores": {}, "reason": REASON_VOICE_COMMAND,
                 "transcript": "force phase 1 at J1",
                 "action_taken": "force_phase(J1, 1)"}}
    log = DecisionLog()
    (e,) = log.record_step(1870.0, fp, {"safety_overrides": []}, snapshot, served)
    assert e.reason == REASON_VOICE_COMMAND
    assert e.transcript == "force phase 1 at J1" and e.action_taken == "force_phase(J1, 1)"
    (e2,) = DecisionLog().record_step(1871.0, decisions, {"safety_overrides": []},
                                     snapshot, served)
    assert e2.transcript is None and e2.action_taken is None   # non-voice: unchanged
    print(f"  [OK] force_phase voice_command row keeps transcript/action_taken; "
          f"other rows keep None")

    # -- 6: query helpers -----------------------------------------------
    log = DecisionLog()
    for t in (10.0, 20.0, 30.0):
        for jid in ("J1", "J2"):
            d = {jid: dict(decisions["J1"], junction_id=jid)}
            log.record_step(t, d, {"safety_overrides": []},
                            {"junctions": {jid: {"lanes": {}}}}, {jid: {}})
    assert len(log.entries_for(junction_id="J2")) == 3
    assert len(log.entries_for(upto_sim_time=20.0)) == 4
    assert log.latest("J1").sim_time == 30.0
    print(f"  [OK] entries_for / latest filters")

    # -- 7: auto-mode contract (Finding 1) ----------------------------
    # An override at a junction the caller scored only as a bare rl_policy
    # row must still be reconciled; an override at a junction ABSENT from
    # `decisions` (the pre-fix backend behaviour, decisions == {}) must RAISE.
    amb_snap = {"junctions": {
        jid: {"lanes": {f"L_{jid}_0": {"approach": "north", "halted_count": 2,
                                       "wait_time_max_single_vehicle": 8.0}}}
        for jid in ("J1", "J2", "J3")
    }}
    amb_served = {jid: {0: frozenset({f"L_{jid}_0"}), 1: frozenset()}
                  for jid in ("J1", "J2", "J3")}
    # Exact shape backend/sim_runner._pick_action now emits in auto mode.
    rl_decisions = {
        jid: {"junction_id": jid, "phase_selected": 0, "score_breakdown": {},
              "alternative_scores": {}, "reason": REASON_RL_POLICY}
        for jid in ("J1", "J2", "J3")
    }
    rl_info = {"safety_overrides": [{
        "junction_id": "J2", "rule": REASON_EMERGENCY_OVERRIDE,
        "from_slot": 0, "to_slot": 1, "lane_id": "L_J2_0",
        "wait_s": 3.0, "outcome": "applied",
    }]}
    made = DecisionLog().record_step(2000.0, rl_decisions, rl_info, amb_snap, amb_served)
    assert len(made) == 3
    j2 = next(e for e in made if e.junction_id == "J2")
    assert j2.reason == REASON_EMERGENCY_OVERRIDE
    assert j2.proposed == {"phase": 0, "reason": REASON_RL_POLICY}
    assert j2.override is not None and j2.override["lane_id"] == "L_J2_0"
    assert j2.phase_selected == 1                       # executed, post-override
    print(f"  [OK] auto-mode override reconciled: J2 {REASON_RL_POLICY} -> "
          f"{j2.reason} (proposed phase {j2.proposed['phase']}, executed "
          f"{j2.phase_selected})")

    for bad in ({}, {"J1": rl_decisions["J1"], "J3": rl_decisions["J3"]}):
        try:
            DecisionLog().record_step(2005.0, bad, rl_info, amb_snap, amb_served)
        except ValueError as exc:
            assert "J2" in str(exc), exc
        else:
            raise AssertionError(
                f"record_step must raise when an override's junction (J2) is "
                f"absent from decisions={sorted(bad)}"
            )
    print(f"  [OK] override at an unscored junction raises ValueError "
          f"(decisions={{}} and partial dict both)")

    # -- 8: sim_time monotonicity guard --------------------------------
    # A log covers ONE episode. Out-of-order entries do not raise anywhere
    # downstream — entries_for()/latest()/why() read the deque positionally
    # — so without this guard a log reused across env.reset() would silently
    # answer at-or-before queries with the previous episode's decision.
    clean = {"safety_overrides": []}
    log = DecisionLog()
    log.record_step(100.0, decisions, clean, snapshot, served)
    log.record_step(100.0, decisions, clean, snapshot, served)   # equal: legal
    log.record_voice(100.0, "J1", "same instant", "noop")        # equal: legal
    log.record_step(200.0, decisions, clean, snapshot, served)
    assert len(log) == 4 and log._last_sim_time == 200.0
    print(f"  [OK] equal and increasing sim_time accepted "
          f"({len(log)} entries, last t={log._last_sim_time:.0f})")

    for label, call in (
        ("record_step", lambda: log.record_step(150.0, decisions, clean,
                                                snapshot, served)),
        ("record_voice", lambda: log.record_voice(150.0, "J1", "late", "noop")),
    ):
        try:
            call()
        except ValueError as exc:
            assert "BACKWARDS" in str(exc) and "150.0" in str(exc) \
                and "200.0" in str(exc), exc
            print(f"  [OK] {label}(t=150) after t=200 raises: {exc}")
        else:
            raise AssertionError(f"{label} must reject a backwards sim_time")

    # The rejected calls appended nothing and did not move the watermark.
    assert len(log) == 4 and log._last_sim_time == 200.0
    # A tolerance-width step back is still accepted (float round-trip noise),
    # and does NOT walk the watermark backwards — `_advance` takes a max.
    log.record_step(200.0 - 1e-12, decisions, clean, snapshot, served)
    assert len(log) == 5 and log._last_sim_time == 200.0, log._last_sim_time
    # And the episode-boundary fix: a FRESH log restarts at t~0 happily.
    fresh = DecisionLog()
    fresh.record_step(0.0, decisions, clean, snapshot, served)
    assert len(fresh) == 1 and fresh._last_sim_time == 0.0
    print(f"  [OK] rejected calls left the log untouched (4 entries, "
          f"watermark 200.0); a FRESH log accepts t=0.0 — the per-episode "
          f"lifecycle is what the guard makes load-bearing")

    print(f"\nAll decision_log self-tests passed.")


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _selftest()

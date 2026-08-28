"""Plain-language narration (§12.2).

Template-based, NOT a per-step LLM call — §12.2 is explicit that an
LLM-per-cycle is too slow for a live decision log, and §2 locks "no
Claude API / no LLM anywhere in the runtime path" regardless.

Pure function: `narrate(entry) -> str`. No state, no I/O.

§12.2 gives four templates verbatim. Two confirmed deviations for this
build (design plan, Phase 8):

  1. A fifth template for `starvation_ceiling` — §12.2 lists only four
     reasons but §10's validator has two rules.
  2. Every template is prefixed with the junction id ("J2 · ..."). The
     §12.2 templates predate §0.1's 3-junction corridor lock, where an
     operator has to know WHICH junction a line is about.

And a sixth, `rl_policy`, added for Phase 9's RL auto mode, which has no
Tier-0-style rule-based justification to render. Its line names the busiest
served lane as CONTEXT, not as the stated cause — the trained policy's
actual reason is opaque, and phrasing it "Lane N — selected" would attribute
a rationale the system cannot attest.

`{lane}` is `entry.lane_slot` — the within-approach index of the lane
the decision turned on (see decision_log._triggering_lane). `{direction}`
is that lane's compass approach, capitalised to match §12.2's "North".
"""

from __future__ import annotations

from explainability.decision_log import (
    REASON_EMERGENCY_OVERRIDE,
    REASON_RAW_COUNT,
    REASON_RL_POLICY,
    REASON_STARVATION_CEILING,
    REASON_VOICE_COMMAND,
    REASON_WAIT_THRESHOLD,
    REASONS,
    DecisionLogEntry,
)


def _lane(entry: DecisionLogEntry) -> str:
    return str(entry.lane_slot) if entry.lane_slot is not None else "?"


def _direction(entry: DecisionLogEntry) -> str:
    return (entry.direction or "unknown").capitalize()


def narrate(entry: DecisionLogEntry) -> str:
    """Render one §12.1 decision-log entry to a single operator-facing line."""
    reason = entry.reason
    j = entry.junction_id

    if reason == REASON_WAIT_THRESHOLD:
        return (f"{j} · Lane {_lane(entry)}, {_direction(entry)} — selected. "
                f"Wait threshold crossed.")

    if reason == REASON_RAW_COUNT:
        return (f"{j} · Lane {_lane(entry)}, {_direction(entry)} — selected. "
                f"Highest vehicle count.")

    if reason == REASON_EMERGENCY_OVERRIDE:
        return (f"{j} · Emergency override — {_direction(entry)} cleared "
                f"for ambulance.")

    if reason == REASON_STARVATION_CEILING:
        wait_s = entry.override["wait_s"] if entry.override else 0.0
        return (f"{j} · Starvation ceiling — Lane {_lane(entry)}, "
                f"{_direction(entry)} forced green after {wait_s:.0f}s wait.")

    if reason == REASON_VOICE_COMMAND:
        return (f"Voice command received: '{entry.transcript}' "
                f"→ {entry.action_taken}.")

    if reason == REASON_RL_POLICY:
        # The trained policy's actual reason is opaque; the lane is CONTEXT
        # (the busiest lane the selected phase serves), not the stated cause.
        return (f"{j} · Learned policy selected phase {entry.phase_selected} "
                f"(busiest served lane: {_lane(entry)}, {_direction(entry)}).")

    raise KeyError(f"no narration template for reason {reason!r}")


# --------------------------------------------------------------------------
# Self-test — no SUMO process
# --------------------------------------------------------------------------
def _entry(reason: str, **kw) -> DecisionLogEntry:
    base = dict(
        sim_time=1840.0, junction_id="J2", phase_selected=2,
        score_breakdown={}, alternative_scores={}, reason=reason,
        lane_id="N2_J2_1", direction="north", lane_slot=1,
    )
    base.update(kw)
    return DecisionLogEntry(**base)


def _selftest() -> None:
    print("§12.2 narrator self-test\n")

    cases = {
        REASON_WAIT_THRESHOLD: _entry(REASON_WAIT_THRESHOLD),
        REASON_RAW_COUNT: _entry(REASON_RAW_COUNT),
        REASON_EMERGENCY_OVERRIDE: _entry(REASON_EMERGENCY_OVERRIDE),
        REASON_STARVATION_CEILING: _entry(
            REASON_STARVATION_CEILING,
            override={"rule": REASON_STARVATION_CEILING, "lane_id": "N2_J2_1",
                      "wait_s": 131.0, "from_slot": 1, "to_slot": 0,
                      "outcome": "applied"},
        ),
        REASON_VOICE_COMMAND: _entry(
            REASON_VOICE_COMMAND, junction_id="J1", phase_selected=-1,
            transcript="give lane 3 more priority", action_taken="set_lane_bias(3, high, 300s)",
        ),
        REASON_RL_POLICY: _entry(REASON_RL_POLICY),
    }

    # Every reason in the enum has a working template.
    assert set(cases) == REASONS, set(cases) ^ REASONS
    for reason, entry in cases.items():
        line = narrate(entry)
        assert isinstance(line, str) and line.strip(), (reason, repr(line))
        print(f"  [{reason:>20}]  {line}")

    # Fragments the templates must contain.
    assert "Wait threshold crossed" in narrate(cases[REASON_WAIT_THRESHOLD])
    assert "Highest vehicle count" in narrate(cases[REASON_RAW_COUNT])
    assert "Emergency override" in narrate(cases[REASON_EMERGENCY_OVERRIDE])
    assert "131s wait" in narrate(cases[REASON_STARVATION_CEILING])
    assert "give lane 3 more priority" in narrate(cases[REASON_VOICE_COMMAND])
    _rl = narrate(cases[REASON_RL_POLICY])
    assert "Learned policy selected phase 2" in _rl
    assert "busiest served lane" in _rl   # lane framed as context, not cause
    assert narrate(cases[REASON_WAIT_THRESHOLD]).startswith("J2 · ")

    # An unknown reason must raise, not return a canned string (§12.3).
    try:
        narrate(_entry("not_a_reason"))
    except KeyError as exc:
        print(f"\n  [OK] unknown reason raises: {exc}")
    else:
        raise AssertionError("unknown reason must raise KeyError")

    print(f"\nAll narrator self-tests passed ({len(REASONS)} templates).")


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # narration is UTF-8
    _selftest()
